"""The monthly review: how the calls evolved, and how the portfolio works as a whole.

    python -m position_watch monthly [--no-email]

Code computes every fact -- the month's call history per symbol, and a
snapshot of the whole portfolio (concentration, sectors, currencies, asset
mix, value-weighted volatility and beta, income, change since last month) --
from the workspace files; it makes no market-data calls. One Claude call
(batched, see llm.py) then reviews the portfolio as a whole against the
client's preferences. Output: reports/monthly/<YYYY-MM>-review.md and, if mail
is configured, a short email. Snapshots are kept in state/monthly_snapshots.csv
so each month can be compared with the last.
"""

import csv
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from position_watch import compliance, instruments, llm, mail, people, settings, suggestion_log
from position_watch.analysis import pnl
from position_watch.dashboard import view
from position_watch.documents import render as documents

POSITIVE, NEGATIVE = {"buy", "add", "top_up"}, {"trim", "sell"}

SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {"type": "string", "description": "Two short paragraphs on how the portfolio works as a whole."},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "call_patterns": {"type": "array", "items": {"type": "string"}},
        "to_consider": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["overview", "strengths", "risks", "call_patterns", "to_consider"],
    "additionalProperties": False,
}

SYSTEM = """You are the analyst for Position Watch, a private portfolio research assistant. Once a month you \
review the portfolio as a whole, from facts computed by code: its concentration and diversification, sector, \
currency and asset mix, value-weighted volatility and beta, income, change since last month, and how the daily \
calls evolved over the month. You never place or suggest executing a trade; the client decides. Not financial advice.

- Judge the whole, not single stocks: does the mix fit the client's stated horizon, goal, risk tolerance and \
preferences? Where is the portfolio concentrated or exposed (one sector, one currency, a few positions, \
high-volatility names)? How do the core ETFs balance the individual stocks? Does the income match a preference \
for dividends?
- Every number you mention must come from the facts given, quoted as given. Never estimate or recall figures. \
Say when coverage is partial (e.g. volatility known for only part of the portfolio).
- call_patterns: what the month's calls show -- sustained calls, calls that changed and why, recurring themes. \
Describe how the suggestions evolved; never claim a past call was right or wrong.
- to_consider: things worth looking into, phrased as questions or areas to review, not as orders.
- Keep each list item to one or two sentences."""


def history(entries: list, end: date, days: int = 30) -> list:
    start = (end - timedelta(days=days)).isoformat()
    rows = sorted((e for e in entries if start < e["date"] <= end.isoformat()), key=lambda e: e["date"])
    by_symbol = defaultdict(list)
    for e in rows:
        by_symbol[(e["scope"], e["symbol"])].append(e)
    out = []
    for (scope, symbol), items in sorted(by_symbol.items()):
        actions = [(e["date"], (e["action"] or "").lower()) for e in items]
        changes = [(d, prev, act) for (_, prev), (d, act) in zip(actions, actions[1:], strict=False) if act != prev]
        out.append({
            "scope": scope, "symbol": symbol, "days": len(items),
            "positive": sum(a in POSITIVE for _, a in actions), "hold": sum(a == "hold" for _, a in actions),
            "negative": sum(a in NEGATIVE for _, a in actions),
            "changes": [{"date": d, "from": compliance.label(p), "to": compliance.label(a)} for d, p, a in changes],
            "latest": {"date": items[-1]["date"], "action": compliance.label(items[-1]["action"]),
                       "one_line": items[-1]["one_line"]},
        })  # fmt: skip
    return out


