"""Map published Sphinx build diagnostics to local translated messages."""

from __future__ import annotations

import ast
import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from io import StringIO
from pathlib import Path, PurePosixPath
from string import punctuation
from typing import Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

import polib
from docutils import nodes
from docutils.core import publish_doctree

from .document_score import INCLUDE_RE

LOCATION_RE = re.compile(
    r"^([^\n:]+):(\d+)(?::<translated>:\d+)?: (?:WARNING|ERROR): ([^\n]+)$",
    re.MULTILINE,
)
REFERENCES_RE = re.compile(
    r"^inconsistent (?P<kind>term |footnote |citation )?references in translated "
    r"message\. original: (?P<original>.+), translated: (?P<translated>.+) "
    r"\[i18n\.inconsistent_references\]$"
)
REFERENCE_PATTERNS = {
    "term ": re.compile(r"(?<![\\`]):[\w:.-]+:`[^`]+`"),
    None: re.compile(r"(?<![\\`])`[^`]+`_(?!_)"),
    "footnote ": re.compile(r"\[[^\]\n]+\]_"),
    "citation ": re.compile(r"\[[^\]\n]+\]_"),
}


@dataclass(frozen=True)
class BuildWarning:
    """A published location with the diagnostic needed to verify its identity."""

    line: int
    message: str


def warnings_url(language: str) -> str:
    """Return the published Sphinx build report URL."""
    code = language.replace("_", "-").lower()
    return f"https://translations.python.org/warnings-{quote(code, safe='-')}.txt"


def load_warnings(language: str) -> dict[PurePosixPath, tuple[BuildWarning, ...]]:
    """Read diagnostics; a missing report provides no build diagnostics."""
    try:
        with urlopen(warnings_url(language), timeout=30) as response:
            text = response.read().decode("utf-8")
    except HTTPError as error:
        if error.code == 404:
            return {}
        raise
    diagnostics: defaultdict[PurePosixPath, list[BuildWarning]] = defaultdict(list)
    for filename, line, message in LOCATION_RE.findall(text):
        document = PurePosixPath(filename)
        if not document.is_absolute() and ".." not in document.parts and int(line) > 0:
            diagnostics[document].append(BuildWarning(int(line), message))
    return {document: tuple(warnings) for document, warnings in diagnostics.items()}


def _resolve_include(target: str, *, parent: Path, source_root: Path) -> Path | None:
    """Resolve an include literally, without Sphinx document suffix inference."""
    relative = PurePosixPath(target)
    candidate = (
        source_root.joinpath(*relative.parts[1:])
        if relative.is_absolute()
        else parent / target
    ).resolve()
    if not candidate.is_relative_to(source_root.resolve()) or not candidate.is_file():
        return None
    return candidate


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
            included = _resolve_include(
                match.group("target"), parent=source.parent, source_root=source_root
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
) -> dict[int, frozenset[str]]:
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
    affected = {}
    for line in warning_lines:
        if line in occurrences:
            affected[line] = frozenset(occurrences[line])
        elif line in matches:
            longest = max(map(len, matches[line]))
            affected[line] = frozenset(
                message for message in matches[line] if len(message) == longest
            )
    return affected


def _catalog_message_locations(
    catalog: polib.POFile,
    source_root: Path,
    documents: Sequence[PurePosixPath],
    locations: Mapping[PurePosixPath, frozenset[int]],
) -> dict[tuple[PurePosixPath, int], frozenset[str]]:
    """Find candidates by source occurrences or matching source text.

    Catalogs can omit occurrences. Match their source messages with
    whitespace normalized, requiring the warning line to fall within the text
    or its heading adornments. Locations that could use an included fragment's
    line number with its parent's filename are ambiguous and are excluded.
    Prefer the longest match to avoid selecting a short message embedded in a
    longer one. Unmapped locations never invalidate an arbitrary nearby entry.
    """
    if not locations:
        return {}
    warned_sources = {}
    for source in _catalog_sources(source_root, documents):
        document = PurePosixPath(source.relative_to(source_root).as_posix())
        if document in locations:
            warned_sources[source] = document
    if not warned_sources:
        return {}
    affected = {}
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
            for line, messages in _source_messages(
                catalog, source, document, lines
            ).items():
                affected[document, line] = messages
    return affected


