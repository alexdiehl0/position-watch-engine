"""The dashboard's view model: everything the page shows, computed from the
workspace files. Numbers stay numbers here; the templates format them, and
Jinja escapes every value, so text from outside sources (news headlines,
company descriptions) can never become markup.

Chart colours follow the dataviz palette in static/style.css (--s1..--s8,
--other): 8 fixed categorical slots in stable holdings.csv order, never by
rank, and anything past the eighth folds into "Other".
"""

import csv
import json
import math
from datetime import datetime, timezone
from urllib.parse import quote

from position_watch import client_requests, compliance, people, settings, suggestion_log
from position_watch.analysis import markets, pnl
from position_watch.analysis.stock import volatility_level

ACTION_STATUS = {"buy": "good", "add": "good", "top_up": "good", "hold": "warn", "trim": "crit", "sell": "crit"}
ACTION_RANK = {"buy": 0, "add": 1, "top_up": 1, "hold": 2, "trim": 3, "sell": 4}
FEEDBACK_SUBJECT = "Portfolio feedback"
ETF_SECTOR = "ETFs (diversified funds)"

NAV = [
    ("overview", "Overview"),
    ("allocation", "Allocation"),
    ("suggestions", "Suggestions"),
    ("watchlist", "Watchlist"),
    ("etfs", "ETFs"),
    ("positions", "P&L"),
    ("sectors", "Sectors"),
    ("pool", "Stock pool"),
    ("news", "News"),
    ("excluded", "Not evaluated"),
    ("profile", "Profile"),
    ("feedback", "Feedback"),
]


# ---- Inputs --------------------------------------------------------------


def _load_json(path):
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_inputs():
    with open(settings.holdings_csv(), newline="") as f:
        holdings = [r for r in csv.DictReader(f) if r.get("status") == "open"]
    return holdings, _load_json(settings.latest_review_path()), _load_json(settings.suggestions_path())


# ---- Shared pieces -------------------------------------------------------


def action(code):
    """Display data for an action pill: label, status class and sort rank."""
    code = (code or "").lower()
    return {
        "label": compliance.label(code),
        "status": ACTION_STATUS.get(code, "warn") if code else "none",
        "rank": ACTION_RANK.get(code, 9),
    }


def company(symbol, data):
    return {
        "symbol": symbol,
        "name": data.get("company_name"),
        "sector": " · ".join(dict.fromkeys(b for b in (data.get("sector"), data.get("industry")) if b)),
        "description": data.get("description"),
    }


def risk(vol_pct, beta):
    return {"vol": vol_pct, "level": volatility_level(vol_pct), "beta": beta}


# ---- Allocation donut ----------------------------------------------------


def _polar(cx, cy, r, angle_deg):
    rad = math.radians(angle_deg - 90)
    return cx + r * math.cos(rad), cy + r * math.sin(rad)


def _arc(cx, cy, r_outer, r_inner, start, end):
    large = 1 if (end - start) > 180 else 0
    x1, y1 = _polar(cx, cy, r_outer, end)
    x2, y2 = _polar(cx, cy, r_outer, start)
    x3, y3 = _polar(cx, cy, r_inner, start)
    x4, y4 = _polar(cx, cy, r_inner, end)
    return (
        f"M{x1:.2f},{y1:.2f} A{r_outer},{r_outer} 0 {large} 0 {x2:.2f},{y2:.2f} "
        f"L{x3:.2f},{y3:.2f} A{r_inner},{r_inner} 0 {large} 1 {x4:.2f},{y4:.2f} Z"
    )


