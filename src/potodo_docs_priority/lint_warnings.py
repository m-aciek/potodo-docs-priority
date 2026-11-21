"""Account for translated entries with lint or Sphinx build findings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import polib
from sphinxlint import PoEntry, check_file, checkers


def warnings_url(language: str) -> str:
    """Return the published lint report URL without fetching it."""
    code = language.replace("_", "-").lower()
    return f"https://translations.python.org/warnings-lint-{quote(code, safe='-')}.txt"


@dataclass(frozen=True)
class LintResult:
    """Local diagnostics and their effect on catalog completion."""

    incomplete_words: int = 0
    warnings: tuple[str, ...] = ()


def check_catalog(
    path: Path, *, build_messages: frozenset[str] = frozenset()
) -> LintResult:
    """Lint a local catalog and count affected translated source words once.

    Match lint diagnostics by PO entry identity, independently of line numbers.
    Build findings and local lint findings count each translated entry once.
    """
    catalog = polib.pofile(str(path))
    enabled = [checker for checker in checkers.all_checkers.values() if checker.enabled]
    failures = check_file(str(path), enabled)
    affected_entries = set()
    warnings = []
    for failure in failures:
        if isinstance(failure, str):
            raise OSError(failure)
        if failure.po_entry is not None:
            affected_entries.add(failure.po_entry)
        warnings.append(
            (
                failure.line_no,
                f"{path.name}:{failure.line_no}: "
                f"{failure.msg} ({failure.checker_name})",
            )
        )
    if not failures and not build_messages:
        return LintResult()
    words = 0
    for entry in catalog.translated_entries():
        identity = PoEntry(entry.msgctxt, entry.msgid, entry.msgid_plural)
        if entry.msgid in build_messages or identity in affected_entries:
            words += len(entry.msgid.split())
    return LintResult(words, tuple(message for _, message in sorted(warnings)))