def _reference_identity(
    message: str,
) -> tuple[str | None, tuple[str, ...], tuple[str, ...]] | None:
    match = REFERENCES_RE.fullmatch(message)
    if match is None:
        return None
    try:
        original = ast.literal_eval(match.group("original"))
        translated = ast.literal_eval(match.group("translated"))
    except (SyntaxError, ValueError):
        return None
    if (
        not all(
            isinstance(refs, list) and all(isinstance(ref, str) for ref in refs)
            for refs in (original, translated)
        )
        or not original
    ):
        return None
    return (
        match.group("kind"),
        tuple(sorted(" ".join(ref.split()) for ref in original)),
        tuple(sorted(" ".join(ref.split()) for ref in translated)),
    )


def _docutils_messages(text: str, heading: bool) -> frozenset[str]:
    """Reparse a message as Sphinx does, collecting only local diagnostics."""
    if heading:
        text += "\n" + "=" * len(text) * 2
    document = publish_doctree(
        text,
        settings_overrides={
            "warning_stream": StringIO(),
            "report_level": 5,
            "halt_level": 6,
            "file_insertion_enabled": False,
            "raw_enabled": False,
        },
    )
    return frozenset(
        node.children[0].astext() for node in document.findall(nodes.system_message)
    )


def _reference_messages(
    catalog: polib.POFile,
    identity: tuple[str | None, tuple[str, ...], tuple[str, ...]],
) -> frozenset[str]:
    kind, original, translated = identity
    pattern = REFERENCE_PATTERNS[kind]

    def references(text: str) -> tuple[str, ...]:
        return tuple(sorted(" ".join(ref.split()) for ref in pattern.findall(text)))

    matching = [
        entry.msgid
        for entry in catalog.translated_entries()
        if references(entry.msgid) == original
        and references(entry.msgstr) == translated
    ]
    return frozenset(matching) if len(matching) == 1 else frozenset()


def _reproduces_finding(
    entry: polib.POEntry,
    heading: bool,
    finding: str,
    parsed: dict[tuple[str, bool], frozenset[str]],
) -> bool:
    for text in (entry.msgid, entry.msgstr):
        key = (text, heading)
        if key not in parsed:
            parsed[key] = _docutils_messages(text, heading)
    return (
        finding in parsed[entry.msgstr, heading]
        and finding not in parsed[entry.msgid, heading]
    )


def catalog_messages(
    path: Path,
    source_root: Path,
    documents: Sequence[PurePosixPath],
    diagnostics: Mapping[PurePosixPath, Sequence[BuildWarning]],
) -> frozenset[str]:
    """Count only candidates whose published diagnostic is verified locally.

    Published reports lack source revisions. References must uniquely identify
    a local source/translation pair; docutils findings must reproduce in the
    translation and be absent from the source. Locations alone are insufficient.
    """
    if not diagnostics:
        return frozenset()
    catalog = polib.pofile(str(path))
    locations = {
        document: frozenset(warning.line for warning in warnings)
        for document, warnings in diagnostics.items()
    }
    candidates = _catalog_message_locations(catalog, source_root, documents, locations)
    if not candidates:
        return frozenset()
    entries = {entry.msgid: entry for entry in catalog.translated_entries()}
    identities = {}
    parsed: dict[tuple[str, bool], frozenset[str]] = {}
    headings = {}
    affected: set[str] = set()
    for (document, line), messages in candidates.items():
        for warning in diagnostics[document]:
            if warning.line != line:
                continue
            identity = _reference_identity(warning.message)
            if identity is not None:
                if identity not in identities:
                    identities[identity] = _reference_messages(catalog, identity)
                affected.update(messages & identities[identity])
            elif warning.message.endswith(" [docutils]"):
                if document not in headings:
                    headings[document] = _heading_locations(
                        (source_root / document).read_text(encoding="utf-8")
                    )
                for message in messages & entries.keys():
                    entry = entries[message]
                    heading = headings[document].get(line) == " ".join(message.split())
                    finding = warning.message.removesuffix(" [docutils]")
                    if _reproduces_finding(entry, heading, finding, parsed):
                        affected.add(message)
    return frozenset(affected)