def allocation(holdings, names, max_slices=8):
    """Ring segments in stable holdings.csv order (so touching segments are
    validated adjacent palette slots and a weight change never repaints a
    position); only the smallest weights past the 8-hue ceiling fold into
    Other. The legend is the table view, largest first."""
    rows = [(r["symbol"], _float(r.get("allocation_pct"))) for r in holdings]
    n_fold = max(0, len(rows) - max_slices)
    folded = sorted(rows, key=lambda x: x[1])[:n_fold] if n_fold else []
    folded_syms = {s for s, _ in folded}
    kept = [(s, p) for s, p in rows if s not in folded_syms]
    slices = [{"key": s, "pct": p, "color": f"var(--s{i + 1})"} for i, (s, p) in enumerate(kept)]
    if folded:
        slices.append({"key": "Other", "pct": sum(p for _, p in folded), "color": "var(--other)"})
        folded.sort(key=lambda x: x[1], reverse=True)

    total, angle = sum(s["pct"] for s in slices) or 1, 0.0
    for s in slices:
        sweep = s["pct"] / total * 360
        s["d"] = _arc(110, 110, 104, 64, angle, angle + sweep)
        angle += sweep

    top = max((s["pct"] for s in slices), default=1) or 1
    legend = [
        {
            "key": s["key"],
            "color": s["color"],
            "pct": s["pct"],
            "bar": s["pct"] / top * 100,
            "name": " · ".join(f"{sym} {p:.1f}%" for sym, p in folded)
            if s["key"] == "Other"
            else names.get(s["key"]) or "",
        }
        for s in sorted(slices, key=lambda s: (s["key"] == "Other", -s["pct"]))
    ]
    return {
        "slices": slices,
        "legend": legend,
        "count": len(holdings),
        "aria": "Portfolio allocation: " + ", ".join(f"{s['key']} {s['pct']:.1f}%" for s in slices),
        "other_note": f"; the {len(folded)} smallest are grouped as Other" if len(folded) > 1 else "",
    }


# ---- Positions & sectors -------------------------------------------------


def positions(pnl_data):
    rows = []
    for p in sorted(pnl_data["positions"], key=lambda p: -(p["value_usd"] or 0)):
        rows.append({**p, "live": p["price_source"].startswith("live"), "flag": "; ".join(p["flags"])})
    notes = [
        f"USD totals convert {cur} positions at {fx['rate']:.4f} ({fx.get('source')}"
        f"{', ' + fx['date'] if fx.get('date') else ''}); USD cost is the broker’s, so currency moves are "
        f"included in the return. Sorting by value or P&L compares USD amounts."
        for cur, fx in pnl_data["fx_used"].items()
    ]
    notes.append(
        "Share counts come from the broker’s transaction history. "
        "“Snapshot” prices are the broker’s last recorded price, used where no live price was fetched."
    )
    return {"rows": rows, "totals": pnl_data["totals"], "notes": notes}


def sectors(pnl_data, review, holdings):
    """One 100% bar: a colour per sector, split into its positions. Sector
    colours follow the sector (first appearance in holdings.csv), never its
    size, and the bar is drawn in that order."""
    sector_of = {sym: d.get("sector") for sym, d in (review or {}).get("holdings", {}).items()}
    sector_of.update({sym: ETF_SECTOR for sym in (review or {}).get("etfs", {})})
    for e in (review or {}).get("excluded", []):
        reason = (e.get("reason") or "").lower()
        sector_of[e["symbol"]] = (
            ETF_SECTOR if reason.startswith("etf") else "Gold & commodities" if "commodity" in reason else None
        )

    def label_of(sym):
        return sector_of.get(sym) or "Not classified"

    live = [p for p in pnl_data["positions"] if (p["value_usd"] or 0) > 0]
    total = sum(p["value_usd"] for p in live) or 1
    groups = {label: [] for label in dict.fromkeys(label_of(r["symbol"]) for r in holdings)}
    rank = {r["symbol"]: i for i, r in enumerate(holdings)}
    for p in sorted(live, key=lambda p: rank.get(p["symbol"], 99)):
        groups.setdefault(label_of(p["symbol"]), []).append(p)
    groups = {k: v for k, v in groups.items() if v}
    if len(groups) > 8:
        smallest = sorted(groups, key=lambda k: sum(p["value_usd"] for p in groups[k]))[: len(groups) - 7]
        groups = {k: v for k, v in groups.items() if k not in smallest} | {
            "Other sectors": [p for k in smallest for p in groups[k]]
        }

    segments = []
    for i, (label, ps) in enumerate(groups.items()):
        value = sum(p["value_usd"] for p in ps)
        segments.append(
            {
                "key": label,
                "value": value,
                "share": value / total * 100,
                "color": "var(--other)" if label == "Other sectors" else f"var(--s{i + 1})",
                "positions": [
                    {
                        "symbol": p["symbol"],
                        "name": p["name"],
                        "value": p["value_usd"],
                        "share": p["value_usd"] / total * 100,
                    }
                    for p in ps
                ],
            }
        )
    return {
        "segments": segments,
        "legend": sorted(segments, key=lambda s: -s["value"]),
        "aria": "Portfolio value by sector: " + ", ".join(f"{s['key']} {s['share']:.1f}%" for s in segments),
    }


