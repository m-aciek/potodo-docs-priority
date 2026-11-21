import polib
import pytest

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


def test_unreadable_catalog_does_not_silently_count_as_clean(tmp_path):
    with pytest.raises(OSError):
        lint_warnings.check_catalog(tmp_path / "missing.po")
