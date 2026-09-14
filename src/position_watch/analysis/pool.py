"""The stock pool: every stock the system knows about and might put on the
watchlist, kept in state/stock_pool.json.

Each day portfolio_review.py:
  1. refreshes the pool if it is older than `refresh_days` (config/stock_universe.json):
     the seed list, plus Finnhub's similar companies for each stock held and
     for each watchlist stock rated Buy or Add in the past week;
  2. screens every pool stock with one Finnhub call (P/E vs its own 5-year
     median, dividend yield, payout ratio, market cap; volatility and beta
     are recorded for display, not scored) and scores it on the
     client's stated preferences: strongly undervalued, high dividend;
  3. sends the top scorers plus a couple of rotation picks (the pool stocks it
     has looked at least recently) to the full evaluation as the watchlist.

Every stock ever put on the watchlist stays in the pool with a count of how
often, so the pool is also the record of what has been suggested.

Deterministic code only: the score is plain arithmetic on live figures, and
the Buy/Hold call on each watchlist stock is still made by the daily routine.
"""

import json
import re
import statistics
from datetime import date, timedelta

from position_watch import settings, suggestion_log
from position_watch.analysis import volatility
from position_watch.sources import finnhub, yahoo


def universe_path():
    return settings.config_dir() / "stock_universe.json"


def pool_path():
    return settings.state_dir() / "stock_pool.json"


# Finnhub's free plan covers US listings; peers such as 9618.HK are skipped.
US_TICKER = re.compile(r"[A-Z]{1,5}")


def load_universe():
    with open(universe_path()) as f:
        return json.load(f)


def load():
    if not pool_path().exists():
        return {"refreshed": None, "stocks": {}}
    with open(pool_path()) as f:
        return json.load(f)


def save(pool):
    pool_path().parent.mkdir(parents=True, exist_ok=True)
    pool["_about"] = (
        "Stocks the watchlist is picked from. Written by position_watch/analysis/pool.py during the "
        "daily review; settings and seed list are in config/stock_universe.json."
    )
    with open(pool_path(), "w") as f:
        json.dump(pool, f, indent=2, sort_keys=True)
        f.write("\n")


def needs_refresh(pool, universe, today):
    last = pool.get("refreshed")
    return not last or date.fromisoformat(last) <= today - timedelta(days=universe["refresh_days"])


def _recent_buy_adds(today, days=7):
    since = (today - timedelta(days=days)).isoformat()
    return sorted(
        {
            e["symbol"]
            for e in suggestion_log.load_entries()
            if e.get("scope") == "candidate"
            and e.get("date", "") >= since
            and (e.get("action") or "").lower() in ("buy", "add")
        }
    )


def refresh(pool, universe, held, today):
    """Adds seeds and similar companies, fetches names for new entries,
    drops stocks that failed the last screen (unless seeded or ever on the
    watchlist), and trims to `pool_cap`. Returns a list of notes."""
    stocks, notes, added = pool.setdefault("stocks", {}), [], 0
    today_s = today.isoformat()

    def add(symbol, via):
        nonlocal added
        if symbol in stocks or not US_TICKER.fullmatch(symbol):
            return
        stocks[symbol] = {"added": today_s, "via": via}
        added += 1

    seeded = set()
    for group, symbols in universe["seeds"].items():
        for s in symbols:
            seeded.add(s)
            add(s, f"starting list: {group}")
    for s in stocks:
        stocks[s]["seed"] = s in seeded

    sources = [s for s in held if US_TICKER.fullmatch(s)] + _recent_buy_adds(today)
    for src in dict.fromkeys(sources):
        for grouping in ("industry", "sector"):
            peers, err = finnhub.get_peers(src, grouping)
            if err:
                notes.append(f"similar companies for {src} ({grouping}): {err}")
                continue
            for p in peers:
                if p != src:
                    add(p, f"similar to {src}")

    for s in [
        s
        for s, e in stocks.items()
        if not e.get("seed") and not e.get("shortlisted") and e.get("screen") and not e["screen"].get("passed")
    ]:
        del stocks[s]

    if len(stocks) > universe["pool_cap"]:

        def keep(e):
            return bool(e.get("seed") or e.get("shortlisted"))

        others = sorted(
            (s for s, e in stocks.items() if not keep(e)),
            key=lambda s: -(stocks[s].get("screen") or {}).get("score", 99),
        )
        room = max(0, universe["pool_cap"] - sum(keep(e) for e in stocks.values()))
        for s in others[room:]:
            del stocks[s]

    for s, e in stocks.items():
        if "name" not in e:
            profile, err = finnhub.get_company_profile(s)
            e["name"] = (profile or {}).get("name") or None
            e["industry"] = (profile or {}).get("finnhubIndustry") or None

    pool["refreshed"] = today_s
    notes.insert(0, f"refreshed {today_s}: {added} added, {len(stocks)} in pool")
    return notes


# Above this P/E a stock isn't "strongly undervalued" whatever its own
# history says (a P/E of 130 can still be half its past level).
MAX_PE_FOR_VALUE = 40


