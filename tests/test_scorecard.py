"""The scorecard: the same money, on the same days, into the index."""

from datetime import date

from position_watch.analysis import scorecard

# The index doubles over two years, from 100 to 200.
INDEX = [("2024-01-02", 100.0), ("2025-01-02", 150.0), ("2026-01-02", 200.0)]
TRANSACTIONS = [
    {"symbol": "AAA", "side": "Buy", "date": "2024-01-02", "total": "1000", "total_currency": "USD"},
    {"symbol": "BBB", "side": "Buy", "date": "2025-01-02", "total": "600", "total_currency": "EUR"},
    {"symbol": "AAA", "side": "Sell", "date": "2026-01-02", "total": "400", "total_currency": "USD"},
]


def test_cash_flows_convert_at_the_rate_of_the_day(monkeypatch):
    monkeypatch.setattr(scorecard.fx, "get_rate_on", lambda day, base, quote="USD": ({"rate": 1.1}, None))
    flows, gaps = scorecard.cash_flows(TRANSACTIONS)
    assert flows == [("2024-01-02", 1000.0), ("2025-01-02", 660.0), ("2026-01-02", -400.0)]  # a sale is money out
    assert gaps == []


def test_a_missing_rate_is_a_gap_not_a_guess(monkeypatch):
    monkeypatch.setattr(scorecard.fx, "get_rate_on", lambda day, base, quote="USD": (None, "ECB rate failed"))
    flows, gaps = scorecard.cash_flows(TRANSACTIONS)
    assert [f[0] for f in flows] == ["2024-01-02", "2026-01-02"]  # the EUR trade is left out, and said so
    assert gaps == ["EUR->USD on 2025-01-02: ECB rate failed"]


def test_the_index_is_bought_on_the_day_the_client_bought():
    flows = [("2024-01-02", 1000.0), ("2025-01-02", 600.0)]
    result = scorecard.index_equivalent(flows, INDEX)
    # 10 units at 100 + 4 units at 150 = 14 units, worth 2,800 at 200
    assert result == {"value_usd": 2800.0, "net_invested_usd": 1600.0, "return_pct": 75.0, "as_of": "2026-01-02"}


def test_a_weekend_trade_uses_the_last_close():
    assert scorecard.index_equivalent([("2024-06-01", 100.0)], INDEX)["value_usd"] == 200.0  # priced at 2024-01-02


def test_the_portfolio_is_measured_against_the_same_cash(monkeypatch):
    monkeypatch.setattr(scorecard.fx, "get_rate_on", lambda day, base, quote="USD": ({"rate": 1.0}, None))
    against = scorecard.against_index(TRANSACTIONS, {"value_usd": 2000.0, "dividends_usd": 100.0}, INDEX)
    assert against["net_invested_usd"] == 1200.0  # 1000 in, 600 in, 400 out
    assert against["portfolio_value_usd"] == 2100.0 and against["portfolio_return_pct"] == 75.0
    # 10 units at 100, 4 at 150, less 2 sold at 200 = 12 units worth 2,400 on 1,200 put in
    assert against["index_return_pct"] == 100.0 and against["difference_pct"] == -25.0  # honest when it lags
    assert against["first_trade"] == "2024-01-02" and against["as_of"] == "2026-01-02"


def test_calls_are_scored_against_the_index_over_their_own_days():
    prices = {
        scorecard.BENCHMARK: [("2026-01-02", 100.0), ("2026-03-02", 110.0)],  # index +10%
        "WIN": [("2026-01-02", 10.0), ("2026-03-02", 13.0)],  # +30%: beat it
        "LOSE": [("2026-01-02", 10.0), ("2026-03-02", 10.5)],  # +5%: didn't
        "YESTERDAY": [("2026-03-01", 10.0), ("2026-03-02", 12.0)],
    }
    entries = [
        {"date": "2026-01-02", "symbol": "WIN", "action": "buy"},
        {"date": "2026-01-15", "symbol": "WIN", "action": "buy"},  # the same call again: counted once, from the first
        {"date": "2026-01-02", "symbol": "LOSE", "action": "buy"},
        {"date": "2026-03-01", "symbol": "YESTERDAY", "action": "buy"},  # too recent to mean anything
    ]
    out = scorecard.calls_against_index(entries, prices, date(2026, 3, 2))
    buys = out["by_action"][0]
    assert buys["action"] == "buy" and buys["calls"] == 2 and buys["beat_the_index"] == 1
    assert buys["average_difference_pct"] == 7.5 and out["too_recent"] == 1
    assert [c["symbol"] for c in buys["detail"]] == ["WIN", "LOSE"]


def test_no_index_prices_means_no_scorecard(monkeypatch, workspace):
    monkeypatch.setattr(scorecard.yahoo, "get_history_many", lambda symbols, period: (None, "yfinance blocked"))
    out = scorecard.compute(transactions=TRANSACTIONS, totals={}, today=date(2026, 3, 2))
    assert out == {"error": "yfinance blocked", "against_index": None, "calls": None}
