"""Prioritize translation resources for supported Sphinx documentation projects."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable, Iterable, Sequence

from .document_score import (
    catalog_scores,
    score_cpython_documents,
    score_sphinx_documents,
)

if TYPE_CHECKING:
    from potodo.po_file import PoDirectories, PoDirectory, PoFileStats

UrlBuilder = Callable[[PurePosixPath, str], tuple[str, ...]]
CORE_RESOURCE_BOOST = 25.0


def is_core_resource(project: Project, resource: PurePosixPath) -> bool:
    """Identify CPython's core translation resources by catalog path."""
    return project.name == "cpython" and (
        resource.as_posix() in {"bugs.po", "builtins/functions.po"}
        or resource.parts[0] == "tutorial"
    )


@dataclass(frozen=True)
class MetricWeights:
    """Relative contribution of each available priority metric."""

    completion: float = 1
    navigation: float = 1
    original_popularity: float = 1
    translated_popularity: float = 1


@dataclass(frozen=True)
class Project:
    """Source, gettext, and analytics conventions for a documentation project."""

    name: str
    stats_site: str | None
    original_stats_prefix: str | None
    localized_layout: bool
    compact_catalog: str | None = None
    weights: MetricWeights = MetricWeights()


PROJECTS = {
    "cpython": Project("cpython", "docs.python.org", "3", False),
    "sphinx": Project(
        "sphinx", None, None, True, weights=MetricWeights(navigation=100)
    ),
    "packaging": Project("packaging", "packaging.python.org", "en", True, "messages"),
}


@dataclass(frozen=True)
class DocumentScore:
    """Navigation and source-document metadata for one gettext catalog."""

    documents: tuple[PurePosixPath, ...]
    score: tuple[int, ...]


@dataclass(frozen=True)
class PriorityRow:  # pylint: disable=too-many-instance-attributes
    """One rendered resource with its combined and raw metric values."""

    resource: str
    path: str
    priority: float
    completion: float
    original_visitors: int | None
    translated_visitors: int | None
    document_score: tuple[int, ...]
    core_boost: float = 0


@dataclass(frozen=True)
class _Candidate:
    resource: PurePosixPath
    path: Path
    completion: float
    original_visitors: int | None
    translated_visitors: int | None
    document_score: tuple[int, ...]


def normalize_language(language: str) -> str:
    """Convert a gettext locale name to a public URL/stats prefix."""
    return language.replace("_", "-").lower()


def resolve_source_root(project: Project, source: Path) -> Path:
    """Resolve the Sphinx source directory accepted by a project preset."""
    source = source.resolve()
    if project.name == "cpython" and source.name != "Doc":
        source /= "Doc"
    root_marker = (
        source / "tools" / "templates" / "indexcontent.html"
        if project.name == "cpython"
        else source / "index.rst"
    )
    if not root_marker.is_file():
        raise FileNotFoundError(f"documentation source root not found: {source}")
    return source


def resolve_translation_paths(
    project: Project, paths: Sequence[Path], language: str
) -> list[Path]:
    """Resolve locale repositories to the directories containing PO files."""
    resolved = []
    for path in paths:
        path = path.resolve()
        localized = path / language / "LC_MESSAGES"
        resolved.append(
            localized if project.localized_layout and localized.is_dir() else path
        )
    return resolved


def load_document_scores(path: Path) -> dict[PurePosixPath, DocumentScore]:
    """Load compatible precomputed document-score JSON."""
    records = json.loads(path.read_text(encoding="utf-8"))
    scores = {}
    for record in records:
        raw_documents = record.get("documents")
        if raw_documents is None:
            raw_document = record.get("document")
            raw_documents = [] if raw_document is None else [raw_document]
        scores[PurePosixPath(record["pot"])] = DocumentScore(
            documents=tuple(PurePosixPath(item) for item in raw_documents),
            score=tuple(record["score"]),
        )
    return scores


