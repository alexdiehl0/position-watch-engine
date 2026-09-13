from position_watch.analysis import pnl


def test_totals_convert_eur_with_the_given_rate(holdings, review_data):
    result = pnl.compute(holdings, review_data, review_data["fx"])
    by_symbol = {p["symbol"]: p for p in result["positions"]}

    assert by_symbol["USDS"]["value_usd"] == 1500  # 100 shares at the live 15.00
    assert by_symbol["EURS"]["value"] == 2500  # stays in EUR per position...
    assert round(by_symbol["EURS"]["value_usd"], 2) == 2750  # ...and converts at 1.1 for the USD total
    assert result["fx_used"]["EUR"]["source"] == "ECB reference rate"
    assert result["totals"]["positions"] == 4  # the closed position is not counted


def test_missing_rate_falls_back_to_broker_value_and_flags_it(holdings, review_data):
    result = pnl.compute(holdings, review_data, fx={})
    eurs = next(p for p in result["positions"] if p["symbol"] == "EURS")

    assert eurs["value_usd"] == 2200 + 220  # broker invested + unrealized
    assert any("no EUR->USD rate" in f for f in eurs["flags"])


def test_snapshot_price_is_used_when_no_live_price(holdings, review_data):
    result = pnl.compute(holdings, review_data, review_data["fx"])
    etf = next(p for p in result["positions"] if p["symbol"] == "ETFX")

    assert etf["price"] == 110.0
    assert etf["price_source"] == "broker snapshot"


def test_summary_line_mentions_value_return_and_dividends(holdings, review_data):
    line = pnl.summary_line(pnl.compute(holdings, review_data, review_data["fx"])["totals"])

    assert line.startswith("Portfolio value $")
    assert "dividends received $70" in line
    assert "positions at live prices" in line
