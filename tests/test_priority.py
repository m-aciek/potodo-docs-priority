import argparse
import json
import re
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from potodo_docs_priority.cli import main
from potodo_docs_priority.priority import (
    PROJECTS,
    DocumentScore,
    build_priority_rows,
    calculate_document_scores,
    cpython_document_urls,
    load_page_visitors,
    normalize_language,
    normalized_metric_values,
    normalized_ranks,
    packaging_document_urls,
    positive_int,
    resolve_translation_paths,
)


@pytest.mark.parametrize("project_name", ["cpython", "sphinx", "packaging"])
def test_core_boost_is_additive_and_scoped_to_cpython(tmp_path, project_name):
    resources = [
        "bugs.po",
        "tutorial/index.po",
        "tutorial/deeper/example.po",
        "builtins/functions.po",
        "library/functions.po",
        "bugs-extra.po",
        "tutorial-extra/index.po",
        "reference/index.po",
    ]
    directory = SimpleNamespace(
        path=tmp_path,
        files=[
            SimpleNamespace(path=tmp_path / name, words=10, translated_words=5)
            for name in resources
        ],
    )
    scores = {
        PurePosixPath(name).with_suffix(".pot"): DocumentScore((), (1,))
        for name in resources
    }
    rows = build_priority_rows(
        [directory],
        scores,
        None,
        None,
        project=PROJECTS[project_name],
        language="pl",
        docs_version="3",
        show_finished=False,
    )
    for row in rows:
        expected_boost = (
            25 if project_name == "cpython" and row.resource in resources[:4] else 0
        )
        assert row.core_boost == expected_boost
        assert row.priority == 50 + expected_boost
    if project_name == "cpython":
        assert {row.resource for row in rows[:4]} == set(resources[:4])


def _write_snapshot(stats_dir, site, date, prefix, rows):
    snapshot_dir = stats_dir / "stats" / f"{site}_{date}"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / f"{site}_{date}.prefix-{prefix}.pages.json"
    path.write_text(json.dumps(rows), encoding="utf-8")


def _write_cpython_tree(tmp_path):
    cpython_root = tmp_path / "cpython"
    doc_root = cpython_root / "Doc"
    templates = doc_root / "tools" / "templates"
    tutorial = doc_root / "tutorial"
    include = cpython_root / "Include"
    templates.mkdir(parents=True)
    tutorial.mkdir()
    include.mkdir()
    (doc_root / "index.rst").write_text("Index\n=====\n", encoding="utf-8")
    (include / "patchlevel.h").write_text(
        "#define PY_MAJOR_VERSION 3\n#define PY_MINOR_VERSION 14\n",
        encoding="utf-8",
    )
    (templates / "indexcontent.html").write_text(
        '<a class="biglink" href="{{ pathto(\'tutorial/first\') }}">First</a>\n'
        '<a class="biglink" href="{{ pathto(\'tutorial/second\') }}">Second</a>\n',
        encoding="utf-8",
    )
    (tutorial / "first.rst").write_text("First\n=====\n", encoding="utf-8")
    (tutorial / "second.rst").write_text("Second\n======\n", encoding="utf-8")
    return cpython_root


def _write_sphinx_tree(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.rst").write_text(
        "Index\n=====\n\n.. toctree::\n\n   first\n   second\n", encoding="utf-8"
    )
    (source / "first.rst").write_text("First\n=====\n", encoding="utf-8")
    (source / "second.rst").write_text("Second\n======\n", encoding="utf-8")
    return source


