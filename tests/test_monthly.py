from datetime import date

from position_watch import mail, monthly
from tests.fakes import FakeClaude

ANSWER = {
    "overview": "Mostly USD, led by two stocks.",
    "strengths": ["Income from dividends."],
    "risks": ["USDS is 40% of value."],
    "call_patterns": ["EURS moved from Hold to Add."],
    "to_consider": ["Is the USD share intended?"],
}

LOG = [
    {"date": "2026-01-10", "scope": "holding", "symbol": "EURS", "action": "hold", "one_line": "Fair."},
    {"date": "2026-01-20", "scope": "holding", "symbol": "EURS", "action": "add", "one_line": "Cheaper."},
    {"date": "2026-01-21", "scope": "etf", "symbol": "ETFX", "action": "top_up", "one_line": "Discount."},
    {"date": "2025-11-01", "scope": "holding", "symbol": "OLD", "action": "sell", "one_line": "Too old."},
]


def test_history_counts_and_changes():
    rows = {r["symbol"]: r for r in monthly.history(LOG, date(2026, 2, 1))}
    assert "OLD" not in rows  # outside the 30-day window
    assert rows["EURS"]["hold"] == 1 and rows["EURS"]["positive"] == 1
    assert rows["EURS"]["changes"] == [{"date": "2026-01-20", "from": "Hold", "to": "Add"}]
    assert rows["ETFX"]["latest"]["action"] == "Top up"


def test_snapshot_measures_the_whole_portfolio(holdings, review_data):
    snap = monthly.snapshot(holdings, review_data)
    assert round(sum(snap["asset_mix_pct"].values())) == 100
    assert set(snap["asset_mix_pct"]) == {"stock", "etf", "commodity"}
    assert snap["currency_pct"]["EUR"] > 0 and snap["concentration"]["top3_weight_pct"] <= 100
    assert snap["risk"]["volatility_3m_pct"]["coverage_pct"] < 100  # only part of the portfolio has volatility
    assert snap["sectors"][0]["share_pct"] >= snap["sectors"][-1]["share_pct"]


CARD = {"against_index": {"benchmark": "S&P 500", "net_invested_usd": 1000.0, "portfolio_value_usd": 1300.0,
                          "portfolio_return_pct": 30.0, "index_value_usd": 1200.0, "index_return_pct": 20.0,
                          "difference_pct": 10.0, "first_trade": "2024-01-02", "as_of": "2026-01-30", "gaps": None},
        "calls": {"by_action": [{"action": "buy", "calls": 2, "beat_the_index": 1, "average_difference_pct": 1.5,
                                 "detail": []}], "too_recent": 3, "benchmark": "S&P 500"},
        "error": None}  # fmt: skip


def test_facts_make_one_price_request(workspace, monkeypatch):
    monkeypatch.setattr(monthly.suggestion_log, "load_entries", lambda: LOG)
    monkeypatch.setattr(monthly.scorecard, "compute", lambda totals, today, **kw: CARD)
    f = monthly.facts(date(2026, 2, 1))
    assert f["scorecard"] == CARD
    assert f["month"] == "2026-01" and f["portfolio"]["concentration"]["top3_weight_pct"] > 0
    assert {r["symbol"] for r in f["calls_over_the_month"]} == {"EURS", "ETFX"}


def test_monthly_run_writes_report_snapshot_and_email(workspace, monkeypatch):
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None, key=None: sent.append((subject, body, html,
                                                                                                   key)))  # fmt: skip
    monkeypatch.setattr(monthly.suggestion_log, "load_entries", lambda: LOG)
    monkeypatch.setattr(monthly.scorecard, "compute", lambda totals, today, **kw: CARD)
    result = monthly.run(client=FakeClaude(calls=ANSWER), today=date(2026, 2, 1))

    report = (workspace / "reports" / "monthly" / "2026-01-review.md").read_text()
    for expected in ("# Monthly Portfolio Review — 2026-01", "Mostly USD, led by two stocks.", "| EURS | holding |",
                     "2026-01-20: Hold → Add", "Is the USD share intended?", "Not financial advice."):  # fmt: skip
        assert expected in report
    assert (workspace / "state" / "monthly_snapshots.csv").read_text().startswith("month,value_usd")
    assert "monthly" in (workspace / "state" / "api_usage.csv").read_text()
    assert sent[0][0] == "AI STOCK PORTFOLIO REVIEW — Monthly — January 2026" and "WHAT'S WORKING" in sent[0][1]
    assert sent[0][3] == "monthly-2026-01" and "THE MONTH&rsquo;S BEST IDEAS" in sent[0][2]
    assert result["month"] == "2026-01"
    report = (workspace / "reports" / "monthly" / "2026-01-review.md").read_text()
    assert "| The same money in the S&P 500 | $1,000 | $1,200 | +20.0% |" in report
    assert "Difference: **+10.0%**" in report and "3 too recent to count" in report
    assert "this portfolio +30.0%, the same money in the index +20.0% (+10.0 pts)" in sent[0][1]


CARD_WITH_IDEAS = {**CARD, "best_ideas": {"considered": 6, "benchmark": "S&P 500", "unpriced": [], "ideas": [
    {"symbol": "AAA", "name": "Triple A Corp", "since": "2026-01-08", "first_action": "buy", "stock_pct": 12.5,
     "index_pct": 1.5, "difference_pct": 11.0, "as_of": "2026-01-30", "days": 22, "latest_action": "hold",
     "latest_date": "2026-01-30", "latest_one_line": "Ran up; fairly valued now.", "on_watchlist_now": True}]}}  # fmt: skip


def test_the_monthly_email_leads_with_the_numbers_and_the_best_ideas(workspace, monkeypatch):
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None, key=None: sent.append((body, html)))
    monkeypatch.setattr(monthly.suggestion_log, "load_entries", lambda: LOG)
    monkeypatch.setattr(monthly.scorecard, "compute", lambda totals, today, **kw: CARD_WITH_IDEAS)
    monthly.run(client=FakeClaude(calls=ANSWER), today=date(2026, 2, 1))
    text, html = sent[0]
    assert "1. AAA — Triple A Corp: +12.5% since 8 Jan (22 days; S&P 500 +1.5%). Today: HOLD" in text
    assert "Triple A Corp" in html and "TODAY: HOLD" in html and "+11.0 pts vs index" in html
    assert html.index("BEST IDEAS") < html.index("THE MONTH IN BRIEF")  # what he may act on comes first
    report = (workspace / "reports" / "monthly" / "2026-01-review.md").read_text()
    assert "| 1 | AAA — Triple A Corp | 2026-01-08 (Buy) | +12.5% | +1.5% | +11.0% | Hold (2026-01-30) |" in report
    # and it can be rebuilt later, without a Claude call, to look at or resend by hand
    out = monthly.preview(workspace / "preview")
    assert "Triple A Corp" in (workspace / "preview" / "monthly-email.html").read_text()
    assert out["subject"] == "AI STOCK PORTFOLIO REVIEW — Monthly — January 2026"


def test_a_month_already_emailed_is_not_reviewed_or_sent_again(workspace, monkeypatch):
    """1 Oct 2026: a run emailed, lost its commit, and a backup slot emailed the client again."""
    monkeypatch.setattr(mail, "already_sent", lambda key, subject, since: key == "monthly-2026-01")
    monkeypatch.setattr(mail, "send", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent twice")))
    client = FakeClaude(calls=ANSWER)
    assert monthly.run(client=client, today=date(2026, 2, 1)) == {"month": "2026-01", "skipped": "already sent"}
    assert client.requests == [] and client.batch_requests == []  # nothing paid for either
