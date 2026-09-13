"""Thin client for the Finnhub API.

Mirrors the structure of fmp.py: every function returns (data, error)
so callers can report a gap explicitly instead of guessing. No fallback
values are estimated or cached here.

FINNHUB_API_KEY is read from the environment at call time and never logged or returned.
"""

import collections
import datetime
import os
import time

import requests

from position_watch import settings

BASE_URL = "https://finnhub.io/api/v1"

# The free tier allows 60 calls a minute; the daily run (stock pool screen +
# full evaluations) makes a few hundred, so calls are spaced to stay under it.
MAX_CALLS_PER_MINUTE = 55
_recent_calls = collections.deque()

# Within one run the same data is often asked for twice (the pool screen and
# the full evaluation both read a watchlist stock's metrics), so successful
# responses are kept for the life of the process.
_cache = {}

# An endpoint the plan doesn't include answers 403 for every symbol (on the
# free tier: price targets, dividends). After it has refused this many
# different symbols with no success, it is skipped for the rest of the run
# instead of spending a paced call on each symbol. A 403 can also be
# per-symbol (non-US listings), hence the threshold.
FORBIDDEN_AFTER = 3
_forbidden_counts = collections.Counter()
_endpoint_worked = set()


def reset_run_state():
    _cache.clear()
    _forbidden_counts.clear()
    _endpoint_worked.clear()


def _throttle():
    now = time.monotonic()
    while _recent_calls and now - _recent_calls[0] >= 60:
        _recent_calls.popleft()
    if len(_recent_calls) >= MAX_CALLS_PER_MINUTE:
        time.sleep(60 - (now - _recent_calls[0]) + 0.1)
    _recent_calls.append(time.monotonic())


def _get(path: str, params: dict):
    """Returns (data, error). data is the parsed JSON list/dict on success,
    or None on failure. error is a human-readable string on failure, or None.
    """
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return None, "FINNHUB_API_KEY not found in environment (.env)"

    cache_key = (path, tuple(sorted(params.items())))
    if cache_key in _cache:
        return _cache[cache_key], None
    if path not in _endpoint_worked and _forbidden_counts[path] >= FORBIDDEN_AFTER:
        return None, f"Finnhub: {path} is not included in this plan (skipped after {FORBIDDEN_AFTER} refusals)"

    query = dict(params)
    query["token"] = key

    for attempt in range(3):
        _throttle()
        try:
            resp = requests.get(f"{BASE_URL}/{path}", params=query, timeout=15)
        except requests.RequestException as exc:
            return None, settings.redact(f"request to {path} failed: {exc}")
        if resp.status_code != 429 or attempt == 2:
            break
        time.sleep(10)  # rate-limited anyway (e.g. another process): wait, retry

    if resp.status_code == 403:
        _forbidden_counts[path] += 1
    if resp.status_code != 200:
        detail = resp.text[:300].replace(key, "***")
        return None, f"Finnhub returned {resp.status_code} for {path}: {detail}"

    try:
        data = resp.json()
    except ValueError:
        return None, f"Finnhub returned non-JSON response for {path}"

    if not data:
        return None, f"Finnhub returned no data for {path}"

    _endpoint_worked.add(path)
    _cache[cache_key] = data
    return data, None


def get_company_profile(symbol: str):
    """Basic company profile: name, finnhubIndustry, country, weburl."""
    return _get("stock/profile2", {"symbol": symbol})


def get_peers(symbol: str, grouping: str = "industry"):
    """Similar companies (tickers), grouped by "industry", "sector" or
    "subIndustry". Largest first for big companies; for small or non-US
    ones it is mostly small caps and foreign listings."""
    return _get("stock/peers", {"symbol": symbol, "grouping": grouping})


def get_quote(symbol: str):
    """Current price snapshot (fields: c=current, pc=prev close, etc.)."""
    return _get("quote", {"symbol": symbol})


def get_ratios(symbol: str):
    """Basic financials (includes valuation/profitability ratios)."""
    return _get("stock/metric", {"symbol": symbol, "metric": "all"})


def get_recommendation_trends(symbol: str):
    """Analyst buy/hold/sell recommendation counts, most recent periods first."""
    return _get("stock/recommendation", {"symbol": symbol})


def get_price_target(symbol: str):
    """Analyst price target consensus."""
    return _get("stock/price-target", {"symbol": symbol})


def get_dividends(symbol: str, from_date: str = None, to_date: str = None):
    """Per-payment dividend history. Defaults to the trailing 5 years if no
    date range is given, since Finnhub requires an explicit range."""
    if to_date is None:
        to_date = datetime.date.today().isoformat()
    if from_date is None:
        from_date = (datetime.date.today() - datetime.timedelta(days=5 * 365)).isoformat()
    return _get("stock/dividend", {"symbol": symbol, "from": from_date, "to": to_date})


def get_general_news(category: str = "general"):
    """Market-wide news headlines (not filtered to any one symbol)."""
    return _get("news", {"category": category})


def get_company_news(symbol: str, from_date: str = None, to_date: str = None):
    """Company-specific news headlines. Defaults to the trailing 7 days if
    no date range is given, since Finnhub requires an explicit range."""
    if to_date is None:
        to_date = datetime.date.today().isoformat()
    if from_date is None:
        from_date = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    return _get("company-news", {"symbol": symbol, "from": from_date, "to": to_date})
