from position_watch.analysis import numbers


def test_to_float_reports_nothing_rather_than_zero():
    # A zero would flow into sums, ratios and money columns as if it were a reading.
    assert numbers.to_float("12.5") == 12.5
    assert numbers.to_float(None) is None
    assert numbers.to_float("") is None
    assert numbers.to_float("n/a") is None


def test_to_pct_converts_only_when_told_to():
    assert numbers.to_pct(0.0342, already_pct=False) == 3.42
    assert numbers.to_pct(3.42, already_pct=True) == 3.42
    assert numbers.to_pct(None, already_pct=False) is None


def test_yield_comes_from_the_dividend_rate_when_the_payload_has_one():
    value, gap = numbers.dividend_yield_pct(
        {"trailingAnnualDividendRate": 1.60, "currentPrice": 32.0, "dividendYield": 0.05}
    )
    assert value == 5.0 and gap is None


def test_a_sub_one_percent_yield_is_not_inflated_a_hundredfold():
    # Infineon: Yahoo returned 0.63 for a 0.63% yield. Read as a fraction it
    # became 63%, which won full marks on the client's high-dividend preference
    # and carried the stock to 11th of 412 in the pool.
    value, gap = numbers.dividend_yield_pct({"trailingAnnualDividendRate": 0.35, "currentPrice": 55.5})
    assert round(value, 2) == 0.63 and gap is None

    # With no rate or price to settle the scale, the vendor's own figure stands
    # as a percentage rather than being multiplied up.
    value, gap = numbers.dividend_yield_pct({"dividendYield": 0.63})
    assert value == 0.63 and gap is None


def test_a_documented_fraction_is_scaled_up():
    value, gap = numbers.dividend_yield_pct({"trailingAnnualDividendYield": 0.045})
    assert value == 4.5 and gap is None


def test_an_implausible_yield_is_reported_not_scored():
    value, gap = numbers.dividend_yield_pct({"dividendYield": 63.0})
    assert value is None
    assert gap and "63" in gap


def test_no_dividend_data_is_silent():
    value, gap = numbers.dividend_yield_pct({})
    assert value is None and gap is None