def snapshot(holdings: list, review: dict) -> dict:
    pnl_data = pnl.compute(holdings, review, (review or {}).get("fx"))
    positions = [p for p in pnl_data["positions"] if (p["value_usd"] or 0) > 0]
    total = sum(p["value_usd"] for p in positions) or 1
    evidence = {**(review or {}).get("holdings", {}), **(review or {}).get("etfs", {})}

    rows, mix, currency = [], defaultdict(float), defaultdict(float)
    for p in sorted(positions, key=lambda p: -p["value_usd"]):
        weight = p["value_usd"] / total * 100
        e = evidence.get(p["symbol"], {})
        kind = instruments.kind(p["symbol"])
        mix[kind] += weight
        currency[p["currency"]] += weight
        rows.append({"symbol": p["symbol"], "name": p["name"], "kind": kind, "currency": p["currency"],
                     "value_usd": round(p["value_usd"]), "weight_pct": round(weight, 1),
                     "pnl_pct": round(p["pnl_pct"], 1) if p["pnl_pct"] is not None else None,
                     "volatility_3m_pct": e.get("volatility_3m_pct"), "beta": e.get("beta", e.get("beta_3y"))})  # fmt: skip

    def weighted(field):
        known = [(r["weight_pct"], r[field]) for r in rows if r[field] is not None]
        cover = sum(w for w, _ in known)
        return {"value": round(sum(w * v for w, v in known) / cover, 2) if cover else None,
                "coverage_pct": round(cover, 1)}  # fmt: skip

    weights = [r["weight_pct"] / 100 for r in rows]
    t = pnl_data["totals"]
    return {
        "totals": {k: round(v, 2) if isinstance(v, float) else v for k, v in t.items()},
        "positions": rows,
        "concentration": {
            "largest": {"symbol": rows[0]["symbol"], "weight_pct": rows[0]["weight_pct"]} if rows else None,
            "top3_weight_pct": round(sum(r["weight_pct"] for r in rows[:3]), 1),
            "effective_positions": round(1 / sum(w * w for w in weights), 1) if weights else None,
        },
        "sectors": [
            {"sector": s["key"], "share_pct": round(s["share"], 1)}
            for s in view.sectors(pnl_data, review, holdings)["legend"]
        ],  # fmt: skip
        "currency_pct": {k: round(v, 1) for k, v in sorted(currency.items(), key=lambda kv: -kv[1])},
        "asset_mix_pct": {k: round(v, 1) for k, v in mix.items()},
        "risk": {"volatility_3m_pct": weighted("volatility_3m_pct"), "beta": weighted("beta")},
        "income": {
            "dividends_received_usd": t["dividends_usd"],
            "dividends_on_cost_pct": round(t["dividends_usd"] / t["invested_usd"] * 100, 2)
            if t["invested_usd"]
            else None,
        },  # fmt: skip
        "fx_used": pnl_data["fx_used"],
    }


def _snapshots_path():
    return settings.state_dir() / "monthly_snapshots.csv"


def previous_snapshot(month: str) -> dict | None:
    path = _snapshots_path()
    if not path.exists():
        return None
    with open(path, newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["month"] < month]
    return rows[-1] if rows else None


def record_snapshot(month: str, snap: dict):
    path = _snapshots_path()
    new = not path.exists()
    t = snap["totals"]
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if new:
            writer.writerow(["month", "value_usd", "invested_usd", "pnl_usd", "dividends_usd", "top3_weight_pct"])
        writer.writerow([month, round(t["value_usd"]), round(t["invested_usd"]), round(t["pnl_usd"]),
                         round(t["dividends_usd"]), snap["concentration"]["top3_weight_pct"]])  # fmt: skip


def facts(today: date | None = None) -> dict:
    """Everything the monthly review is based on, computed from the workspace: no API calls."""
    today = today or datetime.now(timezone.utc).date()
    month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")  # the month just ended
    holdings, review, _ = view.load_inputs()
    snap = snapshot(holdings, review or {})
    prev = previous_snapshot(month)
    if prev:
        snap["since_last_month"] = {
            "previous_month": prev["month"],
            "value_change_usd": round(snap["totals"]["value_usd"] - float(prev["value_usd"])),
            "dividends_change_usd": round(snap["totals"]["dividends_usd"] - float(prev["dividends_usd"])),
        }
    return {"month": month, "portfolio": snap, "calls_over_the_month": history(suggestion_log.load_entries(), today)}


def run(send_email: bool = True, client=None, today: date | None = None, mode=None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    f = facts(today)
    month, snap, looked_back = f["month"], f["portfolio"], f["calls_over_the_month"]
    prefs = {k: v for k, v in (people.client().get("preferences") or {}).items() if k != "history"}
    compact = {"separators": (",", ":"), "default": str}
    user = (
        f"Month reviewed: {month}\n\n<client_preferences>\n{json.dumps(prefs, **compact)}\n</client_preferences>\n\n"
        f"<portfolio>\n{json.dumps(snap, **compact)}\n</portfolio>\n\n"
        f"<calls_over_the_month>\n{json.dumps(looked_back, **compact)}\n</calls_over_the_month>\n\n"
        "Review the portfolio as a whole."
    )
    message, batched = llm.ask(llm.base_request(SYSTEM, user, SCHEMA, 32000), client=client, mode=mode)
    answer = llm.json_answer(message)
    usage = {**llm.usage(message, batched), "task": "monthly"}

    llm.log_usage(today.isoformat(), usage)
    record_snapshot(month, snap)
    path = settings.reports_dir() / "monthly" / f"{month}-review.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    ctx = {"month": month, "snap": snap, "history": looked_back, "answer": answer, "model": usage["model"]}
    path.write_text(documents.render_template("monthly.md", **ctx))
    if send_email:
        link = settings.reports_url()
        body = documents.render_template("monthly_email.txt", **ctx,
                                         report_url=f"{link.replace('/tree/', '/blob/', 1)}/monthly/{month}-review.md"
                                         if link else None)  # fmt: skip
        name = datetime.strptime(month, "%Y-%m").strftime("%B %Y")
        mail.send(people.recipients(), f"{documents.TITLE} — Monthly — {name}", body)
    return {"month": month, "report": str(path), "estimated_usd": usage["estimated_usd"], "symbols": len(looked_back)}
