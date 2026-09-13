"""Thin client for Yahoo Finance via the yfinance library.

Free, no API key required. The last source in the fundamentals fallback:
FMP's free plan refuses most symbols and Finnhub's free tier has no ETF
data, so Yahoo is often the only source for ETFs and non-US listings.
It is unofficial and often unreachable from cloud servers.

Same contract as fmp.py / finnhub.py: every function returns
(data, error). Nothing is estimated or cached as a fallback value — a
missing field comes back as an explicit gap for the caller to report.
"""

import yfinance as yf

from position_watch import instruments


def _ticker(symbol: str):
    # A broker's symbol doesn't always match Yahoo's (a Paris listing needs
    # ".PA", a London ETF ".L"); the mapping lives in the workspace's
    # config/instruments.json.
    return yf.Ticker(instruments.yahoo_symbol(symbol))


def get_info(symbol: str):
    """Raw Yahoo 'quoteSummary' info: price, valuation ratios, dividend
    yield, analyst targets, sector/industry, currency, etc."""
    try:
        info = _ticker(symbol).info
    except Exception as exc:
        return None, f"yfinance info request for {symbol} failed: {exc}"

    if not info or len(info) <= 3:
        return None, f"yfinance returned no fundamentals for {symbol} (check ticker/suffix mapping)"

    return info, None


def get_dividends(symbol: str, limit: int = 8):
    """Per-payment dividend history, most recent first."""
    try:
        series = _ticker(symbol).dividends
    except Exception as exc:
        return None, f"yfinance dividends request for {symbol} failed: {exc}"

    if series is None or series.empty:
        return None, f"yfinance returned no dividend history for {symbol}"

    events = [{"date": ts.date().isoformat(), "amount": float(amount)} for ts, amount in series.items()]
    events.reverse()  # yfinance returns oldest-first
    return events[:limit], None


def get_recommendations(symbol: str):
    """Monthly analyst recommendation counts (strongBuy/buy/hold/sell/
    strongSell), most recent period last as returned by yfinance."""
    try:
        df = _ticker(symbol).recommendations
    except Exception as exc:
        return None, f"yfinance recommendations request for {symbol} failed: {exc}"

    if df is None or df.empty:
        return None, f"yfinance returned no analyst recommendations for {symbol}"

    return df.to_dict(orient="records"), None


def get_price_target(symbol: str):
    """Analyst price target consensus, derived from get_info()."""
    info, err = get_info(symbol)
    if err:
        return None, err

    target = {
        "targetMeanPrice": info.get("targetMeanPrice"),
        "targetHighPrice": info.get("targetHighPrice"),
        "targetLowPrice": info.get("targetLowPrice"),
        "numberOfAnalystOpinions": info.get("numberOfAnalystOpinions"),
        "recommendationKey": info.get("recommendationKey"),
    }
    if target["targetMeanPrice"] is None:
        return None, f"yfinance returned no analyst price target for {symbol}"

    return target, None
