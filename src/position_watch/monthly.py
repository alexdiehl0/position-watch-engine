"""The monthly review: how the calls evolved, and how the portfolio works as a whole.

    python -m position_watch monthly [--no-email]

Code computes every fact -- the month's call history per symbol, and a
snapshot of the whole portfolio (concentration, sectors, currencies, asset
mix, value-weighted volatility and beta, income, change since last month) --
from the workspace files; it makes no market-data calls. One Claude call
(batched, see llm.py) then reviews the portfolio as a whole against the
client's preferences. Output: reports/monthly/<YYYY-MM>-review.md and, if mail
is configured, an email (HTML with a plain-text alternative) that leads with the
numbers and the month's best-performing ideas. Snapshots are kept in
state/monthly_snapshots.csv so each month can be compared with the last, and the
whole review in state/monthly_review.json so the email can be rebuilt.

Sent once: before paying for the review, the run asks the Sent folder whether this
month's email already went out (see mail.send_once) and stops if it did. On
1 Oct 2026 a run that emailed but lost its commit was followed by a backup slot
that emailed the client the same review again.
"""

import csv
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from position_watch import compliance, instruments, llm, mail, people, settings, suggestion_log
from position_watch.analysis import pnl, scorecard
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
- against_the_index compares the client's own cash flows with the same money put into the S&P 500 on the same \
days, and scores each call against the index over exactly its own days. Say plainly how the portfolio stands \
against simply buying the index, and what the call scores do and don't yet show (how many calls, how long they \
have run). A few weeks of calls prove nothing: say so rather than reading a trend into them.
- to_consider: things worth looking into, phrased as questions or areas to review, not as orders.
- best_ideas_of_the_month (inside against_the_index) lists watchlist ideas that rose most since first suggested, \
with today's call for each. A past rise is not a reason to buy: if you mention one, weigh its latest call and the \
client's preferences, and never imply the rise will continue.
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
    """Everything the monthly review is based on: the workspace's own files, plus
    one Yahoo request for the index and the called stocks (the scorecard)."""
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
    first_day = (today.replace(day=1) - timedelta(days=1)).replace(day=1).isoformat()
    last_day = (today.replace(day=1) - timedelta(days=1)).isoformat()
    held = {r["symbol"] for r in holdings}
    card = scorecard.compute(totals=snap["totals"], today=today, month=(first_day, last_day), held=held)
    ideas = ((card or {}).get("best_ideas") or {}).get("ideas") or []
    names = _names(review) if ideas else {}
    for idea in ideas:
        idea["name"] = idea.get("name") or names.get(idea["symbol"], "")
    return {
        "month": month,
        "portfolio": snap,
        "calls_over_the_month": history(suggestion_log.load_entries(), today),
        "scorecard": card,
    }


def _names(review) -> dict:
    """Company names for the watchlist: today's evidence first, then the stock pool."""
    names = {}
    pool = settings.state_dir() / "stock_pool.json"
    if pool.exists():
        for symbol, d in (json.loads(pool.read_text()).get("stocks") or {}).items():
            names[symbol] = d.get("name") or ""
    for section in ("candidates", "holdings"):
        for symbol, d in ((review or {}).get(section) or {}).items():
            names[symbol] = d.get("company_name") or names.get(symbol, "")
    return names


def subject(month: str) -> str:
    return f"{documents.TITLE} — Monthly — {datetime.strptime(month, '%Y-%m').strftime('%B %Y')}"


def _report_url(month: str):
    link = settings.reports_url()
    return f"{link.replace('/tree/', '/blob/', 1)}/monthly/{month}-review.md" if link else None


def run(send_email: bool = True, client=None, today: date | None = None, mode=None, force: bool = False) -> dict:
    today = today or datetime.now(timezone.utc).date()
    month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    since = today.replace(day=1) - timedelta(days=1)
    if send_email and not force and mail.already_sent(f"monthly-{month}", subject(month), since):
        print(f"The {month} monthly review was already emailed; nothing to do (use --force to redo it).", flush=True)
        return {"month": month, "skipped": "already sent"}

    f = facts(today)
    snap, looked_back, card = f["portfolio"], f["calls_over_the_month"], f["scorecard"]
    prefs = {k: v for k, v in (people.client().get("preferences") or {}).items() if k != "history"}
    compact = {"separators": (",", ":"), "default": str}
    user = (
        f"Month reviewed: {month}\n\n<client_preferences>\n{json.dumps(prefs, **compact)}\n</client_preferences>\n\n"
        f"<portfolio>\n{json.dumps(snap, **compact)}\n</portfolio>\n\n"
        f"<calls_over_the_month>\n{json.dumps(looked_back, **compact)}\n</calls_over_the_month>\n\n"
        f"<against_the_index>\n{json.dumps(card, **compact)}\n</against_the_index>\n\n"
        "Review the portfolio as a whole."
    )
    message, batched = llm.ask(llm.base_request(SYSTEM, user, SCHEMA, 32000), client=client, mode=mode)
    answer = llm.json_answer(message)
    usage = {**llm.usage(message, batched), "task": "monthly"}

    llm.log_usage(today.isoformat(), usage)
    record_snapshot(month, snap)
    ctx = {"month": month, "snap": snap, "history": looked_back, "answer": answer, "model": usage["model"],
           "card": card}  # fmt: skip
    (settings.state_dir() / "monthly_review.json").write_text(json.dumps(ctx, indent=2, default=str) + "\n")
    path = settings.reports_dir() / "monthly" / f"{month}-review.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(documents.render_template("monthly.md", **ctx))
    sent = False
    if send_email:
        msg = documents.monthly_email(ctx, report_url=_report_url(month))
        sent = mail.send_once(f"monthly-{month}", people.recipients(), subject(month), msg["text"], html=msg["html"],
                              since=since)  # fmt: skip
    return {"month": month, "report": str(path), "estimated_usd": usage["estimated_usd"], "symbols": len(looked_back),
            "emailed": sent}  # fmt: skip


def preview(out_dir) -> dict:
    """Rebuilds the last monthly email from state/monthly_review.json, without sending it."""
    ctx = json.loads((settings.state_dir() / "monthly_review.json").read_text())
    msg = documents.monthly_email(ctx, report_url=_report_url(ctx["month"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "monthly-email.html").write_text(msg["html"])
    (out_dir / "monthly-email.txt").write_text(msg["text"])
    return {"subject": subject(ctx["month"]), "html": str(out_dir / "monthly-email.html")}
