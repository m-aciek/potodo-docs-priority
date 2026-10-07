from io import BytesIO
from pathlib import PurePosixPath
from urllib.error import HTTPError, URLError

import polib
import pytest

from potodo_docs_priority import build_warnings, lint_warnings


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
        )

    monkeypatch.setattr(build_warnings, "urlopen", fetch)
    assert build_warnings.load_warnings("pt_BR") == {
        PurePosixPath("guide.rst"): frozenset({4, 9})
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
    messages = build_warnings.catalog_messages(
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
    assert build_warnings.catalog_messages(
        path,
        source,
        [PurePosixPath("index.rst")],
        {PurePosixPath("fragment.rst"): frozenset({1})},
    ) == {"Extracted message"}


@pytest.mark.parametrize("with_occurrences", [False, True])
def test_parent_locations_with_fragment_line_numbers_are_ambiguous(
    tmp_path, with_occurrences
):
    source = tmp_path / "source"
    source.mkdir()
    # An included translation can be reported as index.rst:3, even though
    # its source and line number belong to fragment.rst.
    (source / "index.rst").write_text(
        "\n\nInterpreter improvements:\n\n.. include:: fragment.rst\n"
        "\nDirect parent message\n",
        encoding="utf-8",
    )
    (source / "fragment.rst").write_text("\n\nIncluded message\n", encoding="utf-8")
    po = polib.POFile()
    for message, document, line in [
        ("Interpreter improvements:", "index.rst", "3"),
        ("Included message", "fragment.rst", "3"),
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
        build_warnings.catalog_messages(
            path,
            source,
            [PurePosixPath("index.rst")],
            {PurePosixPath("index.rst"): frozenset({3})},
        )
        == frozenset()
    )
    assert build_warnings.catalog_messages(
        path,
        source,
        [PurePosixPath("index.rst")],
        {
            PurePosixPath("index.rst"): frozenset({3, 7}),
            PurePosixPath("fragment.rst"): frozenset({3}),
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
    messages = build_warnings.catalog_messages(
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
    messages = build_warnings.catalog_messages(
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
        build_warnings.catalog_messages(
            path,
            source,
            [PurePosixPath("guide.rst")],
            {PurePosixPath("guide.rst"): frozenset({len(text.splitlines())})},
        )
        == frozenset()
    )
