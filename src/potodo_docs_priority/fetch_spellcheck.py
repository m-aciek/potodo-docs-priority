"""Download a complete spelling report from trusted GitHub Actions runs."""

import argparse
import io
import json
import logging
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import urlencode


def github_api(endpoint: str) -> bytes:
    """Use gh's authentication and redirect handling for artifact downloads."""
    return subprocess.check_output(["gh", "api", endpoint])


def download_report(repository: str, branch: str, output: Path) -> bool:
    """Select by report completeness, including runs that found spelling errors."""
    output.unlink(missing_ok=True)
    page = 1
    while True:
        query = urlencode({"name": "spellcheck-pl", "per_page": 100, "page": page})
        artifacts = json.loads(
            github_api(f"repos/{repository}/actions/artifacts?{query}")
        )["artifacts"]
        for artifact in artifacts:
            run = artifact["workflow_run"]
            if (
                artifact["expired"]
                or run["head_branch"] != branch
                or run["head_repository_id"] != run["repository_id"]
            ):
                continue
            details = json.loads(
                github_api(f"repos/{repository}/actions/runs/{run['id']}")
            )
            if (
                details["status"] != "completed"
                or details["event"] not in {"push", "schedule", "workflow_dispatch"}
                or details["head_repository"]["full_name"] != repository
                or details["path"]
                not in {
                    ".github/workflows/spellcheck.yml",
                    ".github/workflows/update-and-build.yml",
                }
            ):
                continue
            archive = github_api(
                f"repos/{repository}/actions/artifacts/{artifact['id']}/zip"
            )
            with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                if "spellcheck.json" not in zipped.namelist():
                    continue
                report_bytes = zipped.read("spellcheck.json")
            report = json.loads(report_bytes)
            if (
                report.get("schema_version") != 1
                or report.get("status") != "complete"
                or report.get("repository") != repository
                or report.get("language") != "pl"
                or report.get("docs_version") != branch
            ):
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(report_bytes)
            return True
        if len(artifacts) < 100:
            logging.warning(
                "No complete spellcheck report for %s/%s", repository, branch
            )
            return False
        page += 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="python/python-docs-pl")
    parser.add_argument("--branch", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    download_report(args.repository, args.branch, args.output)


if __name__ == "__main__":
    main()