def calculate_document_scores(
    project: Project, source: Path
) -> dict[PurePosixPath, DocumentScore]:
    """Calculate catalog scores directly from a documentation source tree."""
    doc_root = resolve_source_root(project, source)
    if project.name == "cpython":
        documents = score_cpython_documents(doc_root)
    else:
        documents = score_sphinx_documents(doc_root)
    catalogs = catalog_scores(
        documents,
        doc_root,
        compact_catalog=project.compact_catalog,
        shared_template=project.name in {"cpython", "sphinx"},
        shared_template_score=(0,) if project.name == "cpython" else None,
    )
    scores = {
        catalog: DocumentScore(
            documents=tuple(
                PurePosixPath(document.relative_to(doc_root).as_posix())
                for document in source_documents
            ),
            score=score,
        )
        for catalog, (score, source_documents) in catalogs.items()
    }
    return apply_project_catalog_semantics(project, scores)


def apply_project_catalog_semantics(
    project: Project, scores: dict[PurePosixPath, DocumentScore]
) -> dict[PurePosixPath, DocumentScore]:
    """Apply project meanings that cannot be inferred from source filenames."""
    if project.name != "cpython" or PurePosixPath("sphinx.pot") not in scores:
        return scores
    scores = scores.copy()
    scores[PurePosixPath("sphinx.pot")] = DocumentScore(
        documents=(PurePosixPath("index.rst"),),
        score=(0,),
    )
    return scores


def stats_directory(path: Path) -> Path:
    """Accept either plausible-stats itself or its stats subdirectory."""
    path = path.resolve()
    nested = path / "stats"
    return nested if nested.is_dir() else path


def load_page_visitors(
    stats_dir: Path, site: str, prefix: str, snapshots: int
) -> dict[str, int]:
    """Sum visitors by page over the latest Plausible snapshots."""
    pattern = f"{site}_*/*.prefix-{prefix}.pages.json"
    paths = sorted(stats_directory(stats_dir).glob(pattern))[-snapshots:]
    if not paths:
        raise FileNotFoundError(
            f"no Plausible page snapshots matching {pattern!r} in {stats_dir}"
        )
    visitors: defaultdict[str, int] = defaultdict(int)
    for path in paths:
        records = json.loads(path.read_text(encoding="utf-8"))
        for record in records:
            visitors[record["name"]] += int(record["visitors"])
    return dict(visitors)


def cpython_document_urls(document: PurePosixPath, prefix: str) -> tuple[str, ...]:
    """Return public docs.python.org paths for a source document."""
    stem = document.as_posix().removesuffix(".rst")
    if stem == "index":
        return (f"/{prefix}/", f"/{prefix}/index.html")
    if stem.endswith("/index"):
        directory = stem.removesuffix("index")
        return (f"/{prefix}/{directory}", f"/{prefix}/{stem}.html")
    return (f"/{prefix}/{stem}.html",)


def packaging_document_urls(document: PurePosixPath, prefix: str) -> tuple[str, ...]:
    """Return public packaging.python.org dirhtml paths for a source document."""
    stem = document.as_posix().removesuffix(".rst")
    if stem == "index":
        suffix = ""
    elif stem.endswith("/index"):
        suffix = stem.removesuffix("index")
    else:
        suffix = f"{stem}/"
    return (f"/{prefix}/latest/{suffix}",)


def project_urls(project: Project) -> UrlBuilder:
    if project.name == "cpython":
        return cpython_document_urls
    if project.name == "packaging":
        return packaging_document_urls
    raise ValueError(f"{project.name} does not have popularity data")


def _visitor_count(
    visitors: dict[str, int] | None,
    documents: Iterable[PurePosixPath],
    urls: UrlBuilder,
    prefix: str,
) -> int | None:
    if visitors is None:
        return None
    return sum(
        visitors.get(url, 0) for document in documents for url in urls(document, prefix)
    )


