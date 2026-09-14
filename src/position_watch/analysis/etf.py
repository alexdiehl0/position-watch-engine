"""Evidence for the client's ETF holdings, which follow a simple strategy:
they are core positions topped up now and then, never traded. So instead of
single-stock valuation (P/E, PEG, payout ratio -- meaningless for a fund),
this gathers what a top-up decision rests on:

  - price vs the client's average cost
  - how far the price sits below its 52-week high, and vs its 200-day average
  - the position's weight in the portfolio
  - the fund's fee (expense ratio) and dividend yield, as context

Fund data comes from yfinance, the only source that covers these ETFs; it is
unreliable from cloud servers, so every missing field is listed in
`data_gaps` rather than estimated. When the live price is missing, the
broker's last price is used for the price-vs-cost figure and labelled as a
snapshot. A live price in a different currency from the position is not used.
"""

from datetime import datetime, timezone

from position_watch.analysis import volatility
from position_watch.sources import yahoo


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(a, b):
    return round((a - b) / b * 100, 2) if a is not None and b else None


def evaluate_etf(symbol: str, row: dict) -> dict:
    gaps = []
    currency = (row.get("currency") or "USD").upper()
    avg_cost = _f(row.get("avg_price"))
    broker_price = _f(row.get("last_price"))

    info, err = yahoo.get_info(symbol)
    if err:
        gaps.append(f"live fund data: {err}")
        info = {}

    price = _f(info.get("regularMarketPrice") or info.get("previousClose"))
    live_currency = (info.get("currency") or "").upper()
    if price is not None and live_currency and live_currency != currency:
        gaps.append(f"live price is quoted in {live_currency}, not the position's {currency}; not used")
        price = None
    if price is None and not err:
        gaps.append("live price: not returned by yfinance")

    reference_price = price if price is not None else broker_price
    reference_source = "yfinance" if price is not None else "broker snapshot"

    high = _f(info.get("fiftyTwoWeekHigh"))
    low = _f(info.get("fiftyTwoWeekLow"))
    ma200 = _f(info.get("twoHundredDayAverage"))
    expense = _f(info.get("netExpenseRatio"))
    div_yield = _f(info.get("dividendYield"))
    beta = _f(info.get("beta3Year") or info.get("beta"))

    for name, value in (
        ("52-week high/low", high),
        ("200-day average", ma200),
        ("expense ratio", expense),
        ("dividend yield", div_yield),
    ):
        if value is None:
            gaps.append(f"{name}: not returned by yfinance")

    closes, closes_err = yahoo.get_closes(symbol)
    vol = volatility.from_closes(closes) if not closes_err else None
    if vol is None:
        gaps.append(f"3-month volatility: {closes_err or 'too little price history to compute'}")

    below_high = round((high - price) / high * 100, 2) if price is not None and high else None
    swing = round((high - low) / low * 100, 1) if high and low else None
    if beta is None:
        gaps.append("beta: not returned by yfinance")

    return {
        "symbol": symbol,
        "name": info.get("longName") or row.get("name"),
        "currency": currency,
        "price": price,
        "sources": {
            **({"price": "yfinance"} if price is not None else {}),
            **({"volatility_3m_pct": volatility.SOURCE} if vol is not None else {}),
        },
        "volatility_3m_pct": vol,
        "reference_price": reference_price,
        "reference_price_source": reference_source,
        "avg_cost": avg_cost,
        "vs_avg_cost_pct": _pct(reference_price, avg_cost),
        "week52_high": high,
        "week52_low": low,
        "below_52w_high_pct": below_high,
        "ma200": ma200,
        "vs_200d_pct": _pct(price, ma200),
        "week52_swing_pct": swing,
        "beta_3y": beta,
        "expense_ratio_pct": expense,
        "dividend_yield_pct": div_yield,
        "allocation_pct": _f(row.get("allocation_pct")),
        "data_as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data_gaps": gaps or None,
    }
