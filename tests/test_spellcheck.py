import hashlib
import json

import polib
import pytest

from potodo_docs_priority import lint_warnings
from potodo_docs_priority.html_report import (
    HtmlItem,
    HtmlMetrics,
    HtmlSection,
    build_sections,
    render_html,
)
from potodo_docs_priority.spellcheck import (
    SpellcheckReport,
    entry_key,
    translation_digest,
)


def report_data(path, entries):
    return {
        "schema_version": 1,
        "language": "pl",
        "repository": "python/python-docs-pl",
        "docs_version": "3.15",
        "created_at": "2026-10-20T12:00:00+00:00",
        "run_url": "https://github.com/python/python-docs-pl/actions/runs/123",
        "status": "complete",
        "catalogs": {
            path.name: {
                "source_sha256": "temporary-scan-checksum",
                "checkout_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "findings": [
                    {
                        "msgctxt": entry.msgctxt,
                        "msgid": entry.msgid,
                        "msgid_plural": entry.msgid_plural,
                        "msgstr_sha256": translation_digest(entry),
                        "words": ["blont", "drugi"],
                    }
                    for entry in entries
                ],
            }
        },
    }


def test_spelling_counts_contextual_entries_once_across_all_checks(tmp_path):
    path = tmp_path / "guide.po"
    catalog = polib.POFile()
    catalog.extend(
        [
            polib.POEntry(
                msgctxt="button", msgid="Three source words", msgstr="``blont"
            ),
            polib.POEntry(
                msgctxt="title", msgid="Three source words", msgstr="Poprawny tekst"
            ),
            polib.POEntry(msgid="Fuzzy entry", msgstr="blont", flags=["fuzzy"]),
            polib.POEntry(msgid="Untranslated entry", msgstr=""),
            polib.POEntry(msgid="Obsolete entry", msgstr="blont", obsolete=True),
        ]
    )
    catalog.save(str(path))
    report = SpellcheckReport(report_data(path, catalog))
    spelling = report.catalog(path, path.name)
    assert spelling.status == "checked"
    assert spelling.entries == {entry_key(catalog[0]), entry_key(catalog[1])}
    # One bad entry reported by both linters and the build still costs 3 words.
    result = lint_warnings.check_catalog(
        path,
        spelling_entries=frozenset({entry_key(catalog[0])}),
        build_messages=frozenset({"Three source words"}),
    )
    assert result.incomplete_words == 6  # Both contexts have the same build msgid.
    result = lint_warnings.check_catalog(
        path, spelling_entries=frozenset({entry_key(catalog[0])})
    )
    assert result.incomplete_words == 3


def test_fixed_translation_and_changed_context_do_not_reuse_old_findings(tmp_path):
    path = tmp_path / "guide.po"
    entry = polib.POEntry(msgctxt="title", msgid="Three source words", msgstr="blont")
    po = polib.POFile()
    po.append(entry)
    po.save(str(path))
    report = SpellcheckReport(report_data(path, [entry]))
    entry.msgstr = "Poprawny tekst"
    po.save(str(path))
    assert report.catalog(path, path.name).entries == frozenset()
    assert report.catalog(path, path.name).status == "stale"
    entry.msgstr = "blont"
    entry.msgctxt = "button"
    po.save(str(path))
    assert not report.catalog(path, path.name).entries


def test_layout_changes_keep_matching_findings_but_not_clean_coverage(tmp_path):
    path = tmp_path / "guide.po"
    path.write_text('msgid "Source words"\nmsgstr "blont"\n', encoding="utf-8")
    entry = polib.pofile(str(path))[0]
    report = SpellcheckReport(report_data(path, [entry]))
    path.write_text("# new comment\n" + path.read_text(), encoding="utf-8")
    spelling = report.catalog(path, path.name)
    assert spelling.entries == {entry_key(entry)}
    assert spelling.status == "stale"