def normalized_ranks(values: Sequence[Any], *, higher_is_better: bool) -> list[float]:
    """Return percentile ranks from worst (0) to best (1), averaging ties."""
    if len(values) < 2:
        return [0.5] * len(values)
    ordered = sorted(values, reverse=not higher_is_better)
    ranks = {}
    start = 0
    while start < len(ordered):
        value = ordered[start]
        end = start + 1
        while end < len(ordered) and ordered[end] == value:
            end += 1
        ranks[value] = (start + end - 1) / (2 * (len(ordered) - 1))
        start = end
    return [ranks[value] for value in values]


def _completion(po_file: PoFileStats) -> float:
    try:
        return 100 * po_file.translated_words / po_file.words
    except ZeroDivisionError:
        return 0


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _candidate_visitors(
    project: Project,
    documents: tuple[PurePosixPath, ...],
    original_visitors: dict[str, int] | None,
    translated_visitors: dict[str, int] | None,
    language: str,
    docs_version: str,
) -> tuple[int | None, int | None]:
    if not project.stats_site:
        return None, None
    urls = project_urls(project)
    original_prefix = (
        docs_version if project.name == "cpython" else project.original_stats_prefix
    )
    assert original_prefix is not None
    translated_prefix = (
        f"{normalize_language(language)}/{docs_version}"
        if project.name == "cpython"
        else normalize_language(language)
    )
    return (
        _visitor_count(original_visitors, documents, urls, original_prefix),
        _visitor_count(translated_visitors, documents, urls, translated_prefix),
    )


def _collect_candidates(
    po_directories: PoDirectories,
    document_scores: dict[PurePosixPath, DocumentScore],
    original_visitors: dict[str, int] | None,
    translated_visitors: dict[str, int] | None,
    *,
    project: Project,
    language: str,
    docs_version: str,
    show_finished: bool,
) -> list[_Candidate]:
    candidates = []
    unmatched = []
    for po_directory in po_directories:
        for po_file in po_directory.files:
            if not show_finished and _completion(po_file) == 100:
                continue
            resource = PurePosixPath(
                po_file.path.relative_to(po_directory.path).as_posix()
            )
            try:
                document_score = document_scores[resource.with_suffix(".pot")]
            except KeyError:
                unmatched.append(resource.as_posix())
                continue
            visitor_counts = _candidate_visitors(
                project,
                document_score.documents,
                original_visitors,
                translated_visitors,
                language,
                docs_version,
            )
            candidates.append(
                _Candidate(
                    resource=resource,
                    path=po_file.path,
                    completion=_completion(po_file),
                    original_visitors=visitor_counts[0],
                    translated_visitors=visitor_counts[1],
                    document_score=document_score.score,
                )
            )
    if unmatched:
        print(
            "warning: no document score for " + ", ".join(sorted(unmatched)),
            file=sys.stderr,
        )
    return candidates


def build_priority_rows(
    po_directories: PoDirectories,
    document_scores: dict[PurePosixPath, DocumentScore],
    original_visitors: dict[str, int] | None,
    translated_visitors: dict[str, int] | None,
    *,
    project: Project,
    language: str,
    docs_version: str,
    show_finished: bool,
) -> list[PriorityRow]:
    """Join PO resources to available metrics and return priority order."""
    candidates = _collect_candidates(
        po_directories,
        document_scores,
        original_visitors,
        translated_visitors,
        project=project,
        language=language,
        docs_version=docs_version,
        show_finished=show_finished,
    )
    if not candidates:
        return []
    weighted_metric_ranks = [
        (
            normalized_ranks(
                [candidate.completion for candidate in candidates],
                higher_is_better=True,
            ),
            project.weights.completion,
        ),
        (
            normalized_ranks(
                [candidate.document_score for candidate in candidates],
                higher_is_better=False,
            ),
            project.weights.navigation,
        ),
    ]
    if candidates[0].original_visitors is not None:
        weighted_metric_ranks.append(
            (
                normalized_ranks(
                    [candidate.original_visitors for candidate in candidates],
                    higher_is_better=True,
                ),
                project.weights.original_popularity,
            )
        )
    if candidates[0].translated_visitors is not None:
        weighted_metric_ranks.append(
            (
                normalized_ranks(
                    [candidate.translated_visitors for candidate in candidates],
                    higher_is_better=True,
                ),
                project.weights.translated_popularity,
            )
        )

    rows = []
    for index, candidate in enumerate(candidates):
        core_boost = (
            CORE_RESOURCE_BOOST if is_core_resource(project, candidate.resource) else 0
        )
        weighted_ranks = [
            (ranks[index], weight) for ranks, weight in weighted_metric_ranks
        ]
        rows.append(
            PriorityRow(
                resource=candidate.resource.as_posix(),
                path=str(candidate.path),
                priority=100
                * sum(rank * weight for rank, weight in weighted_ranks)
                / sum(weight for _, weight in weighted_ranks)
                + core_boost,
                completion=candidate.completion,
                original_visitors=candidate.original_visitors,
                translated_visitors=candidate.translated_visitors,
                document_score=candidate.document_score,
                core_boost=core_boost,
            )
        )
    return sorted(rows, key=lambda row: (-row.priority, row.resource))