# ---- Suggestions, ETFs, watchlist, pool ----------------------------------


def suggestion_counts(suggestions):
    counts = {}
    for v in (suggestions or {}).get("holdings", {}).values():
        lab = compliance.label(v.get("action"))
        counts[lab] = counts.get(lab, 0) + 1
    parts = [f"{counts[k]} {k}" for k in ("Buy", "Add", "Hold", "Trim", "Sell") if counts.get(k)]
    return "Today: " + " · ".join(parts) + ". " if parts else ""


def holdings_rows(review, suggestions, holdings):
    weight = {r["symbol"]: _float(r.get("allocation_pct")) for r in holdings}
    sugg = (suggestions or {}).get("holdings", {})
    return [
        {
            "company": company(sym, d),
            "action": action(sugg.get(sym, {}).get("action")),
            "one_line": sugg.get(sym, {}).get("one_line") or "No synthesized suggestion for this run.",
            "weight": weight.get(sym, 0),
            "risk": risk(d.get("volatility_3m_pct"), d.get("beta")),
            "gaps": len(d.get("data_gaps") or []),
        }
        for sym, d in (review or {}).get("holdings", {}).items()
    ]


def etf_rows(review, suggestions):
    sugg = (suggestions or {}).get("etfs", {})
    rows = []
    for sym, e in (review or {}).get("etfs", {}).items():
        below = e.get("below_52w_high_pct")
        rows.append(
            {
                **e,
                "symbol": sym,
                "action": action(sugg.get(sym, {}).get("action")),
                "one_line": sugg.get(sym, {}).get("one_line")
                or "First Top up / Hold call arrives with the next daily run.",
                "snapshot": e.get("reference_price_source") != "yfinance",
                "risk": risk(e.get("volatility_3m_pct"), e.get("beta_3y")),
                "vs_high": -below if below is not None else None,
            }
        )
    return rows


def watchlist(review, suggestions):
    sugg = (suggestions or {}).get("candidates", {})
    order = {"good": 0, "warn": 1, "crit": 2}
    cards = []
    for sym, d in (review or {}).get("candidates", {}).items():
        a = action(sugg.get(sym, {}).get("action"))
        cards.append(
            {
                "company": company(sym, d),
                "action": a,
                "pool": d.get("pool") or {},
                "risk_note": d.get("volatility_note"),
                "one_line": sugg.get(sym, {}).get("one_line") or "No synthesized suggestion for this run.",
                "order": order.get(a["status"], 1),
            }
        )
    cards.sort(key=lambda c: c["order"])

    pool = (review or {}).get("pool")
    if pool:
        top = sum(1 for r in pool["stocks"] if r.get("picked_today") == "top")
        rot = sum(1 for r in pool["stocks"] if r.get("picked_today") == "rotation")
        sub = (
            f"Picked today from the {pool['size']}-stock pool: the {top} best fits on today’s screen "
            f"(cheap against their own history, a solid dividend) plus {rot} rotation picks it hasn’t looked at lately, "
            f"each then assessed on the same evidence as your holdings."
        )
    else:
        sub = "Peers of what you already own, assessed on the same evidence as your holdings."
    return {"cards": cards, "sub": sub}


def pool_table(review):
    pool = (review or {}).get("pool")
    if not pool:
        return None
    last_call = {e["symbol"]: e for e in suggestion_log.load_entries() if e.get("scope") == "candidate"}
    rows = []
    for r in pool["stocks"]:
        pe, med = r.get("pe"), r.get("five_yr_median_pe")
        if r.get("held"):
            status, status_rank = ("chip", "You own it"), 1
        elif r.get("picked_today"):
            status, status_rank = ("chip-new", "On today’s watchlist"), 0
        elif r.get("passed"):
            status, status_rank = ("muted", "Screened"), 2
        else:
            status, status_rank = ("muted", f"Left out: {r.get('filtered_out')}"), 3
        lc = last_call.get(r["symbol"])
        rows.append(
            {
                **r,
                "vs_median": (pe - med) / med * 100 if pe and med and pe > 0 else None,
                "cap_b": r["market_cap_usd_m"] / 1000 if r.get("market_cap_usd_m") else None,
                "label": " · ".join(b for b in (r.get("name"), r.get("industry")) if b),
                "risk": risk(r.get("volatility_3m_pct"), r.get("beta")),
                "status": status,
                "status_rank": status_rank,
                "last_call": {"action": action(lc.get("action")), "date": lc.get("date")} if lc else None,
            }
        )
    return {**{k: pool.get(k) for k in ("size", "passed", "refreshed")}, "rows": rows, "notes": pool.get("notes") or []}


