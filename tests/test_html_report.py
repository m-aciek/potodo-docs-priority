import json
from pathlib import Path

import polib
import pytest

from potodo_docs_priority.html_cli import parse_args, write_pages
from potodo_docs_priority.html_report import (
    HtmlItem,
    HtmlMetrics,
    HtmlSection,
    build_sections,
    render_html,
    render_index,
    rst_title,
    transifex_resources,
    transifex_url,
    translated_title,
    weblate_checksum,
    weblate_url,
)


def _write_snapshot(stats_dir, site, prefix, rows):
    date = "2026-01-01"
    snapshot_dir = stats_dir / "stats" / f"{site}_{date}"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / f"{site}_{date}.prefix-{prefix}.pages.json"
    path.write_text(json.dumps(rows), encoding="utf-8")


def test_rst_title_supports_overline_and_underline(tmp_path):
    path = tmp_path / "index.rst"
    path.write_text(".. label\n\n********\nGlossary\n********\n", encoding="utf-8")
    assert rst_title(path) == "Glossary"


def test_translated_title_passes_string_filename_to_polib(tmp_path, monkeypatch):
    po_path = tmp_path / "guide.po"
    po_path.write_text('msgid "Guide"\nmsgstr "Przewodnik"\n', encoding="utf-8")
    real_pofile = __import__("polib").pofile

    def checked_pofile(filename):
        assert isinstance(filename, str)
        return real_pofile(filename)

    monkeypatch.setattr("potodo_docs_priority.html_report.polib.pofile", checked_pofile)
    assert translated_title(po_path, "Guide") == "Przewodnik"


def test_transifex_resources_reads_exact_slug(tmp_path):
    config = tmp_path / ".tx" / "config"
    config.parent.mkdir()
    config.write_text(
        "[main]\nhost = https://www.transifex.com\n\n"
        "[o:python-doc:p:python-newest:r:glossary_]\n"
        "file_filter = glossary.po\nresource_name = glossary_\n",
        encoding="utf-8",
    )
    assert transifex_resources(tmp_path) == {"glossary.po": "glossary_"}
    assert transifex_url("cpython", "pl", "glossary_") == (
        "https://app.transifex.com/python-doc/python-newest/translate/#pl/glossary_"
    )
    assert transifex_url("sphinx", "pl_PL", "usage--quickstart") == (
        "https://app.transifex.com/sphinx-doc/sphinx-doc/translate/"
        "#pl_PL/usage--quickstart"
    )


def test_weblate_checksums_match_hosted_packaging_units():
    assert weblate_checksum("Overview of Python Packaging") == "0142df4ee5f670f4"
    assert weblate_checksum("The Packaging Flow") == "436b0e72ca60175e"


def test_weblate_url_omits_unfinished_filter_for_translated_unit():
    translated = polib.POEntry(msgid="Title", msgstr="Tytuł")
    untranslated = polib.POEntry(msgid="Title", msgstr="")
    assert "q=" not in weblate_url("pl", translated)
    assert "q=state%3A%3Ctranslated" in weblate_url("pl", untranslated)


def test_render_html_escapes_titles_and_links():
    section = HtmlSection(
        project="docs",
        title="Docs & translations",
        items=(
            HtmlItem(
                resource="guide/index",
                title='Use <tools> & "build"',
                url="https://example.test/?a=1&b=2",
                metrics=HtmlMetrics(
                    priority=90,
                    completion=25.5,
                    original_visitors=1234,
                    translated_visitors=12,
                    document_score=(1, 2),
                ),
            ),
        ),
    )
    output = render_html(section, "Top <ten>", (section,))
    assert "body { font-family: sans-serif; }" in output
    assert "<title>Docs &amp; translations – Top &lt;ten&gt;</title>" in output
    assert "<h1>Docs &amp; translations</h1>" in output
    assert "<nav>" not in output
    assert "Use &lt;tools&gt; &amp; &quot;build&quot;" in output
    assert "?a=1&amp;b=2" in output
    assert '<table aria-label="Translation priorities">' in output
    assert '<th class="metric-column" scope="col">Completion</th>' in output
    assert '<td class="priority">90.00</td>' in output
    assert '<td class="metric-column completion"' in output and ">25.50%</td>" in output
    assert (
        '<td class="metric-column original_popularity"' in output
        and ">1,234</td>" in output
    )
    assert (
        '<td class="metric-column translated_popularity"' in output
        and ">12</td>" in output
    )
    assert '<td class="metric-column navigation"' in output and ">1.2</td>" in output
    assert 'class="metric-column completion"' in output
    assert "background-image: linear-gradient(to right" in output
    assert "width: 10rem" in output
    assert "metric-hint" not in output
    assert "<progress" not in output


