"""Renders the daily report (Markdown), the daily email (HTML plus a plain-text
alternative) and the monthly documents from the evidence and the model's calls.

Markdown and text templates are not escaped; the HTML email is, so text from
outside sources can't become markup. Company names come straight from the
evidence; only the calls and reasoning come from the model.
"""

from datetime import date

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from position_watch import client_requests, compliance
from position_watch.analysis import markets
from position_watch.analysis.stock import volatility_level

TITLE = "AI STOCK PORTFOLIO REVIEW"

# Email colours per action: (text, background). The action word is always shown
# too, so nothing depends on colour alone.
BADGES = {
    "good": ("#166534", "#dcfce7"),  # Buy, Add, Top up
    "warn": ("#92400e", "#fef3c7"),  # Hold
    "crit": ("#991b1b", "#fee2e2"),  # Trim, Sell
    "none": ("#374151", "#e5e7eb"),
}
_STATUS = {"buy": "good", "add": "good", "top_up": "good", "hold": "warn", "trim": "crit", "sell": "crit"}
_ORDER = {"good": 0, "crit": 1, "warn": 2, "none": 3}  # what needs attention first


def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("position_watch.documents", "templates"),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.globals.update(label=compliance.label, note=compliance.NOTE, title=TITLE)
    env.filters.update(usd=_usd, pct=_pct)
    return env


def _usd(value, signed=False):
    if value is None:
        return "–"
    sign = ("+" if signed and value > 0 else "") if value >= 0 else "−"
    return f"{sign}${abs(value):,.0f}"


def _pct(value):
    if value is None:
        return "–"
    return f"{'+' if value > 0 else '−' if value < 0 else ''}{abs(value):.1f}%"


def subject(day: str, suffix: str = "") -> str:
    """e.g. 'AI STOCK PORTFOLIO REVIEW — 14 Sep 2026'."""
    when = date.fromisoformat(day).strftime("%-d %b %Y")
    return f"{TITLE} — {when}{f' — {suffix}' if suffix else ''}"


def _stats(d: dict, position: dict | None) -> dict | None:
    """Performance since bought (price vs average cost, in the position's own
    currency) and 3-month volatility, each only when known."""
    perf, vol = (position or {}).get("pnl_pct"), d.get("volatility_3m_pct")
    if perf is None and vol is None:
        return None
    currency = (position or {}).get("currency")
    currency = currency if currency and currency != "USD" else None
    parts = []
    if perf is not None:
        parts.append(f"{_pct(perf)} since you bought" + (f" (in {currency})" if currency else ""))
    if vol is not None:
        parts.append(f"volatility {vol:.0f}% ({volatility_level(vol)})")
    return {
        "perf_pct": perf, "perf_fg": BADGES["good" if (perf or 0) >= 0 else "crit"][0], "perf_currency": currency,
        "vol_pct": vol, "vol_level": volatility_level(vol), "line": " · ".join(parts),
    }  # fmt: skip


def email_rows(calls: list, evidence: dict, names: dict | None = None, positions: dict | None = None) -> list:
    """One compact row per symbol, most actionable first. Names fall back to the holdings file.
    With `positions` (the P&L per symbol), rows also carry performance and volatility."""
    rows = []
    for c in calls:
        status = _STATUS.get((c.get("action") or "").lower(), "none")
        fg, bg = BADGES[status]
        d = evidence.get(c["symbol"], {})
        rows.append({
            "symbol": c["symbol"], "name": d.get("company_name") or d.get("name") or (names or {}).get(c["symbol"]) or "",
            "label": compliance.label(c.get("action")).upper(), "one_line": c.get("one_line", ""),
            "fg": fg, "bg": bg, "order": _ORDER[status],
            "stats": _stats(d, positions.get(c["symbol"])) if positions is not None else None,
        })  # fmt: skip
    return sorted(rows, key=lambda r: r["order"])


def _request_lines(requests) -> list:
    return [client_requests.describe(r) for r in (requests or [])]


# Trade-update cards: (accent, background) per tone.
CARD = {"good": ("#16a34a", "#f0fdf4"), "warn": ("#d97706", "#fffbeb"), "crit": ("#dc2626", "#fef2f2"),
        "none": ("#9ca3af", "#f9fafb")}  # fmt: skip


