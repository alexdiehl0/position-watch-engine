import json

import pytest

from position_watch import daily, mail, review
from position_watch.dashboard import publish
from tests.fakes import CALLS, FEEDBACK, IGNORED, FakeClaude


@pytest.fixture
def pipeline(monkeypatch, review_data):
    for name in ("FMP_API_KEY", "FINNHUB_API_KEY", "ANTHROPIC_API_KEY", "MAIL_USER", "MAIL_APP_PASSWORD"):
        monkeypatch.setenv(name, f"test-{name.lower()}-value")
    review_data["run"] = {"started_at": "2026-01-02T04:00:00+00:00", "finished_at": "2026-01-02T04:05:00+00:00"}
    monkeypatch.setattr(review, "run", lambda: review_data)
    monkeypatch.setattr(mail, "fetch_feedback", lambda: (FEEDBACK, IGNORED))
    sent = []
    monkeypatch.setattr(
        mail, "send", lambda to, subject, body, html=None, key=None: sent.append((to, subject, body, html))
    )
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
    assert "## Markets & world" in report and "| US 10-year yield | 4.50% | +6 bp | +18 bp |" in report
    assert "- **Oil jumped after attacks on shipping lanes.** Higher costs weigh on USDS. *Affects: USDS.*" in report
    site = (workspace / "site" / "index.html").read_text()
    assert "Markets &amp; world" in site and "Oil jumped after attacks on shipping lanes." in site

    (to, subject, body, html) = pipeline[0]
    assert subject == "AI STOCK PORTFOLIO REVIEW — 2 Jan 2026"
    assert to == ["operator@example.com", "Client@Example.com"]
    assert body.startswith("AI STOCK PORTFOLIO REVIEW\nFriday, 2 January 2026")
    assert "BUY  AAA — Alpha Corp\nDiscount and yield." in body
    assert "MARKETS & WORLD\nS&P 500 5,000 (−0.5%) · US 10-year yield 4.50% (+6 bp) · Brent oil $90.00 (+3.1%)" in body
    assert "• Oil jumped after attacks on shipping lanes.\n  Higher costs weigh on USDS. [USDS]" in body
    assert "MARKETS &amp; WORLD" in html and 'href="https://example.com/m1"' in html
    # the message we couldn't use is named once, with how to accept the sender
    assert "A message from stranger@example.com was ignored" in body and "reply: add stranger@example.com" in body
    assert "**Note:** A message from stranger@example.com was ignored" in report
    assert "stranger@example.com" in site
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


@pytest.fixture
def pages(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSCODE", "test passcode")
    monkeypatch.setenv("PAGES_REPOSITORY", "owner/dash")
    checkout = tmp_path / "pages"
    (checkout / ".git").mkdir(parents=True)
    return checkout


def test_email_goes_out_only_once_the_dashboard_is_live(workspace, pipeline, pages, monkeypatch):
    events = []
    monkeypatch.setattr(publish, "push", lambda d, message: events.append(f"push {message}") or True)
    monkeypatch.setattr(publish, "wait_until_live", lambda url, page: events.append(f"live {url}") or True)
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None, key=None: events.append("email"))

    result = daily.run(pages_dir=pages, client=FakeClaude(), today="2026-01-02")

    assert events == ["push Dashboard 2026-01-02", "live https://owner.github.io/dash/", "email"]
    assert result["dashboard_published"] is True and (pages / "index.html").exists()


def test_dashboard_trouble_still_sends_the_email_with_a_note(workspace, pipeline, pages, monkeypatch):
    def refuse(d, message):
        raise publish.PushFailed("git push: denied")

    monkeypatch.setattr(publish, "push", refuse)
    result = daily.run(pages_dir=pages, client=FakeClaude(), today="2026-01-02")
    assert result["dashboard_published"] is False
    assert "The dashboard could not be updated today" in pipeline[0][2]

    pipeline.clear()
    monkeypatch.setattr(publish, "push", lambda d, message: True)
    monkeypatch.setattr(publish, "wait_until_live", lambda url, page: False)  # Pages slower than 10 minutes
    daily.run(pages_dir=pages, client=FakeClaude(), today="2026-01-02")
    assert "still being put online" in pipeline[0][2]


SCREENSHOT_ONLY = {"message_id": "<trades@example.com>", "name": "Client", "role": "client",
                   "from": "client@example.com", "date": "Thu, 1 Jan 2026 15:48:01 +0300", "subject": "Holdings update",
                   "text": "", "attachments": [{"filename": "pasted-image1.png", "content_type": "image/png",
                                                "data": b"\x89PNG-trades"}]}  # fmt: skip


def test_trades_sent_as_a_screenshot_are_in_the_next_mornings_email_report_and_dashboard(
    workspace, pipeline, monkeypatch
):
    """The client's 24 Sept and 1 Oct 2026 screenshots never reached the portfolio. Now they do, that morning."""
    from position_watch import reasoning
    from position_watch.analysis import holdings_update

    monkeypatch.setattr(mail, "fetch_feedback", lambda: ([SCREENSHOT_ONLY], []))
    classified = []
    monkeypatch.setattr(reasoning, "classify_requests", lambda *a, **k: classified.append(a) or ([], {}))
    read = {"view": "transactions", "rows_stated": "", "positions": [], "set_aside": [], "unreadable": "",
            "trades": [holdings_update.check_trade({"symbol": "USDS", "side": "Buy", "date": "Dec 30, 2025",
                                                    "qty": "50", "price": "12.00", "price_currency": "USD",
                                                    "total": "600"})[0]]}  # fmt: skip
    monkeypatch.setattr(holdings_update, "from_picture", lambda uploads, client=None, today=None: (read, {}))

    daily.run(client=FakeClaude({**CALLS, "feedback_applied": [], "preference_changes": []}), today="2026-01-02")

    line = "Bought 50 USDS at 12.00 USD on 30 Dec 2025 — 100 → 150 held, average cost 10.00 → 10.67"
    assert "USDS,US Stock Inc,NYSE,USD,150,10.6667" in (workspace / "data" / "processed" / "holdings.csv").read_text()
    assert classified == []  # a file with no words of his own is not a request
    (to, subject, body, html) = pipeline[0]
    assert f"YOUR TRADES\n\nYour trades are in — holdings updated (from pasted-image1.png)\n• {line}" in body
    assert "YOUR TRADES" in html and line in html
    assert body.index("YOUR TRADES") < body.index("YOUR STOCKS")  # near the top, not in the footnotes
    report = (workspace / "reports" / "2026-01-02-review.md").read_text()
    assert f"## Your trades\n\n**Your trades are in — holdings updated** — from pasted-image1.png\n\n- {line}" in report
    site = (workspace / "site" / "index.html").read_text()
    assert 'id="trades"' in site and line in site and "Your trades are in" in site
    assert (workspace / "data" / "raw" / "uploads" / "2026-01-02-pasted-image1.png").read_bytes() == b"\x89PNG-trades"
    assert "<trades@example.com>" in json.loads((workspace / "state" / "feedback_used.json").read_text())


def test_the_daily_email_is_not_sent_twice(workspace, pipeline, monkeypatch):
    monkeypatch.setattr(mail, "already_sent", lambda key, subject, since: key == "daily-2026-01-02")
    daily.run(client=FakeClaude(), today="2026-01-02")
    assert pipeline == []