def _write_po(path, translated=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    translation = "jeden dwa" if translated else ""
    path.write_text(
        f'msgid "one two"\nmsgstr "{translation}"\n\n'
        'msgid "three four"\nmsgstr ""\n',
        encoding="utf-8",
    )


def test_load_page_visitors_uses_latest_snapshots_and_accepts_repo_root(tmp_path):
    for date, visitors in (
        ("2026-01-01", 1),
        ("2026-01-02", 2),
        ("2026-01-03", 4),
    ):
        _write_snapshot(
            tmp_path,
            "docs.python.org",
            date,
            "3",
            [{"name": "/3/tutorial/", "visitors": str(visitors)}],
        )
    assert load_page_visitors(tmp_path, "docs.python.org", "3", 2) == {
        "/3/tutorial/": 6
    }


def test_load_page_visitors_rejects_missing_snapshots(tmp_path):
    with pytest.raises(FileNotFoundError, match="no Plausible page snapshots"):
        load_page_visitors(tmp_path, "docs.python.org", "pl", 30)


def test_project_url_conventions():
    document = PurePosixPath("tutorial/index.rst")
    assert cpython_document_urls(document, "pl/3") == (
        "/pl/3/tutorial/",
        "/pl/3/tutorial/index.html",
    )
    assert packaging_document_urls(document, "pt-br") == ("/pt-br/latest/tutorial/",)
    assert packaging_document_urls(PurePosixPath("guide.rst"), "en") == (
        "/en/latest/guide/",
    )


def test_normalize_language():
    assert normalize_language("pt_BR") == "pt-br"
    assert normalize_language("ja") == "ja"


def test_normalized_ranks_follow_metric_direction_and_keep_ties():
    assert normalized_ranks([10, 20, 20], higher_is_better=True) == [0, 0.75, 0.75]
    assert normalized_ranks([(1, 1), (2, 1)], higher_is_better=False) == [1, 0]
    assert normalized_ranks([0, 0], higher_is_better=True) == [0.5, 0.5]


def test_metric_normalization_matches_metric_semantics():
    assert normalized_metric_values("completion", [0, 50, 100]) == [0, 0.5, 1]
    assert normalized_metric_values("navigation", [(0,), (1,), (2,)]) == [1, 0.5, 0]
    visitors = normalized_metric_values("original_popularity", [0, 10, 1000])
    assert visitors[0] == 0
    assert 0 < visitors[1] < visitors[2] == 1
    assert normalized_metric_values("translated_popularity", [0, 0]) == [0.5, 0.5]


def test_positive_int():
    assert positive_int("3") == 3
    with pytest.raises(argparse.ArgumentTypeError, match="at least 1"):
        positive_int("0")


def test_cpython_scores_preserve_custom_landing_page(tmp_path):
    scores = calculate_document_scores(
        PROJECTS["cpython"], _write_cpython_tree(tmp_path)
    )
    assert scores[PurePosixPath("tutorial/first.pot")].score == (1, 1)
    assert scores[PurePosixPath("tutorial/second.pot")].score == (1, 2)
    assert scores[PurePosixPath("sphinx.pot")].score == (0,)
    assert scores[PurePosixPath("sphinx.pot")].documents == (
        PurePosixPath("index.rst"),
    )


def test_sphinx_scores_start_at_index(tmp_path):
    scores = calculate_document_scores(PROJECTS["sphinx"], _write_sphinx_tree(tmp_path))
    assert scores[PurePosixPath("index.pot")].score == (0,)
    assert scores[PurePosixPath("first.pot")].score == (1, 1)
    assert scores[PurePosixPath("second.pot")].score == (1, 2)


def test_sphinx_template_catalog_links_to_index(tmp_path):
    scores = calculate_document_scores(PROJECTS["sphinx"], _write_sphinx_tree(tmp_path))
    assert scores[PurePosixPath("sphinx.pot")].documents == (
        PurePosixPath("index.rst"),
    )
    assert scores[PurePosixPath("sphinx.pot")].score == (0,)


def test_packaging_compacts_all_documents_into_messages(tmp_path):
    scores = calculate_document_scores(
        PROJECTS["packaging"], _write_sphinx_tree(tmp_path)
    )
    assert set(scores) == {PurePosixPath("messages.pot")}
    assert scores[PurePosixPath("messages.pot")].score == (0,)
    assert scores[PurePosixPath("messages.pot")].documents == (
        PurePosixPath("first.rst"),
        PurePosixPath("index.rst"),
        PurePosixPath("second.rst"),
    )


def test_locales_root_resolves_language_messages(tmp_path):
    messages = tmp_path / "locales" / "pl_PL" / "LC_MESSAGES"
    messages.mkdir(parents=True)
    assert resolve_translation_paths(
        PROJECTS["sphinx"], [tmp_path / "locales"], "pl_PL"
    ) == [messages.resolve()]


def test_cpython_cli_orders_resources_using_four_metrics(tmp_path, capsys, monkeypatch):
    po_dir = tmp_path / "translation"
    _write_po(po_dir / "sphinx.po", translated=False)
    _write_po(po_dir / "tutorial" / "first.po")
    _write_po(po_dir / "tutorial" / "second.po", translated=False)
    source = _write_cpython_tree(tmp_path)
    _write_snapshot(
        tmp_path,
        "docs.python.org",
        "2026-01-01",
        "3",
        [
            {"name": "/3/", "visitors": "100"},
            {"name": "/3/tutorial/first.html", "visitors": "20"},
            {"name": "/3/tutorial/second.html", "visitors": "10"},
        ],
    )
    _write_snapshot(
        tmp_path,
        "docs.python.org",
        "2026-01-01",
        "pl",
        [
            {"name": "/pl/3/", "visitors": "5"},
            {"name": "/pl/3/tutorial/first.html", "visitors": "4"},
            {"name": "/pl/3/tutorial/second.html", "visitors": "1"},
        ],
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "potodo-docs-priority",
            str(po_dir),
            "--no-cache",
            "--project",
            "cpython",
            "--source",
            str(source),
            "--plausible-stats",
            str(tmp_path),
            "--language",
            "pl",
        ],
    )
    main()
    output = capsys.readouterr().out
    resources = re.findall(r"tutorial/(?:first|second)\.po", output)
    assert resources == ["tutorial/first.po", "tutorial/second.po"]
    assert "50.00%" in output
    assert "20" in output
    sphinx_line = next(
        line for line in output.splitlines() if line.endswith("sphinx.po")
    )
    assert re.search(r"\s100\s+5\s+0\s+sphinx\.po$", sphinx_line)