def _score(pe, median_pe, yld, payout):
    """Plain-arithmetic fit to the client's preferences, roughly -2 to +2:
    discount to its own 5-year median P/E (up to +/-1, capped at 50%; no
    credit above MAX_PE_FOR_VALUE), dividend yield (up to +1 at 8%), minus
    a penalty for a stretched payout."""
    parts, score = [], 0.0
    if pe is not None and pe <= 0:
        score -= 0.5
        parts.append("loss-making (negative P/E)")
    elif pe is not None and median_pe and median_pe > 0:
        gap = (median_pe - pe) / median_pe
        value = max(-0.5, min(0.5, gap)) * 2
        if pe > MAX_PE_FOR_VALUE:
            value = min(value, 0.0)
        score += value
        parts.append(
            f"P/E {pe:.1f} vs 5-yr median {median_pe:.1f} ({abs(gap) * 100:.0f}% {'below' if gap >= 0 else 'above'})"
            + (f", but over {MAX_PE_FOR_VALUE}" if pe > MAX_PE_FOR_VALUE and gap > 0 else "")
        )
    elif pe is not None:
        parts.append(f"P/E {pe:.1f} (no 5-yr history)")
    if yld:
        score += min(yld, 8) / 8
        parts.append(f"yield {yld:.1f}%")
    if payout is not None and yld:
        if payout > 100:
            score -= 0.5
            parts.append(f"payout {payout:.0f}% (over 100%)")
        elif payout >= 80:
            score -= 0.2
            parts.append(f"payout {payout:.0f}% (high)")
        else:
            parts.append(f"payout {payout:.0f}%")
    return round(score, 3), " · ".join(parts)


def screen(pool, universe, today):
    """One Finnhub call per pool stock, plus one Yahoo request for everyone's
    daily prices (volatility is computed from those, Finnhub's figure is the
    fallback). Stores each result on the entry."""
    closes, _ = yahoo.get_closes_many(list(pool["stocks"]))
    for symbol, entry in pool["stocks"].items():
        data, err = finnhub.get_ratios(symbol)
        m = (data or {}).get("metric") or {}
        pe_series = (data or {}).get("series", {}).get("annual", {}).get("pe", [])
        pe_values = [p["v"] for p in pe_series[:5] if p.get("v") is not None]
        pe = m.get("peTTM") or m.get("peAnnual")
        # Median, not mean: one freak year (tiny earnings -> P/E of 1,000+)
        # would otherwise make almost any current P/E look cheap.
        median_pe = round(statistics.median(pe_values), 2) if len(pe_values) >= 3 else None
        yld = m.get("currentDividendYieldTTM") or m.get("dividendYieldIndicatedAnnual")
        payout = m.get("payoutRatioTTM") or m.get("payoutRatioAnnual")
        mcap = m.get("marketCapitalization")
        vol = volatility.from_closes((closes or {}).get(symbol) or [])

        result = {
            "date": today.isoformat(),
            "pe": pe,
            "five_yr_median_pe": median_pe,
            "dividend_yield_pct": yld,
            "payout_ratio_pct": payout,
            "market_cap_usd_m": mcap,
            "volatility_3m_pct": vol if vol is not None else m.get("3MonthADReturnStd"),
            "volatility_source": volatility.SOURCE
            if vol is not None
            else "Finnhub"
            if m.get("3MonthADReturnStd")
            else None,
            "beta": m.get("beta"),
            "score": None,
            "why": None,
            "passed": False,
            "filtered_out": None,
        }
        if err or not m:
            result["filtered_out"] = "no data from Finnhub"
        elif not mcap or mcap < universe["min_market_cap_usd_m"]:
            result["filtered_out"] = f"market cap under ${universe['min_market_cap_usd_m'] / 1000:.0f}B"
        elif pe is None and not yld:
            result["filtered_out"] = "no P/E or dividend to judge"
        else:
            result["score"], result["why"] = _score(pe, median_pe, yld, payout)
            result["passed"] = True
        entry["screen"] = result


def pick(pool, universe, exclude, today):
    """The day's watchlist: the `top` best scores, then `rotation` picks from
    the rest -- the stocks shortlisted least recently (never, first) -- so
    the whole pool gets a full look over time. Returns [(symbol, slot)]."""
    eligible = [s for s, e in pool["stocks"].items() if s not in exclude and (e.get("screen") or {}).get("passed")]
    by_score = sorted(eligible, key=lambda s: -pool["stocks"][s]["screen"]["score"])
    top = by_score[: universe["watchlist"]["top"]]
    rest = [s for s in by_score if s not in top]
    rest.sort(key=lambda s: (pool["stocks"][s].get("shortlisted") or {}).get("last") or "")
    rotation = rest[: universe["watchlist"]["rotation"]]

    picks = [(s, "top") for s in top] + [(s, "rotation") for s in rotation]
    for s, _ in picks:
        record = pool["stocks"][s].setdefault("shortlisted", {"times": 0, "first": today.isoformat()})
        record["times"] += 1
        record["last"] = today.isoformat()
    return picks


def summary(pool, picks, held, today):
    """What the dashboard shows: pool size, and one row per pool stock."""
    slot = dict(picks)
    rows = []
    for s, e in pool["stocks"].items():
        sc = e.get("screen") or {}
        shortlisted = e.get("shortlisted") or {}
        rows.append(
            {
                "symbol": s,
                "name": e.get("name"),
                "industry": e.get("industry"),
                "via": e.get("via"),
                "added": e.get("added"),
                **{
                    k: sc.get(k)
                    for k in (
                        "pe",
                        "five_yr_median_pe",
                        "dividend_yield_pct",
                        "payout_ratio_pct",
                        "market_cap_usd_m",
                        "volatility_3m_pct",
                        "beta",
                        "score",
                        "why",
                        "passed",
                        "filtered_out",
                    )
                },
                "times_on_watchlist": shortlisted.get("times", 0),
                "picked_today": slot.get(s),
                "first_time": shortlisted.get("first") == today.isoformat(),
                "held": s in held,
            }
        )
    rows.sort(key=lambda r: (not r["passed"], -(r["score"] or 0)))
    return {
        "refreshed": pool.get("refreshed"),
        "size": len(rows),
        "passed": sum(1 for r in rows if r["passed"]),
        "stocks": rows,
    }