def test_write_pages_creates_an_index_and_one_page_per_project(tmp_path):
    sections = (
        HtmlSection(project="cpython", title="CPython", items=()),
        HtmlSection(project="sphinx", title="Sphinx", items=()),
    )

    paths = write_pages(tmp_path / "site", sections, "Translation priorities")

    assert [path.name for path in paths] == [
        "index.html",
        "sphinx.html",
    ]
    index = paths[0].read_text(encoding="utf-8")
    assert "<h1>CPython</h1>" in index
    assert '<a href="index.html">CPython</a>' in index
    assert '<a href="sphinx.html">Sphinx</a>' in index
    assert "<h1>Sphinx</h1>" in paths[1].read_text(encoding="utf-8")


def test_write_pages_uses_link_index_when_cpython_is_excluded(tmp_path):
    sections = (HtmlSection(project="sphinx", title="Sphinx", items=()),)

    paths = write_pages(tmp_path / "site", sections, "Translation priorities")

    assert [path.name for path in paths] == ["index.html", "sphinx.html"]
    index = paths[0].read_text(encoding="utf-8")
    assert "<h1>Translation priorities</h1>" in index
    assert '<a href="sphinx.html">Sphinx</a>' in index


def test_title_override_option_is_removed():
    with pytest.raises(SystemExit):
        parse_args(
            [
                "--output",
                "site",
                "--language",
                "pl",
                "--sphinx-language",
                "pl_PL",
                "--title-override",
                "cpython:sphinx=templates",
            ]
        )


def test_render_index_escapes_title():
    output = render_index(
        (HtmlSection(project="cpython", title="CPython & docs", items=()),),
        "Top <ten>",
    )
    assert "<h1>Top &lt;ten&gt;</h1>" in output
    assert '<a href="index.html">CPython &amp; docs</a>' in output


def test_table_omits_unavailable_popularity():
    item = HtmlItem(
        resource="index",
        title="Sphinx",
        url="https://example.test/",
        metrics=HtmlMetrics(priority=99.45, completion=0, document_score=(0,)),
    )
    output = render_html(HtmlSection("sphinx", "Sphinx", (item,)), "Priorities")
    assert '<td class="metric-column completion"' in output and ">0.00%</td>" in output
    assert '<td class="metric-column navigation"' in output and ">0</td>" in output
    assert "Original visitors" not in output
    assert "Translated visitors" not in output


