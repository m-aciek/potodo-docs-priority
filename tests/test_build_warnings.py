from io import BytesIO
from pathlib import PurePosixPath
from urllib.error import HTTPError, URLError

import polib
import pytest

from potodo_docs_priority import build_warnings, lint_warnings
from potodo_docs_priority.build_warnings import (
    _catalog_message_locations,
    _catalog_sources,
)


def _mapped_messages(path, source, documents, locations):
    """Exercise candidate mapping with locations from the same source fixture."""
    candidates = _catalog_message_locations(
        polib.pofile(str(path)), source, documents, locations
    )
    return frozenset(
        message for messages in candidates.values() for message in messages
    )


def test_load_warnings_reads_source_lines_not_translation_offsets(monkeypatch):
    calls = []

    def fetch(url, timeout):
        calls.append((url, timeout))
        return BytesIO(
            b"guide.rst:4:<translated>:2: WARNING: invalid role [docutils]\n"
            b"guide.rst:4: WARNING: inconsistent references\n"
            b"guide.rst:9:<translated>:1: ERROR: unknown role\n"
            b"continued warning text\nWARNING: no source location\n"
            b"../outside.rst:1: WARNING: outside source root\n"
            b"/absolute.rst:1: WARNING: outside source root\n"
            b"guide.rst:0: WARNING: invalid line\n"
            b"library/token-list.inc:3: WARNING: fragment finding\n"
            b"includes/no-extension:2: WARNING: extensionless finding\n"
        )

    monkeypatch.setattr(build_warnings, "urlopen", fetch)
    assert build_warnings.load_warnings("pt_BR") == {
        PurePosixPath("guide.rst"): (
            build_warnings.BuildWarning(4, "invalid role [docutils]"),
            build_warnings.BuildWarning(4, "inconsistent references"),
            build_warnings.BuildWarning(9, "unknown role"),
        ),
        PurePosixPath("library/token-list.inc"): (
            build_warnings.BuildWarning(3, "fragment finding"),
        ),
        PurePosixPath("includes/no-extension"): (
            build_warnings.BuildWarning(2, "extensionless finding"),
        ),
    }
    assert calls == [("https://translations.python.org/warnings-pt-br.txt", 30)]


@pytest.mark.parametrize("status", [404, 500])
def test_only_missing_build_report_is_treated_as_no_warnings(monkeypatch, status):
    def fetch(*args, **kwargs):
        raise HTTPError("https://example.test", status, "Failure", {}, None)

    monkeypatch.setattr(build_warnings, "urlopen", fetch)
    if status == 404:
        assert build_warnings.load_warnings("pl") == {}
    else:
        with pytest.raises(HTTPError):
            build_warnings.load_warnings("pl")


def test_build_report_network_failure_propagates(monkeypatch):
    def fetch(*args, **kwargs):
        raise URLError("Offline")

    monkeypatch.setattr(build_warnings, "urlopen", fetch)
    with pytest.raises(URLError):
        build_warnings.load_warnings("pl")


def test_source_mapping_without_po_occurrences_and_union_with_lint(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "guide.rst").write_text(
        "Guide\n=====\n\n* Broken three\n  source words\n\nClean one\n"
        "\nFuzzy\n\nEmpty\n",
        encoding="utf-8",
    )
    po = polib.POFile()
    po.append(
        polib.POEntry(msgid="Broken three source words", msgstr=":func:`print`tekst ")
    )
    po.append(polib.POEntry(msgid="Clean one", msgstr="Poprawny tekst"))
    # A shorter message on another line must not be selected inside this one.
    po.append(polib.POEntry(msgid="source words", msgstr="Inne tłumaczenie"))
    po.append(polib.POEntry(msgid="Fuzzy", msgstr="Tekst", flags=["fuzzy"]))
    po.append(polib.POEntry(msgid="Empty", msgstr=""))
    po.append(polib.POEntry(msgid="Guide", msgstr="Stary tytuł", obsolete=True))
    path = tmp_path / "guide.po"
    po.save(str(path))
    messages = _mapped_messages(
        path,
        source,
        [PurePosixPath("guide.rst")],
        {PurePosixPath("guide.rst"): frozenset({1, 4, 5, 7, 9, 11, 999})},
    )
    assert messages == {"Broken three source words", "Clean one", "Fuzzy", "Empty"}
    # The four-word entry has both lint and build findings, but counts once.
    result = lint_warnings.check_catalog(path, build_messages=messages)
    assert result.incomplete_words == 6
    assert result.warnings