@pytest.mark.parametrize("status", ["error", "unavailable"])
def test_incomplete_reports_never_penalize_or_claim_clean_coverage(tmp_path, status):
    path = tmp_path / "guide.po"
    path.write_text('msgid "Source"\nmsgstr "blont"\n')
    data = report_data(path, polib.pofile(str(path)))
    data["status"] = status
    report = SpellcheckReport(data)
    assert not report.catalog(path, path.name).entries
    assert report.catalog(path, path.name).status == "not checked"
    assert "unknown" in report.notice


def test_missing_report_and_unscanned_catalog_are_explicit(tmp_path):
    report = SpellcheckReport.load(tmp_path / "missing.json", "pl")
    assert "unknown" in report.notice
    assert report.catalog(tmp_path / "missing.po", "missing.po").status == "not checked"


@pytest.mark.parametrize(
    "field,value", [("schema_version", 2), ("language", "fr"), ("status", "success")]
)
def test_incompatible_reports_are_rejected(tmp_path, field, value):
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps(
            {"schema_version": 1, "language": "pl", "status": "complete", field: value}
        )
    )
    with pytest.raises(ValueError):
        SpellcheckReport.load(path, "pl")


def test_html_status_is_informational_and_does_not_hide_table():
    item = HtmlItem(
        "guide",
        "Guide",
        None,
        HtmlMetrics(25, completion=40),
        spellcheck_url="https://example.test/run?a=1&b=2",
        spellcheck_status="stale, findings",
    )
    section = HtmlSection(
        "cpython", "Docs", (item,), spellcheck_notice="Checked <today>"
    )
    html = render_html(section, "Priorities")
    assert "Checked &lt;today&gt;" in html
    assert "spellcheck: stale, findings" in html
    assert 'href="https://example.test/run?a=1&amp;b=2"' in html
    assert '<table aria-label="Translation priorities">' in html


def test_non_http_report_links_are_not_rendered():
    assert SpellcheckReport({"run_url": "javascript:alert(1)"}).url is None


def test_html_builder_uses_external_spelling_findings_without_running_checker(tmp_path):
    source = tmp_path / "Doc"
    source.mkdir()
    (source / "contents.rst").write_text("Docs\n====\n\n.. toctree::\n\n   guide\n")
    (source / "guide.rst").write_text("Good one\n========\n\nBad three words\n")
    translations = tmp_path / "translations"
    translations.mkdir()
    path = translations / "guide.po"
    po = polib.POFile()
    po.extend(
        [
            polib.POEntry(msgid="Good one", msgstr="Poprawny tekst"),
            polib.POEntry(msgid="Bad three words", msgstr="``blont"),
        ]
    )
    po.save(str(path))
    data = report_data(path, [po[1]])
    report = tmp_path / "spellcheck.json"
    report.write_text(json.dumps(data))
    stats = tmp_path / "stats/docs.python.org_2026-01-01"
    stats.mkdir(parents=True)
    (stats / "docs.python.org_2026-01-01.prefix-3.pages.json").write_text("[]")
    section = build_sections(
        projects=["cpython"],
        language="pl",
        plausible_stats=tmp_path,
        cpython_source=source,
        cpython_translations=translations,
        packaging_source=tmp_path,
        packaging_translations=tmp_path,
        sphinx_source=tmp_path,
        sphinx_translations=tmp_path,
        snapshots=30,
        docs_version="3",
        spellcheck_report=report,
    )[0]
    assert section.notice is None
    assert "2026-10-20" in section.spellcheck_notice
    assert len(section.items) == 1
    assert section.items[0].metrics.completion == 40
    assert section.items[0].spellcheck_status == "checked, findings"
    assert section.items[0].spellcheck_url == data["run_url"]
    assert '<table aria-label="Translation priorities">' in render_html(
        section, "Priorities"
    )


def test_missing_checkout_checksum_never_claims_current_coverage(tmp_path):
    path = tmp_path / "guide.po"
    path.write_text('msgid "Source words"\nmsgstr "blont"\n')
    entry = polib.pofile(str(path))[0]
    data = report_data(path, [entry])
    del data["catalogs"][path.name]["checkout_sha256"]
    result = SpellcheckReport(data).catalog(path, path.name)
    assert result.status == "stale"
    assert result.entries == {entry_key(entry)}