def test_sphinx_cli_uses_completion_and_distance_without_stats(
    tmp_path, capsys, monkeypatch
):
    source = _write_sphinx_tree(tmp_path)
    locales = tmp_path / "locales"
    _write_po(locales / "pl_PL" / "LC_MESSAGES" / "index.po", translated=False)
    _write_po(locales / "pl_PL" / "LC_MESSAGES" / "first.po")
    _write_po(locales / "pl_PL" / "LC_MESSAGES" / "second.po", translated=False)
    monkeypatch.setattr(
        "sys.argv",
        [
            "potodo-docs-priority",
            str(locales),
            "--no-cache",
            "--project",
            "sphinx",
            "--source",
            str(source),
            "--language",
            "pl_PL",
        ],
    )
    main()
    output = capsys.readouterr().out
    assert output.index("index.po") < output.index("first.po")
    assert output.index("first.po") < output.index("second.po")
    assert re.search(r"\s0\s+index\.po", output)
    assert re.search(r"\s-\s+\s-\s+1\.1\s+first\.po", output)


def test_packaging_cli_aggregates_compact_catalog_traffic(
    tmp_path, capsys, monkeypatch
):
    source = _write_sphinx_tree(tmp_path)
    locales = tmp_path / "locales"
    _write_po(locales / "ja" / "LC_MESSAGES" / "messages.po", translated=False)
    _write_snapshot(
        tmp_path,
        "packaging.python.org",
        "2026-01-01",
        "en",
        [
            {"name": "/en/latest/", "visitors": "5"},
            {"name": "/en/latest/first/", "visitors": "20"},
            {"name": "/en/latest/second/", "visitors": "10"},
        ],
    )
    _write_snapshot(
        tmp_path,
        "packaging.python.org",
        "2026-01-01",
        "ja",
        [
            {"name": "/ja/latest/", "visitors": "1"},
            {"name": "/ja/latest/first/", "visitors": "3"},
        ],
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "potodo-docs-priority",
            str(locales),
            "--no-cache",
            "--project",
            "packaging",
            "--source",
            str(source),
            "--plausible-stats",
            str(tmp_path),
            "--language",
            "ja",
            "--show-finished",
        ],
    )
    main()
    output = capsys.readouterr().out
    assert re.search(r"\s35\s+4\s+0\s+messages\.po", output)