def _trade_cards(trades) -> list:
    """holdings_update.panel()'s items, with the colours the email needs."""
    return [{**item, "accent": CARD[item["tone"]][0], "bg": CARD[item["tone"]][1]}
            for item in (trades or {}).get("items", [])]  # fmt: skip


def report(**ctx) -> str:
    backdrop = markets.display(ctx.get("review"), (ctx.get("calls") or {}).get("market_briefing"))
    ctx["requests"] = _request_lines(ctx.get("requests"))
    ctx["trades"] = _trade_cards(ctx.get("trades"))
    return _env().get_template("report.md").render(markets=backdrop, **ctx)


def email(
    day: str,
    review: dict,
    calls: dict,
    totals: dict,
    report_url,
    dashboard_url,
    dashboard_published,
    notes=(),
    names: dict | None = None,
    positions: dict | None = None,
    requests=(),
    trades=None,
) -> dict:
    """The daily email: {"subject", "text", "html"}. `positions`: {symbol: P&L row from
    pnl.compute()}, for the performance shown under "Your stocks". `trades`:
    holdings_update.panel(), shown near the top so he sees his update landed."""
    ctx = {
        "long_date": date.fromisoformat(day).strftime("%A, %-d %B %Y"),
        "totals": totals,
        "markets": markets.display(review, calls.get("market_briefing")),
        "requests": _request_lines(requests),
        "trades": _trade_cards(trades),
        "sections": [
            ("Your stocks", email_rows(calls.get("holdings", []), review.get("holdings", {}), names, positions or {})),
            ("Core ETFs", email_rows(calls.get("etfs", []), review.get("etfs", {}), names)),
            ("Watchlist", email_rows(calls.get("candidates", []), review.get("candidates", {}), names)),
        ],
        "report_url": report_url,
        "dashboard_url": dashboard_url if dashboard_published else None,
        "dashboard_published": dashboard_published,
        "notes": list(notes),
        "legend": [
            (label, *BADGES[s])
            for label, s in (("Buy / Add / Top up", "good"), ("Hold", "warn"), ("Trim / Sell", "crit"))
        ],  # fmt: skip
    }
    env = _env()
    return {
        "subject": subject(day),
        "text": env.get_template("email.txt").render(**ctx),
        "html": env.get_template("email.html").render(**ctx),
    }


def _idea_rows(card) -> list:
    """The month's best ideas, with what the email shows beside each."""
    best = (card or {}).get("best_ideas") or {}
    rows = []
    for i in best.get("ideas") or []:
        status = _STATUS.get(i.get("latest_action") or "", "none")
        fg, bg = BADGES[status]
        rows.append({**i, "label": compliance.label(i.get("latest_action")).upper() or "–", "fg": fg, "bg": bg,
                     "since_text": date.fromisoformat(i["since"]).strftime("%-d %b"),
                     "first_label": compliance.label(i.get("first_action"))})  # fmt: skip
    return rows


def monthly_email(ctx: dict, report_url=None) -> dict:
    """The monthly email: {"text", "html"}. `ctx` is what monthly.run() saves."""
    month = ctx["month"]
    card = ctx.get("card") or {}
    best = card.get("best_ideas") or {}
    data = {
        **ctx,
        "month_name": date.fromisoformat(f"{month}-01").strftime("%B %Y"),
        "month_short": date.fromisoformat(f"{month}-01").strftime("%B"),
        "ideas": _idea_rows(card),
        "considered": best.get("considered") or 0,
        "index": card.get("against_index"),
        "call_scores": ((card.get("calls") or {}).get("by_action")) or [],
        "too_recent": (card.get("calls") or {}).get("too_recent") or 0,
        "report_url": report_url,
        "badges": BADGES,
    }
    env = _env()
    return {
        "text": env.get_template("monthly_email.txt").render(**data),
        "html": env.get_template("monthly_email.html").render(**data),
    }


def render_template(name: str, **ctx) -> str:
    return _env().get_template(name).render(**ctx)
