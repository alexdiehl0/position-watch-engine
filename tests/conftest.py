"""Every test runs against a fresh copy of the synthetic workspace in
fixtures/workspace, never the real one, and with no secrets set."""

import shutil
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "workspace"


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    shutil.copytree(FIXTURE, ws)
    monkeypatch.setenv("POSITION_WATCH_WORKSPACE", str(ws))
    for name in ("FMP_API_KEY", "FINNHUB_API_KEY", "DASHBOARD_PASSCODE"):
        monkeypatch.delenv(name, raising=False)
    return ws


@pytest.fixture
def holdings():
    from position_watch.review import load_open_holdings

    return load_open_holdings()


@pytest.fixture
def review_data(workspace):
    import json

    return json.loads((workspace / "state" / "latest_review.json").read_text())
