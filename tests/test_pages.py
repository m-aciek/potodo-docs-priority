import json
import subprocess
from io import BytesIO
from pathlib import Path

import pytest

from potodo_docs_priority import pages
from potodo_docs_priority.html_report import HtmlSection, transifex_url
from potodo_docs_priority.languages import Language, get_languages, locale_directory


def write_devguide(root):
    path = root / "documentation/translations/translating.rst"
    path.parent.mkdir(parents=True)
    path.write_text(
        "Translations\n============\n\n.. list-table::\n   :header-rows: 1\n\n"
        "   * - Language\n     - Coordination team\n     - Links\n"
        "   * - `French (fr) <https://docs.python.org/fr/>`__\n     - Team\n"
        "     - `Original <https://git.afpy.org/AFPy/python-docs-fr/>`__,\n"
        "       :github:`mirror <python/python-docs-fr>`\n"
        "   * - Brazilian Portuguese (pt_BR)\n     - Team\n"
        "     - :github:`GitHub <python/python-docs-pt-br>`\n"
        "   * - Punjabi (pa)\n     - Team\n"
        "     - :github:`GitHub <someone/python-docs-pa>`, `Transifex <tx_>`_\n"
        "   * - Lithuanian (lt)\n     - Team\n     - Announcement only\n"
        "   * - New language (xx)\n     - Team\n"
        "     - :github:`GitHub <someone/python-docs-xx>`, `Transifex <tx_>`_\n",
        encoding="utf-8",
    )


def test_devguide_discovery(tmp_path):
    write_devguide(tmp_path)
    assert get_languages(tmp_path) == (
        Language("fr", "French", "python/python-docs-fr", False),
        Language("pt-br", "Brazilian Portuguese", "python/python-docs-pt-br", True),
        Language("pa", "Punjabi", "someone/python-docs-pa", True),
        Language("lt", "Lithuanian", None, False),
        Language("xx", "New language", "someone/python-docs-xx", True),
    )


def test_devguide_requires_language_table(tmp_path):
    write_devguide(tmp_path)
    path = tmp_path / "documentation/translations/translating.rst"
    path.write_text("No languages here\n")
    with pytest.raises(ValueError, match="No translation languages"):
        get_languages(tmp_path)


@pytest.mark.parametrize(
    "code,locale",
    [
        ("pt-br", "pt_BR"),
        ("zh-cn", "zh_Hans"),
        ("zh-tw", "zh_Hant"),
        ("it", "it_IT"),
        ("hi-in", "hi"),
        ("bn-in", "bn_IN"),
    ],
)
def test_gettext_locale_resolution(tmp_path, code, locale):
    messages = tmp_path / locale / "LC_MESSAGES"
    messages.mkdir(parents=True)
    (messages / "messages.po").write_text('msgid "Title"\nmsgstr ""\n')
    assert locale_directory(tmp_path, code) == messages.parent
    assert locale_directory(tmp_path, "missing") is None


def test_language_sections_use_only_matching_locales(tmp_path, monkeypatch):
    for root in ("packaging-translations", "sphinx-doc-translations"):
        messages = tmp_path / root / "locales" / "pt_BR" / "LC_MESSAGES"
        messages.mkdir(parents=True)
        (messages / "messages.po").write_text('msgid "Title"\nmsgstr ""\n')
    calls = []

    def build(**kwargs):
        calls.append(kwargs)
        return (HtmlSection(kwargs["projects"][0], "Docs", ()),)

    monkeypatch.setattr(pages, "build_sections", build)
    assert (
        pages.language_sections(
            Language("lt", "Lithuanian", None, False), tmp_path, None
        )
        == ()
    )
    assert calls == []
    sections = pages.language_sections(
        Language("pt-br", "Portuguese", None, True), tmp_path, None
    )
    assert [section.project for section in sections] == ["packaging", "sphinx"]
    assert [call["language"] for call in calls] == ["pt_BR", "pt_BR"]
    assert (
        calls[0]["packaging_translations"]
        == tmp_path / "packaging-translations/locales/pt_BR/LC_MESSAGES"
    )
    assert (
        calls[1]["sphinx_translations"]
        == tmp_path / "sphinx-doc-translations/locales/pt_BR/LC_MESSAGES"
    )


def test_transifex_regional_codes():
    assert "#pt_BR/" in transifex_url("cpython", "pt-br", "tutorial--index")
    assert "#zh_CN/" in transifex_url("cpython", "zh-cn", "tutorial--index")


def test_release_branches_filter_and_sort(monkeypatch):
    data = {
        "3.9": {"status": "end-of-life"},
        "3.14": {"status": "bugfix"},
        "3.16": {"status": "feature"},
        "3.10": {"status": "security"},
        "3.15": {"status": "prerelease"},
    }
    monkeypatch.setattr(
        pages, "urlopen", lambda *args, **kwargs: BytesIO(json.dumps(data).encode())
    )
    assert pages.release_branches() == ("3.15", "3.14", "3.10", "master", "main")


