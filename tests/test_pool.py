from datetime import date

from position_watch.analysis import pool


def test_score_rewards_discount_and_yield():
    score, why = pool._score(pe=10, median_pe=20, yld=4, payout=50)
    assert score == 1.5  # 50% discount -> +1, 4% yield -> +0.5
    assert "50% below" in why


def test_no_value_credit_above_pe_40():
    score, why = pool._score(pe=60, median_pe=200, yld=None, payout=None)
    assert score == 0
    assert "but over 40" in why


def test_stretched_payout_is_penalised():
    covered, _ = pool._score(pe=15, median_pe=15, yld=6, payout=60)
    stretched, why = pool._score(pe=15, median_pe=15, yld=6, payout=120)
    assert stretched == covered - 0.5
    assert "over 100%" in why


def test_loss_makers_score_negative():
    score, why = pool._score(pe=-5, median_pe=12, yld=None, payout=None)
    assert score < 0
    assert "loss-making" in why


def test_pick_takes_top_scores_then_rotation():
    universe = pool.load_universe()
    stocks = {
        s: {"screen": {"passed": True, "score": sc}}
        for s, sc in {"AAA": 1.5, "BBB": 1.0, "CCC": 0.5, "DDD": 0.1}.items()
    }
    stocks["CCC"]["shortlisted"] = {"times": 3, "first": "2026-01-01", "last": "2026-01-05"}
    today = date(2026, 1, 10)

    picks = pool.pick({"stocks": stocks}, universe, exclude={"AAA"}, today=today)

    assert picks[:2] == [("BBB", "top"), ("CCC", "top")]
    assert picks[2] == ("DDD", "rotation")  # never shortlisted, so it comes first in rotation
    assert stocks["BBB"]["shortlisted"] == {"times": 1, "first": "2026-01-10", "last": "2026-01-10"}


def test_refresh_is_due_after_refresh_days():
    universe = pool.load_universe()
    assert pool.needs_refresh({"refreshed": None}, universe, date(2026, 1, 10))
    assert not pool.needs_refresh({"refreshed": "2026-01-05"}, universe, date(2026, 1, 10))
    assert pool.needs_refresh({"refreshed": "2026-01-03"}, universe, date(2026, 1, 10))


# ---- non-US stocks: screened through Yahoo, because the free US plans have none ----

EURO_INFO = {"trailingPE": 11.5, "forwardPE": 9.1, "dividendYield": 4.5, "payoutRatio": 0.49,
             "marketCap": 176_000_000_000, "currency": "EUR", "sector": "Energy", "longName": "TotalEnergies SE",
             "beta": 0.9}  # fmt: skip


def _screen_european(monkeypatch, info, cap_floor=2000):
    monkeypatch.setattr(pool.yahoo, "get_info", lambda s: (info, None) if info else (None, "yfinance returned nothing"))
    monkeypatch.setattr(pool.yahoo, "get_closes_many", lambda symbols: ({}, None))
    monkeypatch.setattr(pool.fx, "get_rate", lambda base, quote="USD": ({"rate": 1.1}, None))
    stocks = {"stocks": {"TTE.PA": {"market": "europe", "name": "TotalEnergies SE", "industry": "Oil & Gas"}}}
    pool.screen(stocks, {"min_market_cap_usd_m": cap_floor}, date(2026, 1, 2))
    return stocks["stocks"]["TTE.PA"]["screen"]


def test_a_european_stock_is_screened_from_yahoo(monkeypatch):
    screen = _screen_european(monkeypatch, EURO_INFO)
    assert screen["passed"] and screen["dividend_yield_pct"] == 4.5 and screen["payout_ratio_pct"] == 49.0
    assert round(screen["market_cap_usd_m"]) == 193_600  # converted at the ECB rate
    assert "no 5-yr history" in screen["why"]  # said, never filled in
    assert screen["gaps"] == ["five_yr_median_pe: Yahoo has no P/E history"]


def test_a_missing_market_cap_is_a_gap_not_a_rejection(monkeypatch):
    screen = _screen_european(monkeypatch, {**EURO_INFO, "marketCap": None})
    assert screen["passed"] and screen["market_cap_usd_m"] is None
    assert "market cap: not returned by Yahoo" in screen["gaps"]


def test_a_european_stock_with_no_data_is_dropped(monkeypatch):
    assert _screen_european(monkeypatch, None)["filtered_out"] == "no data from Yahoo"


def test_a_sub_one_percent_yield_does_not_become_a_watchlist_pick(monkeypatch):
    # Infineon, 19-21 Sept 2026: Yahoo sent 0.63 for a 0.63% yield, the screen
    # read it as a fraction and recorded 63%, and full marks on the client's
    # high-dividend preference took the stock to 11th of 412 in the pool.
    info = {**EURO_INFO, "dividendYield": 0.63, "trailingAnnualDividendRate": 0.35, "currentPrice": 55.5}
    screen = _screen_european(monkeypatch, info)
    assert round(screen["dividend_yield_pct"], 2) == 0.63
    assert "yield 0.6%" in screen["why"]


def test_forward_pe_stands_in_for_a_missing_history():
    cheaper, why = pool._score(pe=20, median_pe=None, yld=3, payout=40, forward_pe=14)
    flat, _ = pool._score(pe=20, median_pe=None, yld=3, payout=40, forward_pe=20)
    assert cheaper > flat and "vs forward 14.0, no 5-yr history" in why


def test_an_index_can_seed_the_pool_when_the_plan_allows_it(monkeypatch, workspace):
    monkeypatch.setattr(
        pool.fmp, "get_index_constituents", lambda index: ([{"symbol": "AAA"}, {"symbol": "BBB"}], None)
    )
    monkeypatch.setattr(pool.finnhub, "get_peers", lambda symbol, grouping: ([], None))
    monkeypatch.setattr(pool.finnhub, "get_company_profile", lambda symbol: ({"name": symbol}, None))
    universe = {"seeds": {}, "pool_cap": 50, "index_seeds": ["sp500"], "refresh_days": 7}
    p = {"stocks": {}}
    pool.refresh(p, universe, [], date(2026, 1, 2))
    assert sorted(p["stocks"]) == ["AAA", "BBB"] and p["stocks"]["AAA"]["via"] == "in the SP500"


def test_a_refused_index_is_a_note_not_a_failure(monkeypatch, workspace):
    monkeypatch.setattr(pool.fmp, "get_index_constituents", lambda index: (None, "HTTP 402: needs a paid plan"))
    monkeypatch.setattr(pool.finnhub, "get_peers", lambda symbol, grouping: ([], None))
    universe = {"seeds": {}, "pool_cap": 50, "index_seeds": ["sp500"], "refresh_days": 7}
    notes = pool.refresh({"stocks": {}}, universe, [], date(2026, 1, 2))
    assert any("sp500 constituents: HTTP 402" in n for n in notes)
