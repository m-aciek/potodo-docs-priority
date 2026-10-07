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
