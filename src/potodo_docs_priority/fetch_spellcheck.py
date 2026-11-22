"""Download the public spelling report published by the dashboard."""

import argparse
import json
import logging
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen


def download_report(repository: str, branch: str, output: Path) -> bool:
    """A missing report or a different branch leaves readiness unknown."""
    output.unlink(missing_ok=True)
    try:
        with urlopen(
            "https://translations.python.org/spellcheck-pl.json", timeout=60
        ) as response:
            report_bytes = response.read()
    except HTTPError as error:
        if error.code != 404:
            raise
        report_bytes = b"null"
    report = json.loads(report_bytes)
    expected = {
        "schema_version": 1,
        "status": "complete",
        "repository": repository,
        "language": "pl",
        "docs_version": branch,
    }
    if report is None or any(
        report.get(key) != value for key, value in expected.items()
    ):
        logging.warning("No complete spellcheck report for %s/%s", repository, branch)
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(report_bytes)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="python/python-docs-pl")
    parser.add_argument("--branch", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    download_report(args.repository, args.branch, args.output)


if __name__ == "__main__":
    main()
