import io
import json
from urllib.error import HTTPError

import pytest

from potodo_docs_priority import fetch_spellcheck


def report(**changes):
    return {
        "schema_version": 1,
        "status": "complete",
        "repository": "python/python-docs-pl",
        "language": "pl",
        "docs_version": "3.15",
        "catalogs": {},
        **changes,
    }


def test_downloads_public_report_unchanged_without_github_api(tmp_path, monkeypatch):
    data = json.dumps(report()).encode()
    calls = []

    def urlopen(url, **kwargs):
        calls.append((url, kwargs))
        return io.BytesIO(data)

    monkeypatch.setattr(fetch_spellcheck, "urlopen", urlopen)
    output = tmp_path / "reports/pl.json"
    assert fetch_spellcheck.download_report("python/python-docs-pl", "3.15", output)
    assert output.read_bytes() == data
    assert calls == [
        ("https://translations.python.org/spellcheck-pl.json", {"timeout": 60})
    ]


@pytest.mark.parametrize(
    "data",
    [
        None,
        report(status="error"),
        report(schema_version=2),
        report(repository="other/repository"),
        report(language="fr"),
        report(docs_version="3.14"),
    ],
)
def test_unavailable_or_incompatible_reports_remove_previous_file(
    tmp_path, monkeypatch, data
):
    monkeypatch.setattr(
        fetch_spellcheck,
        "urlopen",
        lambda *a, **kw: io.BytesIO(json.dumps(data).encode()),
    )
    output = tmp_path / "pl.json"
    output.write_text("old report")
    assert not fetch_spellcheck.download_report("python/python-docs-pl", "3.15", output)
    assert not output.exists()


@pytest.mark.parametrize("status", [404, 403, 500])
def test_only_missing_public_report_is_unavailable(tmp_path, monkeypatch, status):
    def fail(*args, **kwargs):
        raise HTTPError(args[0], status, "HTTP failure", {}, None)

    monkeypatch.setattr(fetch_spellcheck, "urlopen", fail)
    output = tmp_path / "pl.json"
    if status == 404:
        assert not fetch_spellcheck.download_report(
            "python/python-docs-pl", "3.15", output
        )
    else:
        with pytest.raises(HTTPError):
            fetch_spellcheck.download_report("python/python-docs-pl", "3.15", output)
    assert not output.exists()
