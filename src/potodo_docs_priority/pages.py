"""Build a multilingual Pages site from devguide translation repositories."""

import argparse
import html
import json
import logging
import subprocess
from pathlib import Path
from typing import Sequence
from urllib.request import urlopen

from .html_cli import write_pages
from .html_report import HtmlSection, build_sections
from .languages import Language, get_languages, locale_directory
from .priority import normalize_language


def release_branches() -> tuple[str, ...]:
    """Try supported releases newest first, then the usual default branches."""
    with urlopen(
        "https://peps.python.org/api/release-cycle.json", timeout=60
    ) as response:
        data = json.load(response)
    versions = sorted(
        (
            version
            for version, metadata in data.items()
            if metadata["status"] in {"prerelease", "bugfix", "security"}
        ),
        key=lambda version: tuple(map(int, version.split("."))),
        reverse=True,
    )
    return tuple(versions) + ("master", "main")


def checkout_translations(
    repository: str, destination: Path, branches: Sequence[str]
) -> Path:
    """Check out the newest available supported branch with a shallow fetch."""
    url = f"https://github.com/{repository}.git"
    result = subprocess.run(
        ["git", "ls-remote", "--heads", url], check=True, capture_output=True, text=True
    )
    available = {line.split("refs/heads/", 1)[1] for line in result.stdout.splitlines()}
    branch = next((branch for branch in branches if branch in available), None)
    if branch is None:
        raise ValueError(f"No supported or default branch in {repository}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", branch, url, str(destination)],
            check=True,
        )
    else:
        subprocess.run(
            ["git", "-C", str(destination), "fetch", "--depth", "1", "origin", branch],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"],
            check=True,
        )
    print(f"{repository}: {branch}", flush=True)
    return destination


def language_sections(
    language: Language,
    sources: Path,
    translations: Path | None,
    *,
    snapshots: int = 30,
    docs_version: str = "3",
) -> tuple[HtmlSection, ...]:
    """Build only projects with PO catalogs for this language."""
    catalogs: dict[str, Path] = {}
    if translations is not None and any(translations.rglob("*.po")):
        catalogs["cpython"] = translations
    for project, locales in (
        ("packaging", sources / "packaging-translations/locales"),
        ("sphinx", sources / "sphinx-doc-translations/locales"),
    ):
        locale = locale_directory(locales, language.code)
        if locale is not None:
            catalogs[project] = locale
    sections: list[HtmlSection] = []
    for project, catalog in catalogs.items():
        try:
            report = build_sections(
                projects=[project],
                language=language.code if project == "cpython" else catalog.name,
                plausible_stats=sources / "plausible-stats",
                cpython_source=sources / "cpython",
                cpython_translations=catalog,
                packaging_source=sources / "packaging/source",
                packaging_translations=catalog / "LC_MESSAGES",
                sphinx_source=sources / "sphinx/doc",
                sphinx_translations=catalog / "LC_MESSAGES",
                snapshots=snapshots,
                docs_version=docs_version,
                cpython_transifex=language.transifex,
            )
        except OSError as error:
            # Polib uses OSError for invalid PO syntax. Isolate that upstream
            # catalog problem; filesystem and missing-source errors still fail CI.
            if not str(error).startswith("Syntax error in po file"):
                raise
            logging.warning(
                "%s/%s report unavailable: %s", language.code, project, error
            )
            title = {
                "cpython": "CPython Docs translation",
                "packaging": "Packaging guide translation",
                "sphinx": "Sphinx docs translations",
            }[project]
            report = (
                HtmlSection(
                    project,
                    title,
                    (),
                    "This report is unavailable because a translation catalog could not be read.",
                ),
            )
        sections.extend(report)
    return tuple(sections)


def write_language_index(
    output: Path, reports: Sequence[tuple[Language, Sequence[HtmlSection]]], title: str
) -> Path:
    """Link every devguide language and label those without available catalogs."""
    lines = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{html.escape(title)}</title>",
        "<style>body { font-family: sans-serif; } li { margin-block: .5rem; }</style>",
        "</head><body>",
        f"<h1>{html.escape(title)}</h1>",
        "<ul>",
    ]
    for language, sections in reports:
        label = html.escape(f"{language.name} ({language.code})")
        link = html.escape(f"{language.code}/index.html", quote=True)
        projects = ", ".join(
            section.project + (" (unavailable)" if section.notice else "")
            for section in sections
        )
        status = (
            html.escape(projects) if projects else "No translation catalogs available"
        )
        lines.append(f'<li><a href="{link}">{label}</a> — {status}</li>')
    lines.extend(("</ul>", "</body></html>", ""))
    output.mkdir(parents=True, exist_ok=True)
    path = output / "index.html"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devguide", type=Path, default=Path("sources/devguide"))
    parser.add_argument("--sources", type=Path, default=Path("sources"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--languages", nargs="+", help="optional subset of devguide codes"
    )
    parser.add_argument("--title", default="Documentation translation priorities")
    return parser.parse_args(argv)


def main() -> None:
    """Fetch translations and generate all language and project pages."""
    args = parse_args()
    languages = get_languages(args.devguide)
    if args.languages:
        selected = {normalize_language(code) for code in args.languages}
        unknown = selected - {language.code for language in languages}
        if unknown:
            raise ValueError(f"Languages not in devguide: {sorted(unknown)}")
        languages = tuple(
            language for language in languages if language.code in selected
        )
    branches = release_branches()
    reports = []
    for language in languages:
        translations = (
            checkout_translations(
                language.repository,
                args.sources / "translations" / language.repository,
                branches,
            )
            if language.repository
            else None
        )
        sections = language_sections(language, args.sources, translations)
        title = f"{language.name} ({language.code}) – {args.title}"
        for path in write_pages(
            args.output / language.code, sections, title, language_index="../index.html"
        ):
            print(path)
        reports.append((language, sections))
    print(write_language_index(args.output, reports, args.title))


if __name__ == "__main__":
    main()