def test_source_occurrences_and_included_fragments(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.rst").write_text(".. include:: fragment.rst\n")
    (source / "fragment.rst").write_text(".. special:: content\n")
    po = polib.POFile()
    po.append(
        polib.POEntry(
            msgid="Extracted message",
            msgstr="Tłumaczenie",
            occurrences=[("../../fragment.rst", "1")],
        )
    )
    po.append(
        polib.POEntry(
            msgid="Message in another directory",
            msgstr="Inne tłumaczenie",
            occurrences=[("../../unrelated/fragment.rst", "1")],
        )
    )
    path = tmp_path / "index.po"
    po.save(str(path))
    assert _mapped_messages(
        path,
        source,
        [PurePosixPath("index.rst")],
        {PurePosixPath("fragment.rst"): frozenset({1})},
    ) == {"Extracted message"}


@pytest.mark.parametrize("with_occurrences", [False, True])
@pytest.mark.parametrize("fragment", ["fragment.rst", "token-list.inc", "no-extension"])
def test_parent_locations_with_fragment_line_numbers_are_ambiguous(
    tmp_path, with_occurrences, fragment
):
    source = tmp_path / "source"
    source.mkdir()
    # An included translation can be reported as index.rst:3, even though
    # its source and line number belong to fragment.rst.
    (source / "index.rst").write_text(
        f"\n\nInterpreter improvements:\n\n.. include:: {fragment}\n"
        "\nDirect parent message\n",
        encoding="utf-8",
    )
    (source / fragment).write_text("\n\nIncluded message\n", encoding="utf-8")
    po = polib.POFile()
    for message, document, line in [
        ("Interpreter improvements:", "index.rst", "3"),
        ("Included message", fragment, "3"),
        ("Direct parent message", "index.rst", "7"),
    ]:
        po.append(
            polib.POEntry(
                msgid=message,
                msgstr="Poprawne tłumaczenie",
                occurrences=[(document, line)] if with_occurrences else [],
            )
        )
    path = tmp_path / "index.po"
    po.save(str(path))

    assert (
        _mapped_messages(
            path,
            source,
            [PurePosixPath("index.rst")],
            {PurePosixPath("index.rst"): frozenset({3})},
        )
        == frozenset()
    )
    assert _mapped_messages(
        path,
        source,
        [PurePosixPath("index.rst")],
        {
            PurePosixPath("index.rst"): frozenset({3, 7}),
            PurePosixPath(fragment): frozenset({3}),
        },
    ) == {"Included message", "Direct parent message"}


def test_ambiguity_includes_nested_fragments_and_is_local_to_each_document(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.rst").write_text(
        ".. include:: fragment.rst\n\n\n\nClean parent\n", encoding="utf-8"
    )
    (source / "fragment.rst").write_text(".. include:: nested.rst\n")
    # The cycle must not make source discovery loop forever.
    (source / "nested.rst").write_text(
        ".. include:: index.rst\n\n\n\nIncluded message\n", encoding="utf-8"
    )
    (source / "other.rst").write_text("\n\n\n\nOther message\n", encoding="utf-8")
    po = polib.POFile()
    for message in ("Clean parent", "Included message", "Other message"):
        po.append(polib.POEntry(msgid=message, msgstr="Tłumaczenie"))
    path = tmp_path / "compact.po"
    po.save(str(path))
    messages = _mapped_messages(
        path,
        source,
        [PurePosixPath("index.rst"), PurePosixPath("other.rst")],
        {
            PurePosixPath("index.rst"): frozenset({5}),
            PurePosixPath("other.rst"): frozenset({5}),
        },
    )
    assert messages == {"Other message"}


@pytest.mark.parametrize(
    ("heading", "warning_line"),
    [
        ("Allocating objects on the heap\n==============================", 5),
        ("Allocating objects on the heap\n==============================", 6),
        (
            "******************************\nAllocating objects on the heap\n"
            "******************************",
            5,
        ),
        (
            "******************************\nAllocating objects on the heap\n"
            "******************************",
            7,
        ),
    ],
)
def test_heading_adornment_locations_without_occurrences(
    tmp_path, heading, warning_line
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "allocation.rst").write_text(
        ".. highlight:: c\n\n.. _allocating-objects:\n\n" + heading + "\n",
        encoding="utf-8",
    )
    po = polib.POFile()
    po.append(
        polib.POEntry(
            msgid="Allocating objects on the heap",
            msgstr="Alokowanie obiektów na stercie",
        )
    )
    # A separate message must not match a substring of the heading.
    po.append(polib.POEntry(msgid="objects on the heap", msgstr="Inny tekst"))
    path = tmp_path / "allocation.po"
    po.save(str(path))
    messages = _mapped_messages(
        path,
        source,
        [PurePosixPath("allocation.rst")],
        {PurePosixPath("allocation.rst"): frozenset({warning_line})},
    )
    assert messages == {"Allocating objects on the heap"}
    result = lint_warnings.check_catalog(path, build_messages=messages)
    assert result.incomplete_words == 5
    assert not result.warnings


@pytest.mark.parametrize(
    "text",
    [
        "Objects on the heap\n====\n",
        "Example::\n\n    Objects on the heap\n    ===================\n",
        "Objects on the heap\nordinary next line\n",
        "*******************\nObjects on the heap\n===================\n",
    ],
)
def test_non_heading_lines_do_not_map_to_nearby_messages(tmp_path, text):
    source = tmp_path / "source"
    source.mkdir()
    (source / "guide.rst").write_text(text, encoding="utf-8")
    po = polib.POFile()
    po.append(polib.POEntry(msgid="Objects on the heap", msgstr="Obiekty na stercie"))
    path = tmp_path / "guide.po"
    po.save(str(path))
    assert (
        _mapped_messages(
            path,
            source,
            [PurePosixPath("guide.rst")],
            {PurePosixPath("guide.rst"): frozenset({len(text.splitlines())})},
        )
        == frozenset()
    )


def _write_source_catalog(tmp_path, text, entries):
    source = tmp_path / "source"
    source.mkdir()
    (source / "guide.rst").write_text(text, encoding="utf-8")
    catalog = polib.POFile()
    catalog.extend(
        polib.POEntry(msgid=msgid, msgstr=msgstr) for msgid, msgstr in entries
    )
    path = tmp_path / "guide.po"
    catalog.save(str(path))
    return source, path


REFERENCE_WARNING = (
    "inconsistent term references in translated message. "
    "original: [':meth:`assertAlmostEqual`', ':meth:`assertNotAlmostEqual`'], "
    "translated: [':meth:`assertAlmostEqual`', ':meth:`assertNotNotAlmostEqual`'] "
    "[i18n.inconsistent_references]"
)


@pytest.mark.parametrize("with_occurrences", [False, True])
def test_published_source_drift_does_not_invalidate_an_unrelated_translation(
    tmp_path, monkeypatch, with_occurrences
):
    clean = (
        "Test that *first* and *second* are approximately equal using :func:`round`."
    )
    faulty = (
        ":meth:`assertAlmostEqual` and :meth:`assertNotAlmostEqual` compare objects."
    )
    translation = ":meth:`assertAlmostEqual` et :meth:`assertNotNotAlmostEqual`"
    source, path = _write_source_catalog(
        tmp_path,
        f"\n\n\n{clean}\n\n\n{faulty}\n",
        [(clean, "Texte correct"), (faulty, translation)],
    )
    if with_occurrences:
        catalog = polib.pofile(str(path))
        catalog[0].occurrences = [("guide.rst", "4")]
        catalog[1].occurrences = [("guide.rst", "7")]
        catalog.save(str(path))
    report = f"guide.rst:4: WARNING: {REFERENCE_WARNING}\n"
    monkeypatch.setattr(
        build_warnings, "urlopen", lambda *args, **kwargs: BytesIO(report.encode())
    )
    messages = build_warnings.catalog_messages(
        path, source, [PurePosixPath("guide.rst")], build_warnings.load_warnings("fr")
    )
    assert not messages
    assert (
        lint_warnings.check_catalog(path, build_messages=messages).incomplete_words == 0
    )
    # The same diagnostic at a compatible source location still counts.
    messages = build_warnings.catalog_messages(
        path,
        source,
        [PurePosixPath("guide.rst")],
        {
            PurePosixPath("guide.rst"): (
                build_warnings.BuildWarning(7, REFERENCE_WARNING),
            )
        },
    )
    assert messages == {faulty}


@pytest.mark.parametrize("case", ["fixed", "ambiguous", "extra-reference"])
def test_reference_identity_must_match_the_current_catalog_uniquely(tmp_path, case):
    original = "Use :meth:`assertAlmostEqual` and :meth:`assertNotAlmostEqual`."
    translation = ":meth:`assertAlmostEqual` et :meth:`assertNotNotAlmostEqual`"
    if case == "fixed":
        translation = ":meth:`assertAlmostEqual` et :meth:`assertNotAlmostEqual`"
    if case == "extra-reference":
        translation += " :func:`round`"
    entries = [(original, translation)]
    if case == "ambiguous":
        entries.append((original + " Another paragraph.", translation))
    source, path = _write_source_catalog(tmp_path, original + "\n", entries)
    assert not build_warnings.catalog_messages(
        path,
        source,
        [PurePosixPath("guide.rst")],
        {
            PurePosixPath("guide.rst"): (
                build_warnings.BuildWarning(1, REFERENCE_WARNING),
            )
        },
    )


@pytest.mark.parametrize(
    "message",
    [
        "WARNING without message identity",
        "a different diagnostic [docutils]",
        "inconsistent term references in translated message. original: invalid, "
        "translated: [] [i18n.inconsistent_references]",
        "inconsistent term references in translated message. original: [1], "
        "translated: [] [i18n.inconsistent_references]",
    ],
)
def test_unverifiable_published_diagnostics_do_not_reduce_completion(tmp_path, message):
    source, path = _write_source_catalog(
        tmp_path, "Clean message\n", [("Clean message", "Texte correct")]
    )
    assert not build_warnings.catalog_messages(
        path,
        source,
        [PurePosixPath("guide.rst")],
        {PurePosixPath("guide.rst"): (build_warnings.BuildWarning(1, message),)},
    )


@pytest.mark.parametrize("heading", [False, True])
def test_published_docutils_findings_are_reproduced_locally(tmp_path, heading):
    msgid = "Allocating objects on the heap" if heading else "Token"
    msgstr = " Alokowanie obiektów na stercie" if heading else "``Znacznik"
    text = msgid + ("\n" + "=" * len(msgid) if heading else "") + "\n"
    source, path = _write_source_catalog(tmp_path, text, [(msgid, msgstr)])
    warning = (
        "Block quote ends without a blank line; unexpected unindent."
        if heading
        else "Inline literal start-string without end-string."
    )
    diagnostics = {
        PurePosixPath("guide.rst"): (
            build_warnings.BuildWarning(2 if heading else 1, warning + " [docutils]"),
        )
    }
    messages = build_warnings.catalog_messages(
        path, source, [PurePosixPath("guide.rst")], diagnostics
    )
    assert messages == {msgid}
    assert lint_warnings.check_catalog(
        path, build_messages=messages
    ).incomplete_words == len(msgid.split())
    # A report for an older translation no longer deducts its words.
    catalog = polib.pofile(str(path))
    catalog[0].msgstr = "Texte correct"
    catalog.save(str(path))
    assert not build_warnings.catalog_messages(
        path, source, [PurePosixPath("guide.rst")], diagnostics
    )


def test_docutils_findings_already_present_in_source_are_not_translation_defects(
    tmp_path,
):
    source, path = _write_source_catalog(
        tmp_path, "``Unclosed\n", [("``Unclosed", "``Niepoprawne")]
    )
    assert not build_warnings.catalog_messages(
        path,
        source,
        [PurePosixPath("guide.rst")],
        {
            PurePosixPath("guide.rst"): (
                build_warnings.BuildWarning(
                    1, "Inline literal start-string without end-string. [docutils]"
                ),
            )
        },
    )


@pytest.mark.parametrize("with_occurrences", [False, True])
def test_published_diagnostics_in_non_rst_includes(
    tmp_path, monkeypatch, with_occurrences
):
    source, path = _write_source_catalog(
        tmp_path,
        ".. include:: /token-list.inc\n",
        [("Token", "``Znacznik"), ("Value", "``Wartość")],
    )
    (source / "token-list.inc").write_text(
        "\n\nToken\n\n.. include:: includes/no-extension\n", encoding="utf-8"
    )
    (source / "includes").mkdir()
    (source / "includes/no-extension").write_text("Value\n", encoding="utf-8")
    if with_occurrences:
        catalog = polib.pofile(str(path))
        catalog[0].occurrences = [("../../token-list.inc", "3")]
        catalog[1].occurrences = [("../../includes/no-extension", "1")]
        catalog.save(str(path))
    finding = "Inline literal start-string without end-string. [docutils]"
    report = (
        f"token-list.inc:3:<translated>:1: WARNING: {finding}\n"
        f"includes/no-extension:1:<translated>:1: WARNING: {finding}\n"
    )
    monkeypatch.setattr(
        build_warnings, "urlopen", lambda *args, **kwargs: BytesIO(report.encode())
    )
    assert build_warnings.catalog_messages(
        path, source, [PurePosixPath("guide.rst")], build_warnings.load_warnings("pl")
    ) == {"Token", "Value"}


def test_include_resolution_does_not_infer_suffixes_or_escape_source_root(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "guide.rst").write_text(
        ".. include:: missing\n\n.. include:: ../outside.inc\n", encoding="utf-8"
    )
    (source / "missing.rst").write_text("Unrelated file\n", encoding="utf-8")
    (tmp_path / "outside.inc").write_text("Outside source root\n", encoding="utf-8")
    assert _catalog_sources(source, [PurePosixPath("guide.rst")]) == {
        source / "guide.rst"
    }
