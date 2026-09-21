import math

from position_watch.analysis import etf, pool, stock, volatility
from position_watch.sources import finnhub, yahoo


def _zigzag(days, move=0.01, start=100.0):
    """Closes that go up and down by `move` each day: daily log-return stdev ≈ move."""
    closes, price = [start], start
    for i in range(days):
        price *= math.exp(move if i % 2 == 0 else -move)
        closes.append(price)
    return closes


def test_from_closes_annualises_daily_moves():
    assert abs(volatility.from_closes(_zigzag(62)) - 0.01 * math.sqrt(252) * 100) < 0.2  # about 15.9%
    assert volatility.from_closes(_zigzag(62) + [float("nan")]) == volatility.from_closes(_zigzag(62))
    assert volatility.from_closes(_zigzag(20)) is None  # too little history to call
    assert [volatility.level(v) for v in (15, 25, 60, None)] == ["low", "moderate", "high", None]


def test_stocks_prefer_the_computed_figure_over_the_vendors(monkeypatch):
    monkeypatch.setattr(stock, "_fmp_fields", lambda s: {})
    monkeypatch.setattr(stock, "_finnhub_fields", lambda s: {"volatility_3m_pct": 105.3, "beta": 1.1})
    monkeypatch.setattr(stock, "_yfinance_fields", lambda s: {})
    monkeypatch.setattr(stock.yahoo, "get_closes", lambda s: (_zigzag(62), None))
    values, sources, *_ = stock._gather_fundamentals("HON")
    assert abs(values["volatility_3m_pct"] - 15.9) < 0.2 and sources["volatility_3m_pct"] == volatility.SOURCE
    assert sources["beta"] == "Finnhub"

    monkeypatch.setattr(stock.yahoo, "get_closes", lambda s: (None, "yfinance blocked"))
    values, sources, *_ = stock._gather_fundamentals("HON")
    assert values["volatility_3m_pct"] == 105.3 and sources["volatility_3m_pct"] == "Finnhub"  # the fallback


def test_etfs_get_volatility_or_an_explicit_gap(monkeypatch):
    monkeypatch.setattr(etf.yahoo, "get_info", lambda s: ({"regularMarketPrice": 10.0, "currency": "USD"}, None))
    monkeypatch.setattr(etf.yahoo, "get_closes", lambda s: (_zigzag(62), None))
    e = etf.evaluate_etf("ETFX", {"currency": "USD", "avg_price": "9"})
    assert abs(e["volatility_3m_pct"] - 15.9) < 0.2 and e["sources"]["volatility_3m_pct"] == volatility.SOURCE

    monkeypatch.setattr(etf.yahoo, "get_closes", lambda s: (None, "yfinance returned no price history for ETFX"))
    e = etf.evaluate_etf("ETFX", {"currency": "USD", "avg_price": "9"})
    assert e["volatility_3m_pct"] is None
    assert "3-month volatility: yfinance returned no price history for ETFX" in e["data_gaps"]


def test_pool_screen_computes_volatility_in_one_request(monkeypatch):
    metric = {"metric": {"peTTM": 12, "currentDividendYieldTTM": 3, "marketCapitalization": 50_000,
                         "3MonthADReturnStd": 99.0}}  # fmt: skip
    monkeypatch.setattr(finnhub, "get_ratios", lambda s: (metric, None))
    requests = []
    monkeypatch.setattr(yahoo, "get_closes_many",
                        lambda symbols: requests.append(symbols) or ({"AAA": _zigzag(62)}, None))  # fmt: skip
    p = {"stocks": {"AAA": {}, "BBB": {}}}
    pool.screen(p, {"min_market_cap_usd_m": 10_000}, __import__("datetime").date(2026, 1, 2))

    assert requests == [["AAA", "BBB"]]
    a, b = p["stocks"]["AAA"]["screen"], p["stocks"]["BBB"]["screen"]
    assert abs(a["volatility_3m_pct"] - 15.9) < 0.2 and a["volatility_source"] == volatility.SOURCE
    assert b["volatility_3m_pct"] == 99.0 and b["volatility_source"] == "Finnhub"
