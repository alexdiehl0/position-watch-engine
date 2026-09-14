import json

import pytest

from position_watch import daily, mail, review
from tests.fakes import FEEDBACK, FakeClaude


@pytest.fixture
def pipeline(monkeypatch, review_data):
    for name in ("FMP_API_KEY", "FINNHUB_API_KEY", "ANTHROPIC_API_KEY", "MAIL_USER", "MAIL_APP_PASSWORD"):
        monkeypatch.setenv(name, f"test-{name.lower()}-value")
    review_data["run"] = {"started_at": "2026-01-02T04:00:00+00:00", "finished_at": "2026-01-02T04:05:00+00:00"}
    monkeypatch.setattr(review, "run", lambda: review_data)
    monkeypatch.setattr(mail, "fetch_feedback", lambda: FEEDBACK)
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None: sent.append((to, subject, body, html)))
    return sent


def test_full_run_writes_everything_and_emails(workspace, pipeline):
    result = daily.run(client=FakeClaude(), today="2026-01-02")

    state = workspace / "state"
    assert json.loads((state / "suggestions.json").read_text())["etfs"]["ETFX"]["action"] == "top_up"
    assert "2026-01-02,candidate,AAA,buy" in (state / "suggestion_log.csv").read_text()
    assert json.loads((state / "handoff.json").read_text())["notes_for_tomorrow"] == ["Re-check USDS headlines."]
    assert "<m1@example.com>" in json.loads((state / "feedback_used.json").read_text())
    assert "2026-01-02,daily,claude-opus-5,True,20000,8000,0.15" in (state / "api_usage.csv").read_text()

    report = (workspace / "reports" / "2026-01-02-review.md").read_text()
    for expected in ("# Daily Portfolio Review — 2026-01-02", "### USDS — US Stock Inc", "- **Action:** Top up",
                     "**Why it's on the watchlist:** Top of today's screen", "Avoiding tobacco, as the client asked.",
                     "Not financial advice."):  # fmt: skip
        assert expected in report
    assert (workspace / "site" / "index.html").exists()

    (to, subject, body, html) = pipeline[0]
    assert subject == "AI STOCK PORTFOLIO REVIEW — 2 Jan 2026"
    assert to == ["operator@example.com", "Client@Example.com"]
    assert body.startswith("AI STOCK PORTFOLIO REVIEW\nFriday, 2 January 2026")
    assert "BUY  AAA — Alpha Corp\nDiscount and yield." in body
    assert "The dashboard was not updated today." in body  # no --pages-dir
    # HTML version: colour-coded badges that still carry the word, most actionable first
    assert "AI STOCK PORTFOLIO REVIEW" in html and ">BUY</span>" in html and "#dcfce7" in html
    stocks = html.split("YOUR STOCKS")[1].split("CORE ETFS")[0]
    assert stocks.index("EURS") < stocks.index("USDS")  # Add before Hold
    assert result["estimated_usd"] == 0.15 and result["preference_changes"] == ["avoid + tobacco"]


def test_failure_reports_and_emails_without_leaking_secrets(workspace, pipeline, monkeypatch):
    def broken():
        raise RuntimeError("request to quote?apikey=test-fmp_api_key-value failed")

    monkeypatch.setattr(review, "run", broken)
    with pytest.raises(daily.StepFailed) as exc:
        daily.run(client=FakeClaude(), today="2026-01-02")

    assert exc.value.step == "gather evidence"
    assert not (workspace / "reports" / "2026-01-02-review.md").exists()
    report = (workspace / "reports" / "2026-01-02-review-FAILED.md").read_text()
    to, subject, body = pipeline[0][0], pipeline[0][1], pipeline[0][2]
    assert subject == "AI STOCK PORTFOLIO REVIEW — 2 Jan 2026 — FAILED"
    assert to == ["operator@example.com"]  # the operator fixes it; the client isn't alarmed
    for text in (report, body, str(exc.value)):
        assert "test-fmp_api_key-value" not in text and "apikey=***" in text


def test_missing_secret_stops_before_any_work(workspace, pipeline, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(daily.StepFailed, match="ANTHROPIC_API_KEY"):
        daily.run(client=FakeClaude(), today="2026-01-02")


def test_rerun_on_the_same_day_replaces_that_days_log(workspace, pipeline):
    daily.run(client=FakeClaude(), today="2026-01-02")
    daily.run(client=FakeClaude(), today="2026-01-02")

    rows = (workspace / "state" / "suggestion_log.csv").read_text()
    assert rows.count("2026-01-02,candidate,AAA,buy") == 1