@pytest.mark.parametrize("limit", [None, 2])
def test_packaging_section_splits_compact_catalog_by_document(tmp_path, limit):
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.rst").write_text(
        "Guide\n=====\n\n.. toctree::\n\n   overview\n   flow\n   finished\n",
        encoding="utf-8",
    )
    (source / "overview.rst").write_text(
        "Overview of Python Packaging\n============================\n",
        encoding="utf-8",
    )
    (source / "flow.rst").write_text(
        "The Packaging Flow\n==================\n", encoding="utf-8"
    )
    (source / "finished.rst").write_text("Finished\n========\n", encoding="utf-8")
    messages = tmp_path / "locales" / "pl" / "LC_MESSAGES" / "messages.po"
    messages.parent.mkdir(parents=True)
    messages.write_text(
        '#: ../source/index.rst:1\nmsgid "Guide"\nmsgstr ""\n\n'
        '#: ../source/overview.rst:1\nmsgid "Overview of Python Packaging"\n'
        'msgstr "Przegląd pakowania w Pythonie"\n\n'
        '#: ../source/overview.rst:3\nmsgid "Translate this overview"\nmsgstr ""\n\n'
        '#: ../source/flow.rst:1\nmsgid "The Packaging Flow"\nmsgstr ""\n\n'
        '#: ../source/finished.rst:1\nmsgid "Finished"\nmsgstr "Gotowe"\n',
        encoding="utf-8",
    )
    _write_snapshot(
        tmp_path,
        "packaging.python.org",
        "en",
        [
            {"name": "/en/latest/", "visitors": "30"},
            {"name": "/en/latest/overview/", "visitors": "20"},
            {"name": "/en/latest/flow/", "visitors": "10"},
        ],
    )
    _write_snapshot(
        tmp_path,
        "packaging.python.org",
        "pl",
        [{"name": "/pl/latest/", "visitors": "3"}],
    )
    sections = build_sections(
        projects=["packaging"],
        language="pl",
        sphinx_language="pl_PL",
        plausible_stats=tmp_path,
        cpython_source=Path("unused"),
        cpython_translations=Path("unused"),
        packaging_source=source,
        packaging_translations=tmp_path / "locales",
        sphinx_source=Path("unused"),
        sphinx_translations=Path("unused"),
        snapshots=30,
        docs_version="3",
        limit=limit,
    )
    expected = ["index", "overview", "flow"][:limit]
    assert [item.resource for item in sections[0].items] == expected
    assert sections[0].items[0].metrics.ranks["original_popularity"] == 1
    assert sections[0].items[1].title == "Przegląd pakowania w Pythonie"
    assert (
        sections[0]
        .items[1]
        .url.endswith("?checksum=" + weblate_checksum("Overview of Python Packaging"))
    )


def test_html_defaults_to_all_unfinished_resources(tmp_path):
    args = parse_args(
        ["--output", "site", "--language", "pl", "--sphinx-language", "pl_PL"]
    )
    assert args.limit is None
    source = tmp_path / "source"
    source.mkdir()
    translations = tmp_path / "translations"
    translations.mkdir()
    for index in range(13):
        name = "index" if index == 0 else f"page{index}"
        (source / f"{name}.rst").write_text("Title\n=====\n", encoding="utf-8")
        po = polib.POFile()
        po.append(polib.POEntry(msgid="Title", msgstr="Tytuł" if index == 12 else ""))
        po.save(str(translations / f"{name}.po"))
    sections = build_sections(
        projects=["sphinx"],
        language="pl",
        sphinx_language="pl_PL",
        plausible_stats=tmp_path,
        cpython_source=tmp_path,
        cpython_translations=tmp_path,
        packaging_source=tmp_path,
        packaging_translations=tmp_path,
        sphinx_source=source,
        sphinx_translations=translations,
        snapshots=30,
        docs_version="3",
    )
    assert len(sections[0].items) == 12
    assert "page12" not in {item.resource for item in sections[0].items}
    output = render_html(sections[0], "Priorities")
    assert output.count("<tr data-resource=") == 12
    assert 'data-metric="navigation" min="0" step="any" required value="100"' in output
    assert 'data-metric="original_popularity"' not in output


def test_packaging_section_explains_missing_translation_branch(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.rst").write_text("Guide\n=====\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="translation/source branch"):
        build_sections(
            projects=["packaging"],
            language="pl",
            sphinx_language="pl_PL",
            plausible_stats=tmp_path,
            cpython_source=Path("unused"),
            cpython_translations=Path("unused"),
            packaging_source=source,
            packaging_translations=tmp_path / "locales",
            sphinx_source=Path("unused"),
            sphinx_translations=Path("unused"),
            snapshots=30,
            docs_version="3",
            limit=10,
        )
