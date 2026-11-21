import json

import polib
import pytest
from sphinxlint import LintError, PoEntry

from potodo_docs_priority import lint_warnings


def test_check_catalog_counts_multiline_entry_once_and_ignores_inactive_entries(
    tmp_path,
):
    po = polib.POFile()
    po.append(polib.POEntry(msgid="Clean one", msgstr="Poprawny tekst"))
    po.append(
        polib.POEntry(
            msgid="Broken three source words",
            msgstr="Pierwsza linia\n:func:`print`tekst \n``niedomknięte",
        )
    )
    po.append(polib.POEntry(msgid="Empty", msgstr=""))
    po.append(polib.POEntry(msgid="Fuzzy", msgstr="``błędne", flags=["fuzzy"]))
    po.append(polib.POEntry(msgid="Obsolete", msgstr="``błędne", obsolete=True))
    path = tmp_path / "guide.po"
    po.save(str(path))
    result = lint_warnings.check_catalog(path)
    assert result.incomplete_words == 4
    assert len(result.warnings) >= 2
    assert all(message.startswith("guide.po:") for message in result.warnings)
    assert any("missing-space-after-role" in message for message in result.warnings)


def test_fixed_catalog_has_no_incomplete_words(tmp_path):
    path = tmp_path / "guide.po"
    path.write_text('msgid "Clean one"\nmsgstr "Poprawny tekst"\n', encoding="utf-8")
    assert lint_warnings.check_catalog(path) == lint_warnings.LintResult()


def test_overlapping_translation_lines_preserve_entry_context(tmp_path):
    path = tmp_path / "guide.po"
    broken = polib.POEntry(
        msgctxt="button", msgid="Three source words", msgstr="Bad \nWorse \n"
    )
    clean = polib.POEntry(msgctxt="title", msgid=broken.msgid, msgstr="Poprawny tekst")
    # The first translation pushes extracted lines past the next PO entry.
    path.write_text(
        'msgid "Clean first entry"\nmsgstr '
        + json.dumps("Good\n" * 12)
        + "\n\n"
        + str(broken)
        + "\n"
        + str(clean),
        encoding="utf-8",
    )
    result = lint_warnings.check_catalog(path)
    assert result.incomplete_words == 3
    assert len(result.warnings) == 2


@pytest.mark.parametrize(
    "identity, expected_words",
    [(PoEntry("button", "Three source words", ""), 3), (None, 0)],
)
def test_diagnostic_identity_controls_completion_independently_of_line_number(
    tmp_path, monkeypatch, identity, expected_words
):
    path = tmp_path / "guide.po"
    po = polib.POFile()
    po.append(
        polib.POEntry(
            msgctxt="button", msgid="Three source words", msgstr="Poprawny tekst"
        )
    )
    po.append(polib.POEntry(msgid="Three source words", msgstr="Inny tekst"))
    po.save(str(path))
    failure = LintError(str(path), 0, "Test diagnostic", "test-checker", identity)
    monkeypatch.setattr(lint_warnings, "check_file", lambda *args: [failure])
    result = lint_warnings.check_catalog(path)
    assert result.incomplete_words == expected_words
    assert result.warnings == ("guide.po:0: Test diagnostic (test-checker)",)


def test_unreadable_catalog_does_not_silently_count_as_clean(tmp_path):
    with pytest.raises(OSError):
        lint_warnings.check_catalog(tmp_path / "missing.po")
