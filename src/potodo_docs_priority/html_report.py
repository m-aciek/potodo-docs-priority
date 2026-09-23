"""Build an HTML report linking priority resources to translation UIs."""

from __future__ import annotations

import configparser
import html
import importlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Iterable, Sequence
from urllib.parse import quote, urlencode

import polib
from potodo.po_file import PoDirectories, PoDirectory

from .document_score import score_sphinx_documents
from .html_tuning import TUNING_SCRIPT
from .priority import (
    CORE_RESOURCE_BOOST,
    PROJECTS,
    MetricWeights,
    Project,
    build_priority_rows,
    calculate_document_scores,
    load_page_visitors,
    normalize_language,
    normalized_metric_values,
    packaging_document_urls,
    resolve_source_root,
    resolve_translation_paths,
)

if TYPE_CHECKING:
    from polib import POEntry

ADORNMENT_RE = re.compile(r"^(?P<char>[^\w\s])(?P=char){2,}$")


@dataclass(frozen=True)
class HtmlMetrics:
    """Raw and combined metrics displayed for an HTML resource."""

    priority: float
    completion: float | None = None
    original_visitors: int | None = None
    translated_visitors: int | None = None
    document_score: tuple[int, ...] | None = None
    core_boost: float = 0
    ranks: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class HtmlItem:
    """One linked resource in the HTML report."""

    resource: str
    title: str
    url: str
    metrics: HtmlMetrics


@dataclass(frozen=True)
class HtmlSection:
    """A project heading and its resources in priority order."""

    project: str
    title: str
    items: tuple[HtmlItem, ...]


@dataclass(frozen=True)
class _PackagingCandidate:
    document: PurePosixPath
    title: str
    entry: POEntry
    completion: float
    original_visitors: int
    translated_visitors: int | None
    document_score: tuple[int, ...]


