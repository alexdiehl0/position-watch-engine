from types import SimpleNamespace

import pytest

from position_watch import settings
from position_watch.analysis import stock
from position_watch.sources import finnhub, fmp


class FakeHTTP:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append(url)
        status, body = self.responses(url, params)
        return SimpleNamespace(status_code=status, text=str(body), json=lambda: body)


@pytest.fixture(autouse=True)
def fresh_clients(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "fh-test-key")
    monkeypatch.setenv("FMP_API_KEY", "fmp-test-key")
    monkeypatch.setattr(finnhub, "_throttle", lambda: None)
    finnhub.reset_run_state()
    fmp.reset_run_state()


def test_finnhub_answers_repeat_requests_from_the_run_cache(monkeypatch):
    http = FakeHTTP(lambda url, p: (200, {"metric": {"peTTM": 10}}))
    monkeypatch.setattr(finnhub.requests, "get", http)
    assert finnhub.get_ratios("AAA")[0] == finnhub.get_ratios("AAA")[0]
    assert len(http.calls) == 1


def test_finnhub_stops_calling_an_endpoint_the_plan_refuses(monkeypatch):
    http = FakeHTTP(lambda url, p: (403, {"error": "You don't have access to this resource."}))
    monkeypatch.setattr(finnhub.requests, "get", http)
    for symbol in ("A", "B", "C", "D", "E"):
        finnhub.get_price_target(symbol)
    assert len(http.calls) == finnhub.FORBIDDEN_AFTER


def test_finnhub_keeps_an_endpoint_that_only_refuses_some_symbols(monkeypatch):
    http = FakeHTTP(lambda url, p: (403, {}) if p["symbol"].endswith(".PA") else (200, ["X"]))
    monkeypatch.setattr(finnhub.requests, "get", http)
    finnhub.get_peers("OK1")
    for symbol in ("A.PA", "B.PA", "C.PA", "D.PA"):
        finnhub.get_peers(symbol)
    assert finnhub.get_peers("OK2")[0] == ["X"]


def test_fmp_plan_refusal_costs_one_call_per_symbol(monkeypatch):
    http = FakeHTTP(lambda url, p: (402, {"Error Message": "Premium"}))
    monkeypatch.setattr(fmp.requests, "get", http)
    stock._fmp_fields("PM")
    assert len(http.calls) == 1


def test_fmp_quota_exhaustion_stops_further_calls(monkeypatch):
    http = FakeHTTP(lambda url, p: (429, {"Error Message": "Limit Reach"}))
    monkeypatch.setattr(fmp.requests, "get", http)
    fmp.get_quote("A")
    fmp.get_quote("B")
    assert len(http.calls) == 1


def test_links_come_from_github_actions_environment(workspace, monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/their-portfolio")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace.parent))
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    monkeypatch.setenv("PAGES_REPOSITORY", "Someone/locked-page")
    assert settings.reports_url() == "https://github.com/someone/their-portfolio/tree/main/workspace/reports"
    assert settings.report_url("2026-01-02").endswith("/blob/main/workspace/reports/2026-01-02-review.md")
    assert settings.dashboard_url() == "https://someone.github.io/locked-page/"


def test_no_links_outside_github(monkeypatch):
    for name in ("GITHUB_REPOSITORY", "PAGES_REPOSITORY", "POSITION_WATCH_REPORTS_URL", "POSITION_WATCH_DASHBOARD_URL"):
        monkeypatch.delenv(name, raising=False)
    assert settings.reports_url() is None and settings.report_url("x") is None and settings.dashboard_url() is None