@pytest.mark.parametrize(
    "available,expected",
    [
        (("3.14", "main"), "3.14"),
        (("master", "main"), "master"),
        (("main",), "main"),
    ],
)
def test_checkout_selects_available_branch(tmp_path, monkeypatch, available, expected):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0, "".join(f"abc\trefs/heads/{branch}\n" for branch in available)
        )

    monkeypatch.setattr(pages.subprocess, "run", run)
    destination = tmp_path / "translations"
    assert (
        pages.checkout_translations(
            "python/python-docs-fr", destination, ("3.15", "3.14", "master", "main")
        )
        == destination
    )
    assert calls[-1] == [
        "git",
        "clone",
        "--depth",
        "1",
        "--branch",
        expected,
        "https://github.com/python/python-docs-fr.git",
        str(destination),
    ]


def test_checkout_failure_does_not_publish_incomplete_site(tmp_path, monkeypatch):
    def run(command, **kwargs):
        raise subprocess.CalledProcessError(128, command)

    monkeypatch.setattr(pages.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        pages.checkout_translations("python/python-docs-pl", tmp_path, ("main",))


def test_multilingual_builder_writes_languages_and_navigation(tmp_path, monkeypatch):
    write_devguide(tmp_path / "devguide")
    output = tmp_path / "site"
    monkeypatch.setattr(pages, "release_branches", lambda: ("main",))
    monkeypatch.setattr(pages, "checkout_translations", lambda *args: tmp_path)
    monkeypatch.setattr(
        pages,
        "language_sections",
        lambda language, *args: (
            (HtmlSection("cpython", "CPython", ()), HtmlSection("sphinx", "Sphinx", ()))
            if language.repository
            else ()
        ),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["pages", "--devguide", str(tmp_path / "devguide"), "--output", str(output)],
    )
    pages.main()
    index = (output / "index.html").read_text()
    for code in ("fr", "pt-br", "pa", "lt", "xx"):
        assert f'href="{code}/index.html"' in index
        assert (output / code / "index.html").is_file()
    assert "No translation catalogs available" in index
    assert (
        '<a href="../index.html">Languages</a>'
        in (output / "fr/sphinx.html").read_text()
    )
    assert "No translation catalogs available" in (output / "lt/index.html").read_text()
    assert (
        '<a href="../index.html">Languages</a>'
        in (output / "lt/index.html").read_text()
    )


def test_unknown_language_fails_before_fetching(tmp_path, monkeypatch):
    write_devguide(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "pages",
            "--devguide",
            str(tmp_path),
            "--output",
            str(tmp_path / "site"),
            "--languages",
            "missing",
        ],
    )
    with pytest.raises(ValueError, match="Languages not in devguide"):
        pages.main()


def test_invalid_upstream_catalog_keeps_other_reports(tmp_path, caplog):
    translations = tmp_path / "translations"
    translations.mkdir()
    (translations / "contents.po").write_text('msgstr "Broken"\n')
    source = tmp_path / "cpython" / "Doc"
    source.mkdir(parents=True)
    (source / "contents.rst").write_text("Docs\n====\n")
    sphinx = tmp_path / "sphinx" / "doc"
    sphinx.mkdir(parents=True)
    (sphinx / "index.rst").write_text("Sphinx\n======\n")
    locale = tmp_path / "sphinx-doc-translations" / "locales" / "it_IT" / "LC_MESSAGES"
    locale.mkdir(parents=True)
    (locale / "index.po").write_text('msgid "Sphinx"\nmsgstr ""\n')
    stats = tmp_path / "plausible-stats" / "stats" / "docs.python.org_2026-01-01"
    stats.mkdir(parents=True)
    (stats / "docs.python.org_2026-01-01.prefix-3.pages.json").write_text("[]")
    language = Language("it", "Italian", "python/python-docs-it", False)
    sections = pages.language_sections(language, tmp_path, translations)
    assert len(sections) == 2
    assert sections[0].notice is not None
    assert sections[1].notice is None
    assert len(sections[1].items) == 1
    assert "Syntax error in po file" in caplog.text
    output = tmp_path / "site"
    pages.write_pages(
        output / "it", sections, "Priorities", language_index="../index.html"
    )
    pages.write_language_index(output, [(language, sections)], "Priorities")
    assert "cpython (unavailable), sphinx" in (output / "index.html").read_text()
    report = (output / "it/index.html").read_text()
    assert "This report is unavailable" in report
    assert 'href="sphinx.html"' in report
    assert '<form id="weights"' not in report


def test_missing_stats_still_fails_the_build(tmp_path):
    translations = tmp_path / "translations"
    translations.mkdir()
    (translations / "contents.po").write_text('msgid "Docs"\nmsgstr ""\n')
    source = tmp_path / "cpython" / "Doc"
    source.mkdir(parents=True)
    (source / "contents.rst").write_text("Docs\n====\n")
    with pytest.raises(FileNotFoundError):
        pages.language_sections(
            Language("it", "Italian", "python/python-docs-it", False),
            tmp_path,
            translations,
        )