def rst_title(path: Path) -> str:
    """Return the first reStructuredText section title in a source file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for title, underline in zip(lines, lines[1:]):
        title = title.strip()
        underline = underline.strip()
        if title and ADORNMENT_RE.fullmatch(underline) and len(underline) >= len(title):
            return title
    return path.stem


def translated_title(po_path: Path, source_title: str) -> str:
    """Use the translated document heading when the catalog contains one."""
    entries = polib.pofile(str(po_path))
    for entry in entries:
        if entry.msgid.casefold() == source_title.casefold():
            return entry.msgstr if entry.translated() else entry.msgid
    return source_title


def transifex_resources(translations_root: Path) -> dict[str, str]:
    """Read exact PO-to-resource mappings from a Transifex configuration."""
    config_path = translations_root / ".tx" / "config"
    if not config_path.is_file():
        config_path = translations_root / "locales" / ".tx" / "config"
    if not config_path.is_file():
        return {}
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(config_path, encoding="utf-8")
    resources = {}
    for section in parser.sections():
        if not parser.has_option(section, "file_filter"):
            continue
        resource = parser.get(
            section, "resource_name", fallback=section.rpartition(":r:")[2]
        )
        resources[parser.get(section, "file_filter")] = resource
    return resources


def transifex_url(project: str, language: str, resource: str) -> str:
    """Return a Transifex resource translation URL."""
    organization, tx_project = {
        "cpython": ("python-doc", "python-newest"),
        "sphinx": ("sphinx-doc", "sphinx-doc"),
    }[project]
    return (
        f"https://app.transifex.com/{organization}/{tx_project}/translate/"
        f"#{quote(language, safe='_')}/{quote(resource, safe='-_')}"
    )


def weblate_checksum(source: str, context: str = "") -> str:
    """Return the checksum Weblate uses to address a bilingual PO unit."""
    try:
        siphash = importlib.import_module("siphashc").siphash
    except ImportError as error:
        raise RuntimeError(
            "Packaging HTML links require potodo-docs-priority[html]"
        ) from error
    return format(siphash("Weblate Sip Hash", source + context), "016x")


def weblate_url(language: str, entry: POEntry) -> str:
    """Return a Weblate URL focused on one unit."""
    parameters = {}
    if not entry.translated():
        parameters["q"] = "state:<translated"
    parameters["checksum"] = weblate_checksum(entry.msgid, entry.msgctxt or "")
    query = urlencode(parameters)
    return (
        "https://hosted.weblate.org/translate/pypa/packaging-python-org/"
        f"{quote(language, safe='-_')}/?{query}"
    )


def _load_visitors(
    project: Project,
    stats: Path | None,
    language: str,
    snapshots: int,
    docs_version: str,
) -> tuple[dict[str, int] | None, dict[str, int] | None]:
    if project.stats_site is None:
        return None, None
    if stats is None:
        raise ValueError(f"Plausible stats are required for {project.name}")
    original_prefix = (
        docs_version if project.name == "cpython" else project.original_stats_prefix
    )
    assert original_prefix is not None
    original = load_page_visitors(stats, project.stats_site, original_prefix, snapshots)
    try:
        translated = load_page_visitors(
            stats, project.stats_site, normalize_language(language), snapshots
        )
    except FileNotFoundError:
        translated = None
    return original, translated


def _visitor_count(visitors: dict[str, int] | None, urls: Iterable[str]) -> int | None:
    if visitors is None:
        return None
    return sum(visitors.get(url, 0) for url in urls)


def _scan_paths(paths: Sequence[Path]) -> PoDirectories:
    directories = PoDirectories()
    for path in paths:
        directory = PoDirectory(path, use_cache=False)
        directory.scan()
        directories.append(directory)
    return directories


def _standard_items(
    project_name: str,
    source: Path,
    translations: Path,
    language: str,
    stats: Path | None,
    snapshots: int,
    docs_version: str,
    limit: int | None,
) -> tuple[HtmlItem, ...]:
    project = PROJECTS[project_name]
    paths = resolve_translation_paths(project, [translations], language)
    directories = _scan_paths(paths)
    scores = calculate_document_scores(project, source)
    original, translated = _load_visitors(
        project, stats, language, snapshots, docs_version
    )
    rows = build_priority_rows(
        directories,
        scores,
        original,
        translated,
        project=project,
        language=language,
        docs_version=docs_version,
        show_finished=False,
    )
    source_root = resolve_source_root(project, source)
    tx_resources = transifex_resources(translations)
    items = []
    for row in rows:
        resource = row.resource.removesuffix(".po")
        catalog = scores[PurePosixPath(row.resource).with_suffix(".pot")]
        if catalog.documents and (source_root / catalog.documents[0]).is_file():
            title = translated_title(
                Path(row.path), rst_title(source_root / catalog.documents[0])
            )
        elif resource == "sphinx":
            title = "Documentation templates"
        else:
            title = resource
        tx_resource = tx_resources.get(row.resource, resource.replace("/", "--"))
        items.append(
            HtmlItem(
                resource=resource,
                title=title,
                url=transifex_url(project_name, language, tx_resource),
                metrics=HtmlMetrics(
                    priority=row.priority,
                    completion=row.completion,
                    original_visitors=row.original_visitors,
                    translated_visitors=row.translated_visitors,
                    document_score=row.document_score,
                    core_boost=row.core_boost,
                ),
            )
        )
    return _ranked_items(items, limit)


def _occurrence_document(path: str) -> PurePosixPath | None:
    parts = PurePosixPath(path).parts
    try:
        source_index = parts.index("source")
    except ValueError:
        return None
    document = PurePosixPath(*parts[source_index + 1 :])
    return document if document.suffix == ".rst" else None


def _packaging_entries(
    po_path: Path,
) -> dict[PurePosixPath, list[POEntry]]:
    result: defaultdict[PurePosixPath, list[POEntry]] = defaultdict(list)
    for entry in polib.pofile(str(po_path)):
        if entry.obsolete or not entry.msgid:
            continue
        documents = {
            document
            for occurrence, _ in entry.occurrences
            if (document := _occurrence_document(occurrence)) is not None
        }
        for document in documents:
            result[document].append(entry)
    return dict(result)


def _packaging_candidates(
    source: Path,
    translations: Path,
    language: str,
    stats: Path,
    snapshots: int,
) -> list[_PackagingCandidate]:
    project = PROJECTS["packaging"]
    source_root = resolve_source_root(project, source)
    po_path = (
        resolve_translation_paths(project, [translations], language)[0] / "messages.po"
    )
    if not po_path.is_file():
        raise FileNotFoundError(
            f"Packaging translation catalog not found: {po_path}; "
            "check out the translation/source branch"
        )
    entries_by_document = _packaging_entries(po_path)
    document_scores = score_sphinx_documents(source_root)
    original, translated = _load_visitors(project, stats, language, snapshots, "3")
    candidates = []
    for source_path, score in document_scores.items():
        document = PurePosixPath(source_path.relative_to(source_root).as_posix())
        entries = entries_by_document.get(document, [])
        if not entries:
            continue
        words = sum(len(entry.msgid.split()) for entry in entries)
        translated_words = sum(
            len(entry.msgid.split()) for entry in entries if entry.translated()
        )
        completion = 100 * translated_words / words if words else 0
        if completion == 100:
            continue
        title = rst_title(source_path)
        title_entry = next(
            (entry for entry in entries if entry.msgid.casefold() == title.casefold()),
            None,
        )
        link_entry = title_entry or next(
            entry for entry in entries if not entry.translated()
        )
        candidates.append(
            _PackagingCandidate(
                document=document,
                title=(
                    title_entry.msgstr
                    if title_entry is not None and title_entry.translated()
                    else title
                ),
                entry=link_entry,
                completion=completion,
                original_visitors=_visitor_count(
                    original, packaging_document_urls(document, "en")
                )
                or 0,
                translated_visitors=_visitor_count(
                    translated,
                    packaging_document_urls(document, normalize_language(language)),
                ),
                document_score=score,
            )
        )
    return candidates


def _packaging_items(
    source: Path,
    translations: Path,
    language: str,
    stats: Path,
    snapshots: int,
    limit: int | None,
) -> tuple[HtmlItem, ...]:
    candidates = _packaging_candidates(source, translations, language, stats, snapshots)
    if not candidates:
        return ()
    project = PROJECTS["packaging"]
    metrics: list[tuple[str, Sequence[float | int | tuple[int, ...]], float]] = [
        (
            "completion",
            [candidate.completion for candidate in candidates],
            project.weights.completion,
        ),
        (
            "navigation",
            [candidate.document_score for candidate in candidates],
            project.weights.navigation,
        ),
        (
            "original_popularity",
            [candidate.original_visitors for candidate in candidates],
            project.weights.original_popularity,
        ),
    ]
    if candidates[0].translated_visitors is not None:
        metrics.append(
            (
                "translated_popularity",
                [candidate.translated_visitors or 0 for candidate in candidates],
                project.weights.translated_popularity,
            )
        )
    weighted_ranks = [
        (normalized_metric_values(name, values), weight)
        for name, values, weight in metrics
    ]
    ranked = []
    for index, candidate in enumerate(candidates):
        priority = (
            100
            * sum(ranks[index] * weight for ranks, weight in weighted_ranks)
            / sum(weight for _, weight in weighted_ranks)
        )
        ranked.append((priority, candidate))
    ranked.sort(key=lambda item: (-item[0], item[1].document.as_posix()))
    items = []
    for priority, candidate in ranked:
        resource = candidate.document.as_posix().removesuffix(".rst")
        items.append(
            HtmlItem(
                resource=resource,
                title=candidate.title,
                url=weblate_url(language, candidate.entry),
                metrics=HtmlMetrics(
                    priority=priority,
                    completion=candidate.completion,
                    original_visitors=candidate.original_visitors,
                    translated_visitors=candidate.translated_visitors,
                    document_score=candidate.document_score,
                ),
            )
        )
    return _ranked_items(items, limit)


METRICS = {
    "completion": ("Completion", "completion", True),
    "navigation": ("Navigation proximity", "document_score", False),
    "original_popularity": ("Original visitors", "original_visitors", True),
    "translated_popularity": ("Translated visitors", "translated_visitors", True),
}


def _ranked_items(items: Sequence[HtmlItem], limit: int | None) -> tuple[HtmlItem, ...]:
    """Keep full-population percentiles available for interactive reweighting."""
    ranks = {}
    for name, (_, attribute, _) in METRICS.items():
        values = [getattr(item.metrics, attribute) for item in items]
        if values and all(value is not None for value in values):
            ranks[name] = normalized_metric_values(name, values)
    return tuple(
        replace(
            item,
            metrics=replace(
                item.metrics,
                ranks={name: values[index] for name, values in ranks.items()},
            ),
        )
        for index, item in enumerate(items[:limit])
    )


def build_sections(
    *,
    projects: Sequence[str],
    language: str,
    sphinx_language: str,
    plausible_stats: Path,
    cpython_source: Path,
    cpython_translations: Path,
    packaging_source: Path,
    packaging_translations: Path,
    sphinx_source: Path,
    sphinx_translations: Path,
    snapshots: int,
    docs_version: str,
    limit: int | None = None,
) -> tuple[HtmlSection, ...]:
    """Rank requested projects and return renderable report sections."""
    sections = []
    if "cpython" in projects:
        sections.append(
            HtmlSection(
                project="cpython",
                title="CPython Docs translation",
                items=_standard_items(
                    "cpython",
                    cpython_source,
                    cpython_translations,
                    language,
                    plausible_stats,
                    snapshots,
                    docs_version,
                    limit,
                ),
            )
        )
    if "packaging" in projects:
        sections.append(
            HtmlSection(
                project="packaging",
                title="Packaging guide translation",
                items=_packaging_items(
                    packaging_source,
                    packaging_translations,
                    language,
                    plausible_stats,
                    snapshots,
                    limit,
                ),
            )
        )
    if "sphinx" in projects:
        sections.append(
            HtmlSection(
                project="sphinx",
                title="Sphinx docs translations",
                items=_standard_items(
                    "sphinx",
                    sphinx_source,
                    sphinx_translations,
                    sphinx_language,
                    None,
                    snapshots,
                    docs_version,
                    limit,
                ),
            )
        )
    return tuple(sections)


def project_filename(project: str) -> str:
    """Return the output filename for a project report."""
    return "index.html" if project == "cpython" else f"{project}.html"


def _weight_controls(section: HtmlSection) -> str:
    if not section.items:
        return ""
    project = PROJECTS.get(section.project)
    weights = project.weights if project else MetricWeights()
    available = {name for item in section.items for name in item.metrics.ranks}
    lines = [
        f'<form id="weights" data-project="{html.escape(section.project, quote=True)}" '
        "hidden><fieldset><legend>Priority weights</legend>",
        "<p>Higher weights give a metric more influence. Zero disables it. "
        "Higher completion favors resources closer to being finished.</p>",
    ]
    for name, (label, _, _) in METRICS.items():
        if name in available:
            lines.append(
                f'<label>{label} <input type="number" data-metric="{name}" '
                f'min="0" step="any" required value="{getattr(weights, name):g}">'
                "</label>"
            )
    if section.project == "cpython":
        lines.append(
            "<label>Core resources boost (points) "
            f'<input id="core-boost" type="number" min="0" step="any" required '
            f'value="{CORE_RESOURCE_BOOST:g}"></label>'
            "<p>Core resources: bugs, tutorial/*, builtins/functions. "
            "The boost is added after weighting the metrics.</p>"
        )
    lines.append(
        '<button type="reset">Reset weights</button></fieldset>'
        f'<p id="tuning-status" role="status">{len(section.items)} unfinished '
        "resources.</p></form>"
    )
    return "\n".join(lines)


def render_html(
    section: HtmlSection,
    site_title: str,
    sections: Sequence[HtmlSection] = (),
) -> str:
    """Render one project's complete, dependency-free HTML page."""
    page_title = f"{section.title} – {site_title}"
    navigation = (
        " | ".join(
            f'<a href="{html.escape(project_filename(item.project), quote=True)}">'
            f"{html.escape(item.title)}</a>"
            for item in sections
        )
        if len(sections) > 1
        else ""
    )
    columns = [
        (name, "Navigation distance" if name == "navigation" else label, attribute)
        for name, (label, attribute, _) in METRICS.items()
        if any(getattr(item.metrics, attribute) is not None for item in section.items)
    ]
    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="utf-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1">',
        f"  <title>{html.escape(page_title)}</title>",
        "  <style>body { font-family: sans-serif; } "
        "#weights { margin-block: 1rem; } #weights label { display: inline-block; "
        "margin: .4rem 1rem .4rem 0; } #weights input { width: 5rem; } "
        ".table-scroll { overflow-x: auto; margin-block: 1rem; } "
        "table { border-collapse: collapse; width: 100%; table-layout: fixed; } "
        "th, td { padding: .6rem .8rem; border-bottom: 1px solid #ccc; "
        "text-align: right; vertical-align: top; } "
        "thead th { white-space: nowrap; } th:first-child { text-align: left; } "
        ".resource-column { width: 18rem; } .priority-column { width: 8rem; } "
        "tbody th { font-weight: normal; min-width: 16rem; } "
        "td { font-variant-numeric: tabular-nums; white-space: nowrap; } "
        "th.metric-column, td.metric-column { width: 10rem; min-width: 10rem; } "
        "td.metric-column { background-repeat: no-repeat; background-size: 100% 100%; } "
        ".resource-title { display: block; margin-top: .2rem; } "
        ".core-resource { display: block; margin-top: .2rem; font-size: .85em; }"
        "</style>",
        "</head>",
        "<body>",
        f"  <h1>{html.escape(section.title)}</h1>",
    ]
    if navigation:
        lines.append(f"  <nav>{navigation}</nav>")
    lines.append(_weight_controls(section))
    lines.extend(
        (
            '<div class="table-scroll" tabindex="0" role="region" '
            'aria-label="Scrollable resource metrics">',
            '<table aria-label="Translation priorities"><colgroup>'
            '<col class="resource-column"><col class="priority-column">'
            + "".join('<col class="metric-column">' for _ in columns)
            + "</colgroup><thead><tr>"
            '<th scope="col">Resource</th><th scope="col">Priority</th>',
        )
    )
    lines.extend(
        f'<th class="metric-column" scope="col">{label}</th>' for _, label, _ in columns
    )
    lines.append('</tr></thead><tbody id="resources">')
    for item in section.items:
        ranks = html.escape(json.dumps(item.metrics.ranks), quote=True)
        resource = html.escape(item.resource, quote=True)
        core = str(item.metrics.core_boost > 0).lower()
        badge = (
            '<span class="core-resource">Core resource</span> '
            if core == "true"
            else ""
        )
        lines.append(
            f'    <tr data-resource="{resource}" data-ranks="{ranks}" '
            f'data-core="{core}" data-priority="{item.metrics.priority}">'
            f'<th scope="row"><a href="{html.escape(item.url, quote=True)}">'
            f"{html.escape(item.resource)}</a>"
            f'<span class="resource-title">{html.escape(item.title)}</span>{badge}</th>'
            f'<td class="priority">{item.metrics.priority:.2f}</td>'
        )
        for name, _, attribute in columns:
            value = getattr(item.metrics, attribute)
            if value is None:
                formatted = "—"
                rank_style = ""
            elif name == "completion":
                formatted = f"{value:.2f}%"
                rank_style = _metric_background(item, name)
            elif name == "navigation":
                formatted = ".".join(map(str, value))
                rank_style = _metric_background(item, name)
            else:
                formatted = f"{value:,}"
                rank_style = _metric_background(item, name)
            lines.append(
                f'<td class="metric-column {name}"{rank_style}>{formatted}</td>'
            )
        lines.append("</tr>")
    lines.append("  </tbody></table></div>")
    lines.append(
        '<button id="show-more" type="button" aria-controls="resources" '
        'aria-expanded="false" hidden>Show all</button>'
    )
    lines.append(TUNING_SCRIPT)
    lines.extend(("</body>", "</html>", ""))
    return "\n".join(lines)


def _metric_background(item: HtmlItem, name: str) -> str:
    """Return a normalized metric bar as an escaped inline style."""
    rank = min(1, max(0, item.metrics.ranks.get(name, 0)))
    percentage = f"{rank * 100:.4f}%"
    return (
        ' style="background-image: linear-gradient(to right, '
        f'rgba(30, 136, 229, .22) {percentage}, transparent {percentage});"'
    )


def render_index(sections: Sequence[HtmlSection], title: str) -> str:
    """Render an index linking the generated project pages."""
    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '  <meta charset="utf-8">',
        f"  <title>{html.escape(title)}</title>",
        "  <style>body { font-family: sans-serif; }</style>",
        "</head>",
        "<body>",
        f"  <h1>{html.escape(title)}</h1>",
        "  <ul>",
    ]
    for section in sections:
        lines.append(
            f'    <li><a href="{html.escape(project_filename(section.project), quote=True)}">'
            f"{html.escape(section.title)}</a></li>"
        )
    lines.extend(("  </ul>", "</body>", "</html>", ""))
    return "\n".join(lines)
