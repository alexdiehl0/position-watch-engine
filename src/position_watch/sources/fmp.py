"""Thin client for the Financial Modeling Prep (FMP) "stable" API.

First source in the fundamentals fallback (FMP -> Finnhub -> Yahoo). It never estimates or caches a fallback
value — every function returns (data, error) so callers can report a gap
explicitly instead of guessing.

FMP_API_KEY is read from the environment at call time and never logged or returned.
"""

import os

import requests

from position_watch import settings

BASE_URL = "https://financialmodelingprep.com/stable"

# Once FMP answers 429 (daily quota used up), further calls this run can only
# fail too, so they are skipped.
_quota_exhausted = False


def reset_run_state():
    global _quota_exhausted
    _quota_exhausted = False


def _get(path: str, params: dict):
    """Returns (data, error). data is the parsed JSON list/dict on success,
    or None on failure. error is a human-readable string on failure, or None.
    """
    key = os.environ.get("FMP_API_KEY")
    if not key:
        return None, "FMP_API_KEY not found in environment (.env)"

    global _quota_exhausted
    if _quota_exhausted:
        return None, "FMP daily quota used up earlier in this run"

    query = dict(params)
    query["apikey"] = key

    try:
        resp = requests.get(f"{BASE_URL}/{path}", params=query, timeout=15)
    except requests.RequestException as exc:
        return None, settings.redact(f"request to {path} failed: {exc}")

    if resp.status_code == 429:
        _quota_exhausted = True
    if resp.status_code != 200:
        detail = resp.text[:300].replace(key, "***")
        return None, f"FMP returned {resp.status_code} for {path}: {detail}"

    try:
        data = resp.json()
    except ValueError:
        return None, f"FMP returned non-JSON response for {path}"

    if not data:
        return None, f"FMP returned no data for {path}"

    return data, None


def get_quote(symbol: str):
    return _get("quote", {"symbol": symbol})


def get_profile(symbol: str):
    return _get("profile", {"symbol": symbol})


def get_ratios(symbol: str, limit: int = 5):
    """Annual ratios, most recent first — used for current P/E and the
    trailing-years series needed for a 5-year average."""
    return _get("ratios", {"symbol": symbol, "period": "annual", "limit": limit})


def get_key_metrics(symbol: str, limit: int = 1):
    return _get("key-metrics", {"symbol": symbol, "period": "annual", "limit": limit})


def get_price_target_consensus(symbol: str):
    return _get("price-target-consensus", {"symbol": symbol})


def get_analyst_estimates(symbol: str, limit: int = 2):
    return _get("analyst-estimates", {"symbol": symbol, "period": "annual", "limit": limit})


def get_grades_consensus(symbol: str):
    return _get("grades-consensus", {"symbol": symbol})


def get_dividends(symbol: str, limit: int = 5):
    """Per-payment dividend history — used for growth-streak and coverage.
    Current plan caps `limit` at 5, so a growth streak beyond ~1-2 years of
    payments may be reported as a data gap rather than computed."""
    return _get("dividends", {"symbol": symbol, "limit": limit})


def get_earnings(symbol: str, limit: int = 5):
    """Recent quarterly earnings incl. actual vs. estimated — used for the
    beat/miss surprise pattern. Current plan caps `limit` at 5."""
    return _get("earnings", {"symbol": symbol, "limit": limit})


def get_index_constituents(index: str = "sp500"):
    """Every member of an index (paid plans only; the free plan refuses it, and
    the caller falls back to the seed list). `index`: sp500, nasdaq or dowjones."""
    return _get(f"{index}-constituent", {})


def get_sector_pe_snapshot(exchange: str, as_of_date: str):
    return _get("sector-pe-snapshot", {"exchange": exchange, "date": as_of_date})
