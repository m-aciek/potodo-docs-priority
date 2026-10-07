from io import BytesIO

import pytest

from potodo_docs_priority import build_warnings


@pytest.fixture(autouse=True)
def empty_published_build_report(monkeypatch):
    """Keep report tests independent of live dashboard data and networking."""
    monkeypatch.setattr(build_warnings, "urlopen", lambda *args, **kwargs: BytesIO(b""))
