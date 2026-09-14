"""Renders the daily report (Markdown), the daily email (HTML plus a plain-text
alternative) and the monthly documents from the evidence and the model's calls.

Markdown and text templates are not escaped; the HTML email is, so text from
outside sources can't become markup. Company names come straight from the
evidence; only the calls and reasoning come from the model.
"""

from datetime import date

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from position_watch import compliance
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


def report(**ctx) -> str:
    return _env().get_template("report.md").render(**ctx)


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
) -> dict:
    """The daily email: {"subject", "text", "html"}. `positions`: {symbol: P&L row from
    pnl.compute()}, for the performance shown under "Your stocks"."""
    ctx = {
        "long_date": date.fromisoformat(day).strftime("%A, %-d %B %Y"),
        "totals": totals,
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


def render_template(name: str, **ctx) -> str:
    return _env().get_template(name).render(**ctx)