def news(review, limit=10):
    seen, items = set(), []
    for section in ("holdings", "candidates"):
        for sym, d in (review or {}).get(section, {}).items():
            for n in d.get("recent_news") or []:
                if n.get("url") and n["url"] not in seen:
                    seen.add(n["url"])
                    items.append({"symbol": sym, **n})
    return items[:limit]


def requests_panel():
    """What the client has asked for, and what can be asked (client_requests.py)."""
    active = [
        {"line": client_requests.describe(r), "kind": r.get("kind"), "text": r.get("text") or ""}
        for r in people.active_requests()
    ]
    return {"active": active, "kinds": [{"name": k.name, "effect": k.effect, "example": k.examples[0]}
                                        for k in client_requests.KINDS if k.name != "other"]}  # fmt: skip


def ignored_messages():
    """Addresses whose messages couldn't be used, newest first (state/ignored_messages.json)."""
    known = _load_json(settings.state_dir() / "ignored_messages.json") or {}
    rows = [{"address": a, **v} for a, v in known.items()]
    return sorted(rows, key=lambda r: r.get("last_seen") or "", reverse=True)


def excluded(review):
    groups = {}
    for e in (review or {}).get("excluded", []):
        groups.setdefault(e["reason"], []).append(e["symbol"])
    return [{"symbols": " / ".join(syms), "reason": reason} for reason, syms in groups.items()]


def profile(person):
    prefs = (person or {}).get("preferences") or {}

    def listed(key):
        return ", ".join(prefs.get(key) or []) or None

    rows = [
        ("Time horizon", prefs.get("investment_horizon"), "not stated"),
        ("Goal", prefs.get("goal"), "not stated"),
        ("Risk tolerance", prefs.get("risk_tolerance"), "not stated yet"),
        ("Favours", listed("favours"), "none yet"),
        ("Preferred sectors", listed("preferred_sectors"), "none yet"),
        ("Avoid", listed("avoid"), "none yet"),
        ("Other notes", listed("other_notes"), "none yet"),
    ]
    history = prefs.get("history") or []
    return {"rows": rows, "last": history[-1] if history else None}


# ---- The whole page ------------------------------------------------------


def build(fragment=False) -> dict:
    holdings, review, suggestions = load_inputs()
    names = {r["symbol"]: r.get("name") for r in holdings}
    for d in (review or {}).get("holdings", {}).values():
        if d.get("company_name"):
            names[d["symbol"]] = d["company_name"]
    pnl_data = pnl.compute(holdings, review, (review or {}).get("fx"))
    first = next(iter((review or {}).get("holdings", {}).values()), {})
    inbox = people.feedback_inbox()
    return {
        "fragment": fragment,
        "report_date": (suggestions or {}).get("date") or (first.get("data_as_of") or "")[:10],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "reports_url": settings.reports_url(),
        "note": compliance.NOTE,
        "nav": NAV,
        "has_review": bool(review),
        "totals": pnl_data["totals"],
        "allocation": allocation(holdings, names),
        "positions": positions(pnl_data),
        "sectors": sectors(pnl_data, review, holdings),
        "suggestion_counts": suggestion_counts(suggestions),
        "holdings": holdings_rows(review, suggestions, holdings),
        "etfs": etf_rows(review, suggestions),
        "watchlist": watchlist(review, suggestions),
        "pool": pool_table(review),
        "news": news(review),
        "markets": markets.display(review, (suggestions or {}).get("market_briefing")),
        "excluded": excluded(review),
        "profile": profile(people.client()),
        "requests": requests_panel(),
        "feedback": {
            "to": inbox,
            "subject": FEEDBACK_SUBJECT,
            "mailto": f"mailto:{inbox}?subject={quote(FEEDBACK_SUBJECT)}",
            "ignored": ignored_messages(),
        },
    }
