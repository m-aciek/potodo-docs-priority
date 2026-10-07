"""Map published Sphinx build diagnostics to local translated messages."""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path, PurePosixPath
from string import punctuation
from typing import Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

import polib

from .document_score import INCLUDE_RE, resolve_document

LOCATION_RE = re.compile(
    r"^(.+?\.rst):(\d+)(?::<translated>:\d+)?: (?:WARNING|ERROR):",
    re.MULTILINE,
)


def warnings_url(language: str) -> str:
    """Return the published Sphinx build report URL."""
    code = language.replace("_", "-").lower()
    return f"https://translations.python.org/warnings-{quote(code, safe='-')}.txt"


def load_warnings(language: str) -> dict[PurePosixPath, frozenset[int]]:
    """Read source locations; a missing report provides no build diagnostics."""
    try:
        with urlopen(warnings_url(language), timeout=30) as response:
            text = response.read().decode("utf-8")
    except HTTPError as error:
        if error.code == 404:
            return {}
        raise
    locations: defaultdict[PurePosixPath, set[int]] = defaultdict(set)
    for filename, line in LOCATION_RE.findall(text):
        document = PurePosixPath(filename)
        if not document.is_absolute() and ".." not in document.parts:
            locations[document].add(int(line))
    return {document: frozenset(lines) for document, lines in locations.items()}


def _catalog_sources(
    source_root: Path, documents: Sequence[PurePosixPath]
) -> set[Path]:
    """Include fragments whose messages can occur in the same catalog."""
    sources = set()
    pending = [source_root / document for document in documents]
    while pending:
        source = pending.pop()
        if source in sources or not source.is_file():
            continue
        sources.add(source)
        for match in INCLUDE_RE.finditer(source.read_text(encoding="utf-8")):
            included = resolve_document(
                match.group("target"), parent=source.parent, doc_root=source_root
            )
            if included is not None:
                pending.append(included)
    return sources


def _occurrence_document(filename: str) -> PurePosixPath:
    """Normalize gettext's relative source-root prefix without losing folders."""
    while filename.startswith("../"):
        filename = filename[3:]
    return PurePosixPath(filename.removeprefix("Doc/"))


def _heading_locations(text: str) -> dict[int, str]:
    """Map unindented RST heading adornments to normalized title text."""
    lines = text.splitlines()
    headings = {}
    for index, (title, underline) in enumerate(zip(lines, lines[1:])):
        title = title.rstrip()
        underline = underline.rstrip()
        if (
            not title
            or title[0].isspace()
            or len(underline) < len(title)
            or underline[0] not in punctuation
            or len(set(underline)) != 1
        ):
            continue
        first = index
        if index and lines[index - 1].strip():
            # An overline must match the underline. Other preceding text
            # means this is not a section heading.
            if lines[index - 1].rstrip() != underline:
                continue
            first -= 1
        for line in range(first + 1, index + 3):
            headings[line] = " ".join(title.split())
    return headings


def _source_messages(
    catalog: polib.POFile,
    source: Path,
    document: PurePosixPath,
    warning_lines: frozenset[int],
) -> set[str]:
    text = source.read_text(encoding="utf-8")
    headings = _heading_locations(text)
    line_starts = [0] + [match.end() for match in re.finditer("\n", text)]
    matches: defaultdict[int, list[str]] = defaultdict(list)
    occurrences: defaultdict[int, list[str]] = defaultdict(list)
    for entry in catalog:
        if not entry.msgid.strip() or entry.obsolete:
            continue
        normalized = " ".join(entry.msgid.split())
        for line in warning_lines:
            if headings.get(line) == normalized:
                matches[line].append(entry.msgid)
        for filename, line in entry.occurrences:
            if _occurrence_document(filename) == document and (
                line.isdigit() and int(line) in warning_lines
            ):
                occurrences[int(line)].append(entry.msgid)
        pattern = r"\s+".join(map(re.escape, entry.msgid.split()))
        for match in re.finditer(r"(?<!\S)" + pattern + r"(?!\S)", text):
            first = bisect_right(line_starts, match.start())
            last = bisect_right(line_starts, match.end() - 1)
            for line in warning_lines:
                if first <= line <= last:
                    matches[line].append(entry.msgid)
    affected: set[str] = set()
    for line in warning_lines:
        if line in occurrences:
            affected.update(occurrences[line])
        elif line in matches:
            longest = max(map(len, matches[line]))
            affected.update(
                message for message in matches[line] if len(message) == longest
            )
    return affected


def catalog_messages(
    path: Path,
    source_root: Path,
    documents: Sequence[PurePosixPath],
    locations: Mapping[PurePosixPath, frozenset[int]],
) -> frozenset[str]:
    """Find warned messages by source occurrences or matching source text.

    Catalogs can omit occurrences. Match their source messages with
    whitespace normalized, requiring the warning line to fall within the text
    or its heading adornments. Locations that could use an included fragment's
    line number with its parent's filename are ambiguous and are excluded.
    Prefer the longest match to avoid selecting a short message embedded in a
    longer one. Unmapped locations never invalidate an arbitrary nearby entry.
    """
    if not locations:
        return frozenset()
    warned_sources = {}
    for source in _catalog_sources(source_root, documents):
        document = PurePosixPath(source.relative_to(source_root).as_posix())
        if document in locations:
            warned_sources[source] = document
    if not warned_sources:
        return frozenset()
    catalog = polib.pofile(str(path))
    affected: set[str] = set()
    for source, document in warned_sources.items():
        fragments = _catalog_sources(source_root, [document]) - {source}
        fragment_lines = max(
            (
                len(fragment.read_text(encoding="utf-8").splitlines())
                for fragment in fragments
            ),
            default=0,
        )
        # Sphinx can retain a fragment-relative line while replacing the
        # filename with the parent document during translation. Occurrences
        # and parent text matches cannot disambiguate that location.
        lines = frozenset(line for line in locations[document] if line > fragment_lines)
        if lines:
            affected.update(_source_messages(catalog, source, document, lines))
    return frozenset(affected)
