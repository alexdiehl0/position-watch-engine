"""What the current data keys can actually reach.

The free plans refuse a lot quietly (HTTP 402/403 with an empty body), and the
paid tiers differ mostly in *which endpoints* and *which markets* they open. So
rather than trusting a pricing page, this asks: one small request per endpoint,
reporting open / refused / error -- never a value, never a key.

    position-watch check-data
"""

import json
import os
import urllib.error
import urllib.request

from position_watch import settings

FMP = "https://financialmodelingprep.com"
FINNHUB = "https://finnhub.io/api/v1"

# (what it would give us, url template with {key})
FMP_CHECKS = (
    ("US quote (free tier)", f"{FMP}/stable/quote?symbol=AAPL&apikey={{key}}"),
    ("US fundamentals", f"{FMP}/stable/ratios?symbol=AAPL&limit=1&apikey={{key}}"),
    ("stock screener (a sector on demand)", f"{FMP}/stable/company-screener?sector=Energy&limit=3&apikey={{key}}"),
    ("S&P 500 constituents (a base universe)", f"{FMP}/stable/sp500-constituent?apikey={{key}}"),
    ("European quote (TotalEnergies)", f"{FMP}/stable/quote?symbol=TTE.PA&apikey={{key}}"),
    ("European fundamentals", f"{FMP}/stable/ratios?symbol=TTE.PA&limit=1&apikey={{key}}"),
    ("sector P/E (the gap in today's reports)", f"{FMP}/stable/sector-pe-snapshot?date=2026-01-02&apikey={{key}}"),
    ("analyst estimates", f"{FMP}/stable/analyst-estimates?symbol=AAPL&limit=1&apikey={{key}}"),
)

FINNHUB_CHECKS = (
    ("US metrics (free tier)", f"{FINNHUB}/stock/metric?symbol=AAPL&metric=all&token={{key}}"),
    ("similar companies", f"{FINNHUB}/stock/peers?symbol=AAPL&token={{key}}"),
    ("European metrics (Shell, London)", f"{FINNHUB}/stock/metric?symbol=SHEL.L&metric=all&token={{key}}"),
    ("index constituents", f"{FINNHUB}/index/constituents?symbol=%5EGSPC&token={{key}}"),
    ("price target", f"{FINNHUB}/stock/price-target?symbol=AAPL&token={{key}}"),
    ("dividend history", f"{FINNHUB}/stock/dividend?symbol=AAPL&from=2025-01-01&to=2026-01-01&token={{key}}"),
)


def _check(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            body = json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return {402: "refused (needs a paid plan)", 403: "refused (not on this plan)",
                429: "rate-limited"}.get(exc.code, f"HTTP {exc.code}")  # fmt: skip
    except Exception as exc:
        return f"failed: {type(exc).__name__}"
    if body in (None, [], {}) or (isinstance(body, dict) and (body.get("Error Message") or body.get("error"))):
        return "open but empty (not covered)"
    if isinstance(body, list):
        return f"open ({len(body)} rows)"
    return "open"


def probe() -> list:
    """[(provider, what it would give us, verdict)] -- no values, no keys."""
    rows = []
    for provider, key_name, checks in (
        ("FMP", "FMP_API_KEY", FMP_CHECKS),
        ("Finnhub", "FINNHUB_API_KEY", FINNHUB_CHECKS),
    ):
        key = os.environ.get(key_name)
        if not key:
            rows.append((provider, "(no key set)", f"{key_name} is not set"))
            continue
        for what, url in checks:
            rows.append((provider, what, settings.redact(_check(url.format(key=key)))))
    return rows
