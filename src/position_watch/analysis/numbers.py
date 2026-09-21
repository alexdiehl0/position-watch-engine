"""Reading numbers off a vendor's payload, once, the same way everywhere.

Each source hands back whatever its own API felt like sending: a percentage in
one field and a fraction in the next, a string where a float was documented, a
key that is simply absent. These helpers settle that in one place so the rest of
the analysis code can assume a plain float or None.

Nothing here estimates. A figure that cannot be read, or whose scale cannot be
settled from the payload itself, comes back as None with the reason, for the
caller to report as a gap -- never as a filled-in value.
"""

# Above this, a trailing dividend yield is far likelier to be a fraction that has
# already been multiplied by 100 than a real equity payout. The highest genuine
# yields in the pool sit in the low teens.
MAX_PLAUSIBLE_YIELD_PCT = 30.0


def to_float(value):
    """A float, or None when the value is missing or unparseable.

    None rather than 0.0 on purpose: a zero flows into sums, ratios and money
    columns as though it were a reading, and both briefs forbid presenting a
    figure that was never returned.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_pct(value, already_pct: bool, digits: int = 2):
    """A percentage, from a value the caller already knows the scale of."""
    number = to_float(value)
    if number is None:
        return None
    return round(number if already_pct else number * 100, digits)


def dividend_yield_pct(info: dict) -> tuple[float | None, str | None]:
    """A Yahoo `info` dict's dividend yield as a percentage, and any gap.

    `dividendYield` has been a fraction (0.0063) in some yfinance versions and a
    percentage (0.63) in others, and no single value distinguishes them: read as
    a fraction, a real 0.63% yield becomes 63% -- enough to win full marks on the
    client's high-dividend preference and carry the stock onto the watchlist.

    So the scale is settled against the payload rather than guessed. The dividend
    rate over the price is unambiguous, `trailingAnnualDividendYield` is
    documented as a fraction, and only if neither is present is `dividendYield`
    taken at face value -- as a percentage, which is what the current library
    returns and what the stock and ETF paths already assume. A leftover figure
    too large to be a real payout is reported, not scored.
    """
    rate = to_float(info.get("trailingAnnualDividendRate"))
    price = to_float(info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose"))
    if rate is not None and price:
        return round(rate / price * 100, 3), None

    fraction = to_float(info.get("trailingAnnualDividendYield"))
    if fraction is not None:
        return round(fraction * 100, 3), None

    raw = to_float(info.get("dividendYield"))
    if raw is None:
        return None, None  # no dividend data at all; the caller says so in its own words
    if raw > MAX_PLAUSIBLE_YIELD_PCT:
        return None, (
            f"dividend yield: Yahoo returned {raw:g} and the payload carried no dividend rate or price "
            "to settle its scale against, so it is not a usable percentage"
        )
    return round(raw, 3), None
