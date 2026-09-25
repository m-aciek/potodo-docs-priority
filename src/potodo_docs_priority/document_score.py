"""Score Sphinx documents by their distance from the documentation index.

CPython starts at ``contents.rst``; other Sphinx projects use ``index.rst``.
Descendants come from ``toctree`` and ``include`` directives.
"""

from __future__ import annotations

import heapq
import re
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

TOCTREE_RE = re.compile(r"^(?P<indent>[ \t]*)\.\.\s+toctree::\s*$")
INCLUDE_RE = re.compile(
    r"^[ \t]*\.\.\s+include::\s+(?P<target>\S.*?)\s*$", flags=re.MULTILINE
)
EXPLICIT_TITLE_RE = re.compile(r"^.*\s+<(?P<target>[^<>]+)>$")
EXCLUDED_SOURCE_DIRECTORIES = {
    ".tx",
    ".venv",
    "_build",
    "build",
    "dist",
    "env",
    "includes",
    "locales",
    "venv",
}
SHARED_TEMPLATE_CATALOG = PurePosixPath("sphinx.pot")


def _directive_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def iter_toctree_targets(document: Path) -> Iterator[tuple[int, str]]:
    """Yield one-based positions and targets from all toctrees in a document."""
    lines = document.read_text(encoding="utf-8").splitlines()
    position = 0
    line_number = 0
    while line_number < len(lines):
        match = TOCTREE_RE.match(lines[line_number])
        line_number += 1
        if match is None:
            continue
        directive_indent = len(match.group("indent"))
        while line_number < len(lines):
            line = lines[line_number]
            if line and _directive_indent(line) <= directive_indent:
                break
            line_number += 1
            entry = line.strip()
            if (
                not entry
                or entry.startswith(":")
                or entry == ".."
                or entry.startswith(".. ")
            ):
                continue
            position += 1
            explicit_title = EXPLICIT_TITLE_RE.match(entry)
            if explicit_title is not None:
                entry = explicit_title.group("target").strip()
            yield position, entry


def iter_document_targets(document: Path) -> Iterator[tuple[int, str]]:
    """Yield navigable children followed by included RST fragments."""
    position = 0
    for position, target in iter_toctree_targets(document):
        yield position, target
    for match in INCLUDE_RE.finditer(document.read_text(encoding="utf-8")):
        position += 1
        yield position, match.group("target")


def is_scored_source(document: Path, doc_root: Path) -> bool:
    try:
        relative = document.relative_to(doc_root)
    except ValueError:
        return False
    if relative == Path("README.rst"):
        return False
    first_part = relative.parts[0]
    return first_part not in EXCLUDED_SOURCE_DIRECTORIES and not first_part.startswith(
        "LANGUAGE="
    )


def iter_scored_sources(doc_root: Path) -> Iterator[Path]:
    for document in doc_root.rglob("*.rst"):
        if is_scored_source(document, doc_root):
            yield document


def resolve_document(target: str, *, parent: Path, doc_root: Path) -> Path | None:
    target = target.partition("#")[0]
    if not target or target == "self" or "://" in target:
        return None
    relative_target = PurePosixPath(target)
    if relative_target.is_absolute():
        candidate = doc_root.joinpath(*relative_target.parts[1:])
    else:
        candidate = parent.joinpath(*relative_target.parts)
    if not str(candidate).endswith(".rst"):
        candidate = Path(f"{candidate}.rst")
    try:
        candidate = candidate.resolve()
        candidate.relative_to(doc_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _score_from_roots(
    doc_root: Path, roots: Iterator[tuple[tuple[int, ...], Path]]
) -> dict[Path, tuple[int, ...]]:
    pending = list(roots)
    heapq.heapify(pending)
    scores: dict[Path, tuple[int, ...]] = {}
    while pending:
        score, document = heapq.heappop(pending)
        if document in scores:
            continue
        scores[document] = score
        for position, target in iter_document_targets(document):
            child = resolve_document(target, parent=document.parent, doc_root=doc_root)
            if (
                child is not None
                and child not in scores
                and is_scored_source(child, doc_root)
            ):
                heapq.heappush(pending, ((score[0] + 1, *score[1:], position), child))

    fallback_depth = max((score[0] for score in scores.values()), default=0) + 1
    unlinked = sorted(
        set(iter_scored_sources(doc_root)) - scores.keys(),
        key=lambda path: path.relative_to(doc_root).as_posix(),
    )
    for position, document in enumerate(unlinked, 1):
        scores[document] = (fallback_depth, position)
    return scores


def score_sphinx_documents(
    doc_root: Path, *, root_document: str = "index.rst"
) -> dict[Path, tuple[int, ...]]:
    """Score a Sphinx tree starting with its root document."""
    doc_root = doc_root.resolve()
    root = doc_root / root_document
    if not root.is_file():
        raise FileNotFoundError(f"Sphinx root document not found: {root}")
    return _score_from_roots(doc_root, iter([((0,), root)]))


def catalog_scores(
    document_scores: Mapping[Path, tuple[int, ...]],
    doc_root: Path,
    *,
    compact_catalog: str | None = None,
    shared_template: bool = False,
    shared_template_score: tuple[int, ...] | None = None,
) -> dict[PurePosixPath, tuple[tuple[int, ...], tuple[Path, ...]]]:
    """Map source scores to normal or compact gettext catalog paths."""
    if compact_catalog is not None:
        documents = tuple(sorted(document_scores, key=lambda path: path.as_posix()))
        return {
            PurePosixPath(f"{compact_catalog}.pot"): (
                min(document_scores.values()),
                documents,
            )
        }

    catalogs: dict[PurePosixPath, tuple[tuple[int, ...], tuple[Path, ...]]] = {
        PurePosixPath(
            f"{document.relative_to(doc_root).as_posix().removesuffix('.rst')}.pot"
        ): (score, (document,))
        for document, score in document_scores.items()
    }
    if not shared_template:
        return catalogs
    if shared_template_score is not None:
        catalogs[SHARED_TEMPLATE_CATALOG] = (shared_template_score, ())
        return catalogs

    deepest_depth = max(score[0] for score in document_scores.values())
    fallback_positions = [
        score[1]
        for score in document_scores.values()
        if score[0] == deepest_depth and len(score) == 2
    ]
    template_score = (
        (deepest_depth, max(fallback_positions) + 1)
        if fallback_positions
        else (deepest_depth + 1, 1)
    )
    catalogs[SHARED_TEMPLATE_CATALOG] = (template_score, ())
    return catalogs
