"""Choosing the day's watchlist out of the pool, and describing it.

The first `focus` slots go to the best-scoring stocks answering whatever the
client asked for (a sector, a theme, named tickers), then the best overall,
then `rotation` picks so the whole pool gets looked at over time. Anything he
asked to avoid is never eligible.
"""

import re

from position_watch import client_requests
from position_watch.analysis.pool.screening import screen_subset
from position_watch.analysis.pool.sectors import _entry_for, matches_request
from position_watch.analysis.pool.state import US_TICKER
from position_watch.sources import finnhub

FOCUS_KINDS = client_requests.FOCUS


def _requested(kind: str, requests: list) -> dict | None:
    return next((r for r in requests if r.get("kind") == kind and (r.get("value") or "").strip()), None)


def pick(pool, universe, exclude, today, requests=()):
    """The day's watchlist. With a focus request (sector, theme or tickers) the
    first `focus` slots go to the best-scoring stocks that answer it, then the
    best overall, then `rotation` picks the whole pool gets looked at through.
    Anything the client asked to avoid is never eligible. Returns [(symbol, slot)]."""
    requests = list(requests)
    wanted = universe["watchlist"]
    size = _requested("size", requests)
    limit = min(12, max(2, int(size["value"]))) if size and str(size["value"]).strip().isdigit() else None

    avoid = [r for r in requests if r.get("kind") == "avoid"]
    floors = _floors(requests)
    eligible = [
        s
        for s, e in pool["stocks"].items()
        if s not in exclude
        and (e.get("screen") or {}).get("passed")
        and not any(matches_request(s, e, r, universe) for r in avoid)
        and _clears_floors(e, floors)
    ]
    by_score = sorted(eligible, key=lambda s: -pool["stocks"][s]["screen"]["score"])

    focus_request = next((r for r in requests if r.get("kind") in FOCUS_KINDS), None)
    focus = []
    if focus_request:
        room = wanted.get("focus", 4)
        focus = [s for s in by_score if matches_request(s, pool["stocks"][s], focus_request, universe)][:room]

    others = wanted["top"] - len(focus) if focus else wanted["top"]
    top = [s for s in by_score if s not in focus][: max(0, others)]
    rest = [s for s in by_score if s not in focus and s not in top]
    rest.sort(key=lambda s: (pool["stocks"][s].get("shortlisted") or {}).get("last") or "")
    rotation = rest[: wanted["rotation"]]

    picks = [(s, "focus") for s in focus] + [(s, "top") for s in top] + [(s, "rotation") for s in rotation]
    if limit:
        picks = picks[:limit]
    for s, _ in picks:
        record = pool["stocks"][s].setdefault("shortlisted", {"times": 0, "first": today.isoformat()})
        record["times"] += 1
        record["last"] = today.isoformat()
    return picks


FLOOR_FIELDS = {"dividend_yield": "dividend_yield_pct", "market_cap_usd_m": "market_cap_usd_m", "pe": "pe"}


def _floors(requests: list) -> list:
    """'dividend_yield >= 4' -> [(screen field, 4.0)]; anything unparseable is ignored."""
    out = []
    for r in requests:
        if r.get("kind") != "metric_floor":
            continue
        match = re.match(r"\s*([a-z_]+)\s*>?=\s*([0-9.]+)\s*$", str(r.get("value", "")).lower())
        if match and match.group(1) in FLOOR_FIELDS:
            out.append((FLOOR_FIELDS[match.group(1)], float(match.group(2))))
    return out


def _clears_floors(entry: dict, floors: list) -> bool:
    screen = entry.get("screen") or {}
    return all((screen.get(field) or 0) >= floor for field, floor in floors)


def add_requested(pool, request: dict, today) -> list:
    """Puts tickers the client named into the pool (screened with everything else)."""
    added = []
    for symbol in {v.strip().upper() for v in str(request.get("value", "")).split(",") if v.strip()}:
        if symbol not in pool["stocks"]:
            pool["stocks"][symbol] = {"added": today.isoformat(), "via": "you asked for it"}
            added.append(symbol)
    return [f"added {', '.join(sorted(added))}: you asked for them"] if added else []


def expand_for(pool, universe, request: dict, today, needed: int = 4) -> list:
    """When too few pool stocks answer a focus request, adds peers of the ones
    that do (and of that sector's seed group) and screens just those. Notes what happened."""
    stocks = pool["stocks"]
    matching = [s for s, e in stocks.items() if matches_request(s, e, request, universe)]
    if len([s for s in matching if (stocks[s].get("screen") or {}).get("passed")]) >= needed:
        return []
    asked = _entry_for(request.get("value"), universe)
    seeds = [s for group, symbols in universe["seeds"].items()
             if any(w in group.lower() for w in (*asked["core"], *asked["signals"])) for s in symbols]  # fmt: skip
    added, notes = [], []
    for source in dict.fromkeys(matching[:6] + seeds[:6]):
        if not US_TICKER.fullmatch(source):
            continue
        peers, err = finnhub.get_peers(source, "industry")
        if err:
            continue
        for peer in peers or []:
            if peer not in stocks and US_TICKER.fullmatch(peer):
                stocks[peer] = {
                    "added": today.isoformat(),
                    "via": f"asked for {request.get('value')}: similar to {source}",
                }
                added.append(peer)
    if added:
        screen_subset(pool, universe, today, added)
        notes.append(f"added {len(added)} stocks to answer the request for {request.get('value')}")
    return notes


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