def _format_visitors(value: int | None, width: int) -> str:
    return f"{value:{width}d}" if value is not None else f"{'-':>{width}}"


class PriorityReport:
    """Documentation translation priority report."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        group = parser.add_argument_group("priority data sources")
        group.add_argument(
            "--project", choices=PROJECTS, required=True, help="project conventions"
        )
        scores = group.add_mutually_exclusive_group(required=True)
        scores.add_argument(
            "--source",
            type=Path,
            help="project root (CPython) or Sphinx source directory",
        )
        scores.add_argument(
            "--document-scores",
            type=Path,
            help="path to compatible precomputed document-score JSON",
        )
        group.add_argument(
            "--plausible-stats",
            type=Path,
            help="path to plausible-stats or its stats directory",
        )
        group.add_argument("--language", required=True, help="gettext locale name")
        group.add_argument(
            "--stats-snapshots",
            type=positive_int,
            default=30,
            metavar="N",
            help="latest daily snapshots to aggregate (default: 30)",
        )
        group.add_argument(
            "--docs-version",
            default="3",
            metavar="VERSION",
            help="docs.python.org version segment (CPython only; default: 3)",
        )

    def render(self, po_directories: PoDirectories, args: argparse.Namespace) -> None:
        project = PROJECTS[args.project]
        if args.document_scores:
            document_scores = apply_project_catalog_semantics(
                project, load_document_scores(args.document_scores)
            )
        else:
            document_scores = calculate_document_scores(project, args.source)
        original_visitors = None
        translated_visitors = None
        if project.stats_site:
            original_prefix = (
                args.docs_version
                if project.name == "cpython"
                else project.original_stats_prefix
            )
            assert original_prefix is not None
            original_visitors = load_page_visitors(
                args.plausible_stats,
                project.stats_site,
                original_prefix,
                args.stats_snapshots,
            )
            translated_stats_prefix = normalize_language(args.language)
            try:
                translated_visitors = load_page_visitors(
                    args.plausible_stats,
                    project.stats_site,
                    translated_stats_prefix,
                    args.stats_snapshots,
                )
            except FileNotFoundError as error:
                print(
                    f"warning: {error}; omitting translated popularity", file=sys.stderr
                )

        rows = build_priority_rows(
            po_directories,
            document_scores,
            original_visitors,
            translated_visitors,
            project=project,
            language=args.language,
            docs_version=args.docs_version,
            show_finished=args.show_finished,
        )
        if args.json_format:
            print(json.dumps([asdict(row) for row in rows], indent=4))
            return
        if args.matching_files:
            for row in rows:
                print(row.path)
            return
        print(
            f"{'priority':>8}  {'completion':>10}  {'original':>8}  "
            f"{'translated':>10}  {'distance':>10}  resource"
        )
        for row in rows:
            distance = ".".join(map(str, row.document_score))
            print(
                f"{row.priority:8.2f}  {row.completion:9.2f}%  "
                f"{_format_visitors(row.original_visitors, 8)}  "
                f"{_format_visitors(row.translated_visitors, 10)}  "
                f"{distance:>10}  {row.resource}"
            )
