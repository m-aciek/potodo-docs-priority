"""Discover translation languages using the dashboard's devguide table policy."""

import re
from dataclasses import dataclass
from pathlib import Path

from .priority import normalize_language

# Keep the dashboard's Transifex policy, including languages explicitly marked
# as using Transifex in the devguide even before the dashboard adds them.
CPYTHON_TRANSIFEX_LANGUAGES = frozenset(
    {"zh-cn", "pt-br", "ja", "uk", "pl", "ru", "id", "pa", "vi"}
)


@dataclass(frozen=True)
class Language:
    """A language and its CPython translation repository."""

    code: str
    name: str
    repository: str | None
    transifex: bool


def get_languages(devguide: Path) -> tuple[Language, ...]:
    """Read the language, repository and platform from the devguide table."""
    # Docutils understands the table and inline links; unknown Sphinx roles
    # remain as text, which lets us extract :github: targets as in the dashboard.
    from docutils import core  # pylint: disable=import-outside-toplevel
    from docutils.nodes import row, table  # pylint: disable=import-outside-toplevel

    source = devguide / "documentation/translations/translating.rst"
    doctree = core.publish_doctree(
        source.read_text(encoding="utf-8"),
        settings_overrides={"report_level": 5, "halt_level": 6},
    )
    languages = []
    for node in doctree.findall(table):
        rows = list(node.findall(row))
        if [cell.astext() for cell in rows[0]] != [
            "Language",
            "Coordination team",
            "Links",
        ]:
            continue
        for row_node in rows[1:]:
            match = re.fullmatch(r"(.*?)\s*\(([^()]+)\)", row_node[0].astext())
            if match is None:
                raise ValueError(f"Missing language code: {row_node[0].astext()}")
            code = normalize_language(match[2])
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", code):
                raise ValueError(f"Invalid language code: {code}")
            links = row_node[2].astext()
            repo = re.search(r":github:`[^`]*<([\w.-]+/[\w.-]+)>`", links)
            languages.append(
                Language(
                    code,
                    match[1],
                    repo[1] if repo else None,
                    code in CPYTHON_TRANSIFEX_LANGUAGES or "Transifex" in links,
                )
            )
    if not languages:
        raise ValueError("No translation languages found in the devguide")
    if len({language.code for language in languages}) != len(languages):
        raise ValueError("Duplicate language codes in the devguide")
    return tuple(languages)


def locale_directory(locales: Path, language: str) -> Path | None:
    """Find an available gettext locale without scanning other languages."""
    aliases = {
        "zh-cn": ("zh-hans",),
        "zh-tw": ("zh-hant",),
        "it": ("it-it",),
        "hi-in": ("hi",),
    }
    codes = (
        normalize_language(language),
        *aliases.get(normalize_language(language), ()),
    )
    if not locales.is_dir():
        return None
    for code in codes:
        for path in sorted(locales.iterdir()):
            if normalize_language(path.name) == code:
                messages = path / "LC_MESSAGES"
                if messages.is_dir() and any(messages.rglob("*.po")):
                    return path
    return None
