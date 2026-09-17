"""Exchange rates for turning non-USD positions into USD portfolio totals.

Same contract as the other clients: get_rate() returns (data, error) and never
estimates. Sources, in order:
  1. ECB reference rates via api.frankfurter.dev -- free, no key, one fix per
     business day (so `date` can be the previous business day).
  2. yfinance (e.g. "EURUSD=X") -- live, but unreliable from cloud servers.
Finnhub and FMP forex endpoints are paywalled on the current plans.
"""

import requests
import yfinance as yf

# frankfurter.app now redirects here; the cloud allowlist needs this host.
ECB_RATES_URL = "https://api.frankfurter.dev/v1/latest"
ECB_HISTORY_URL = "https://api.frankfurter.dev/v1"  # /<date>?from=EUR&to=USD


def _from_ecb(base, quote):
    try:
        resp = requests.get(ECB_RATES_URL, params={"from": base, "to": quote}, timeout=15)
    except requests.RequestException as exc:
        return None, f"ECB rate request failed: {exc}"
    if resp.status_code != 200:
        return None, f"ECB rate returned {resp.status_code}"
    try:
        data = resp.json()
        rate = data["rates"][quote]
    except (ValueError, KeyError, TypeError):
        return None, "ECB rate response had no rate"
    return {"rate": rate, "date": data.get("date"), "source": "ECB reference rate"}, None


def _from_yfinance(base, quote):
    try:
        info = yf.Ticker(f"{base}{quote}=X").info
    except Exception as exc:
        return None, f"yfinance rate request failed: {exc}"
    rate = (info or {}).get("regularMarketPrice")
    if not rate:
        return None, "yfinance returned no rate"
    return {"rate": rate, "date": None, "source": "yfinance"}, None


def get_rate_on(day: str, base: str, quote: str = "USD"):
    """The ECB rate on a past date (the fix on or before it), for valuing an old
    trade in USD. Returns ({"rate", "date", "source"}, None) or (None, error)."""
    if base == quote:
        return {"rate": 1.0, "date": day, "source": "same currency"}, None
    try:
        resp = requests.get(f"{ECB_HISTORY_URL}/{day}", params={"from": base, "to": quote}, timeout=15)
    except requests.RequestException as exc:
        return None, f"ECB rate for {day} failed: {exc}"
    if resp.status_code != 200:
        return None, f"ECB rate for {day} returned {resp.status_code}"
    try:
        data = resp.json()
        return {"rate": data["rates"][quote], "date": data.get("date"), "source": "ECB (api.frankfurter.dev)"}, None
    except (ValueError, KeyError, TypeError):
        return None, f"ECB rate for {day} had no {base}->{quote} rate"


def get_rate(base: str, quote: str = "USD"):
    """Returns ({"rate", "date", "source"}, None) or (None, error).
    `rate` is how many units of `quote` one unit of `base` buys."""
    errors = []
    for fetch in (_from_ecb, _from_yfinance):
        data, err = fetch(base, quote)
        if data:
            return data, None
        errors.append(err)
    return None, "; ".join(errors)
