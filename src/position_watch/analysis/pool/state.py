"""Where the pool lives and how it grows: the file, and the weekly refresh.

The pool is every stock the system knows about and might put on the watchlist,
kept in state/stock_pool.json. It is refreshed when older than `refresh_days`
(config/stock_universe.json) from the seed list, plus Finnhub's similar
companies for each stock held and for each watchlist stock rated Buy or Add in
the past week. Every stock ever shortlisted stays, with a count of how often,
so the pool doubles as the record of what has been suggested.
"""

import json
import re
from datetime import date, timedelta

from position_watch import settings, suggestion_log
from position_watch.sources import finnhub, fmp, yahoo


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
        "Stocks the watchlist is picked from. Written by position_watch/analysis/pool/ during the "
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

    def add(symbol, via, market="us"):
        nonlocal added
        if symbol in stocks or (market == "us" and not US_TICKER.fullmatch(symbol)):
            return
        stocks[symbol] = {"added": today_s, "via": via, **({"market": market} if market != "us" else {})}
        added += 1

    seeded = set()
    for group, symbols in universe["seeds"].items():
        market = "europe" if group.strip().lower() == "europe" else "us"  # screened through Yahoo
        for s in symbols:
            seeded.add(s)
            add(s, f"starting list: {group}", market)
    for s in stocks:
        stocks[s]["seed"] = s in seeded

    # A whole index, when the data plan allows it: 500 names beats any hand-written
    # list. The free plan refuses this, and the seed list above carries the pool.
    for index in universe.get("index_seeds") or []:
        members, err = fmp.get_index_constituents(index)
        if err:
            notes.append(f"{index} constituents: {err}")
            continue
        for member in members or []:
            symbol = (member.get("symbol") or "").upper()
            seeded.add(symbol)
            add(symbol, f"in the {index.upper()}")

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
        if "name" in e:
            continue
        if e.get("market") == "europe":  # Finnhub's free plan has no non-US profiles
            info, err = yahoo.get_info(s)
            e["name"] = (info or {}).get("longName") or (info or {}).get("shortName")
            e["industry"] = (info or {}).get("industry") or (info or {}).get("sector")
        else:
            profile, err = finnhub.get_company_profile(s)
            e["name"] = (profile or {}).get("name") or None
            e["industry"] = (profile or {}).get("finnhubIndustry") or None

    pool["refreshed"] = today_s
    notes.insert(0, f"refreshed {today_s}: {added} added, {len(stocks)} in pool")
    return notes
