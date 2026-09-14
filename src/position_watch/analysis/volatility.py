"""Volatility computed from daily closing prices -- the same method for every
stock and ETF, rather than whichever figure a data vendor happens to return.

3-month volatility = standard deviation of daily log returns over the last
~63 trading days, annualised with sqrt(252), in %. Closes are Yahoo's
split- and dividend-adjusted prices, so a split or a spin-off doesn't show up
as a fake one-day crash (a vendor's unadjusted figure can, e.g. 105% for a
steady stock that spun off a business).
"""

import math
import statistics

SOURCE = "computed from yfinance daily closes"
MIN_RETURNS = 40  # about two months of trading days; fewer is too thin to call

# Plain convention for labelling annualised volatility; the broad US market
# usually runs around 15-20%.
BANDS = ((20, "low"), (35, "moderate"))


def from_closes(closes) -> float | None:
    """Annualised volatility in % from daily closes (oldest first), or None if too few."""
    prices = [float(c) for c in closes if c is not None and c == c and c > 0]  # drops NaN and bad prints
    returns = [math.log(b / a) for a, b in zip(prices, prices[1:], strict=False)]
    if len(returns) < MIN_RETURNS:
        return None
    return round(statistics.stdev(returns) * math.sqrt(252) * 100, 2)


def level(vol_pct) -> str | None:
    if vol_pct is None:
        return None
    return next((label for limit, label in BANDS if vol_pct < limit), "high")
