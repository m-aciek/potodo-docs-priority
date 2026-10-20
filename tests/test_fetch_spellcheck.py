import io
import json
import subprocess
import zipfile

import pytest

from potodo_docs_priority import fetch_spellcheck


def archive(status="complete", **changes):
    data = {
        "schema_version": 1,
        "status": status,
        "repository": "python/python-docs-pl",
        "language": "pl",
        "docs_version": "3.15",
        **changes,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr("spellcheck.json", json.dumps(data))
    return buffer.getvalue()


def artifact(number=1, **changes):
    return {
        "id": number,
        "expired": False,
        "workflow_run": {
            "id": number,
            "head_branch": "3.15",
            "head_repository_id": 10,
            "repository_id": 10,
        },
        **changes,
    }


def test_failed_runs_with_complete_reports_are_used_and_partial_ones_skipped(
    tmp_path, monkeypatch
):
    calls = []

    def api(endpoint):
        calls.append(endpoint)
        if "?" in endpoint:
            return json.dumps({"artifacts": [artifact(1), artifact(2)]}).encode()
        if endpoint.endswith("/zip"):
            return archive("error" if endpoint.endswith("/1/zip") else "complete")
        return json.dumps(
            {
                "status": "completed",
                "conclusion": "failure",
                "event": "schedule",
                "head_repository": {"full_name": "python/python-docs-pl"},
                "path": ".github/workflows/update-and-build.yml",
            }
        ).encode()

    monkeypatch.setattr(fetch_spellcheck, "github_api", api)
    output = tmp_path / "reports/pl.json"
    assert fetch_spellcheck.download_report("python/python-docs-pl", "3.15", output)
    assert json.loads(output.read_text())["status"] == "complete"
    assert any("/2/zip" in call for call in calls)


def test_expired_other_branch_and_fork_artifacts_are_skipped(tmp_path, monkeypatch):
    candidates = [artifact(1, expired=True), artifact(2), artifact(3)]
    candidates[1]["workflow_run"]["head_branch"] = "3.14"
    candidates[2]["workflow_run"]["head_repository_id"] = 99

    def api(endpoint):
        assert "?" in endpoint
        return json.dumps({"artifacts": candidates}).encode()

    monkeypatch.setattr(fetch_spellcheck, "github_api", api)
    output = tmp_path / "pl.json"
    output.write_text("old cached report")
    assert not fetch_spellcheck.download_report("python/python-docs-pl", "3.15", output)
    assert not output.exists()


@pytest.mark.parametrize(
    "change",
    [
        {"event": "pull_request"},
        {"status": "in_progress"},
        {"path": ".github/workflows/unrelated.yml"},
    ],
)
def test_untrusted_or_running_workflows_are_not_downloaded(
    tmp_path, monkeypatch, change
):
    def api(endpoint):
        assert not endpoint.endswith("/zip")
        if "?" in endpoint:
            return json.dumps({"artifacts": [artifact()]}).encode()
        return json.dumps(
            {
                "status": "completed",
                "event": "push",
                "path": ".github/workflows/spellcheck.yml",
                "head_repository": {"full_name": "python/python-docs-pl"},
                **change,
            }
        ).encode()

    monkeypatch.setattr(fetch_spellcheck, "github_api", api)
    assert not fetch_spellcheck.download_report(
        "python/python-docs-pl", "3.15", tmp_path / "pl.json"
    )


def test_artifact_lists_are_paginated(tmp_path, monkeypatch):
    pages = []

    def api(endpoint):
        pages.append(endpoint)
        candidates = (
            [artifact(expired=True)] * 100 if endpoint.endswith("&page=1") else []
        )
        return json.dumps({"artifacts": candidates}).encode()

    monkeypatch.setattr(fetch_spellcheck, "github_api", api)
    assert not fetch_spellcheck.download_report(
        "python/python-docs-pl", "3.15", tmp_path / "pl.json"
    )
    assert len(pages) == 2
    assert "page=2" in pages[-1]


def test_api_failure_is_not_reported_as_zero_findings(tmp_path, monkeypatch):
    def api(endpoint):
        raise subprocess.CalledProcessError(1, ["gh", "api", endpoint])

    monkeypatch.setattr(fetch_spellcheck, "github_api", api)
    with pytest.raises(subprocess.CalledProcessError):
        fetch_spellcheck.download_report(
            "python/python-docs-pl", "3.15", tmp_path / "pl.json"
        )
