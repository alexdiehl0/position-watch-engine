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
    monkeypatch.setattr(monthly.scorecard, "compute", lambda totals, today: CARD)
    f = monthly.facts(date(2026, 2, 1))
    assert f["scorecard"] == CARD
    assert f["month"] == "2026-01" and f["portfolio"]["concentration"]["top3_weight_pct"] > 0
    assert {r["symbol"] for r in f["calls_over_the_month"]} == {"EURS", "ETFX"}


def test_monthly_run_writes_report_snapshot_and_email(workspace, monkeypatch):
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None: sent.append((subject, body)))
    monkeypatch.setattr(monthly.suggestion_log, "load_entries", lambda: LOG)
    monkeypatch.setattr(monthly.scorecard, "compute", lambda totals, today: CARD)
    result = monthly.run(client=FakeClaude(calls=ANSWER), today=date(2026, 2, 1))

    report = (workspace / "reports" / "monthly" / "2026-01-review.md").read_text()
    for expected in ("# Monthly Portfolio Review — 2026-01", "Mostly USD, led by two stocks.", "| EURS | holding |",
                     "2026-01-20: Hold → Add", "Is the USD share intended?", "Not financial advice."):  # fmt: skip
        assert expected in report
    assert (workspace / "state" / "monthly_snapshots.csv").read_text().startswith("month,value_usd")
    assert "monthly" in (workspace / "state" / "api_usage.csv").read_text()
    assert sent[0][0] == "AI STOCK PORTFOLIO REVIEW — Monthly — January 2026" and "Strengths" in sent[0][1]
    assert result["month"] == "2026-01"
    report = (workspace / "reports" / "monthly" / "2026-01-review.md").read_text()
    assert "| The same money in the S&P 500 | $1,000 | $1,200 | +20.0% |" in report
    assert "Difference: **+10.0%**" in report and "3 too recent to count" in report
    assert "The same money in the S&P 500: +20.0%" in sent[0][1]
