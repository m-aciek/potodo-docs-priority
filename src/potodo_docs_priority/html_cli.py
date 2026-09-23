"""Command-line entry point for the linked HTML priority report."""

import argparse
from pathlib import Path
from typing import Sequence

from .html_report import (
    HtmlSection,
    build_sections,
    project_filename,
    render_html,
    render_index,
)
from .priority import PROJECTS, positive_int


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="potodo-docs-priority-html",
        description="Build HTML pages linking all translation resources by priority.",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="directory for generated pages"
    )
    parser.add_argument("--title", default="Documentation translation priorities")
    parser.add_argument("--language", required=True, help="CPython/Packaging locale")
    parser.add_argument("--sphinx-language", required=True, help="Sphinx locale")
    parser.add_argument(
        "--projects", nargs="+", choices=PROJECTS, default=list(PROJECTS)
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        help="maximum resources per project (default: all)",
    )
    parser.add_argument("--stats-snapshots", type=positive_int, default=30)
    parser.add_argument("--docs-version", default="3")
    parser.add_argument(
        "--plausible-stats", type=Path, default=Path("../plausible-stats")
    )
    parser.add_argument("--cpython-source", type=Path, default=Path("../cpython"))
    parser.add_argument(
        "--cpython-translations", type=Path, default=Path("../python-docs-pl")
    )
    parser.add_argument(
        "--packaging-source",
        type=Path,
        default=Path("../packaging.python.org/source"),
    )
    parser.add_argument(
        "--packaging-translations",
        type=Path,
        default=Path("../packaging.python.org/locales"),
    )
    parser.add_argument("--sphinx-source", type=Path, default=Path("../sphinx/doc"))
    parser.add_argument(
        "--sphinx-translations",
        type=Path,
        default=Path("../sphinx-doc-translations/locales"),
    )
    return parser.parse_args(argv)


def write_pages(
    output: Path, sections: Sequence[HtmlSection], title: str
) -> tuple[Path, ...]:
    """Write a landing page and one page for every requested project."""
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    paths = []
    if not any(section.project == "cpython" for section in sections):
        index = output / "index.html"
        index.write_text(render_index(sections, title), encoding="utf-8")
        paths.append(index)
    for section in sections:
        path = output / project_filename(section.project)
        path.write_text(render_html(section, title, sections), encoding="utf-8")
        paths.append(path)
    return tuple(paths)


def main() -> None:
    """Build the linked HTML priority reports."""
    args = parse_args()
    sections = build_sections(
        projects=args.projects,
        language=args.language,
        sphinx_language=args.sphinx_language,
        plausible_stats=args.plausible_stats,
        cpython_source=args.cpython_source,
        cpython_translations=args.cpython_translations,
        packaging_source=args.packaging_source,
        packaging_translations=args.packaging_translations,
        sphinx_source=args.sphinx_source,
        sphinx_translations=args.sphinx_translations,
        snapshots=args.stats_snapshots,
        docs_version=args.docs_version,
        limit=args.limit,
    )
    for path in write_pages(args.output, sections, args.title):
        print(path)
