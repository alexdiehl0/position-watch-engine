"""The day's market backdrop: a snapshot of the main indices, rates,
currencies and commodities (one Yahoo request), plus the market and
geopolitical headlines from analysis/news.py. Plain code; which of it
matters for which holding is the model's call.
"""

from position_watch.analysis import news
from position_watch.sources import yahoo

# (label, Yahoo ticker, unit). Yields move in basis points, the rest in %.
SNAPSHOT = (
    ("S&P 500", "^GSPC", "index"),
    ("Nasdaq 100", "^NDX", "index"),
    ("Euro Stoxx 50", "^STOXX50E", "index"),
    ("Hang Seng", "^HSI", "index"),
    ("VIX (fear gauge)", "^VIX", "index"),
    ("US 10-year yield", "^TNX", "yield"),
    ("EUR/USD", "EURUSD=X", "fx"),
    ("Brent oil", "BZ=F", "usd"),
    ("Gold", "GC=F", "usd"),
)


def _change(rows, back, unit):
    if len(rows) <= back:
        return None
    last, then = rows[-1][1], rows[-1 - back][1]
    return round((last - then) * 100) if unit == "yield" else round((last / then - 1) * 100, 2)


def snapshot():
    """[{name, level, unit, change_1d, change_5d, as_of, source}]; change in % (bp for yields). Returns (rows, error)."""
    history, err = yahoo.get_history_many([t for _, t, _ in SNAPSHOT], period="1mo")
    if err:
        return [], err
    rows = []
    for name, ticker, unit in SNAPSHOT:
        series = (history or {}).get(ticker) or []
        if not series:
            continue
        rows.append({"name": name, "level": round(series[-1][1], 2), "unit": unit, "change_1d": _change(series, 1, unit),
                     "change_5d": _change(series, 5, unit), "as_of": series[-1][0], "source": "yfinance"})  # fmt: skip
    missing = [n for n, t, _ in SNAPSHOT if not (history or {}).get(t)]
    return rows, (f"no Yahoo data for {', '.join(missing)}" if missing else None)


def gather(topics=()) -> dict:
    """`topics`: extra searches the client asked for (news_topic requests)."""
    rows, err = snapshot()
    headlines = news.market_news(extra_topics=topics)
    gaps = [e for e in [err, *headlines["errors"]] if e]
    return {"snapshot": rows, "headlines": headlines["headlines"], "data_gaps": gaps or None}


def _level_text(r: dict) -> str:
    v = r["level"]
    if r["unit"] == "yield":
        return f"{v:.2f}%"
    if r["unit"] == "usd":
        return f"${v:,.2f}"
    if r["unit"] == "fx":
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return f"{v:,.0f}" if v >= 1000 else f"{v:,.2f}"


def change_text(value, unit: str) -> str:
    if value is None:
        return "–"
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return f"{sign}{abs(value):.0f} bp" if unit == "yield" else f"{sign}{abs(value):.1f}%"


def display(review: dict | None, briefing: list | None) -> dict:
    """The backdrop as the email, report and dashboard show it: formatted
    snapshot rows, and the day's briefing with the headlines each item cites."""
    m = (review or {}).get("markets") or {}
    by_id = {h["id"]: {**h, "url": h["url"] if str(h.get("url", "")).startswith(("https://", "http://")) else None}
             for h in m.get("headlines") or []}  # fmt: skip
    rows = []
    for r in m.get("snapshot") or []:
        d1, d5 = change_text(r.get("change_1d"), r["unit"]), change_text(r.get("change_5d"), r["unit"])
        rows.append({**r, "level_text": _level_text(r), "d1": d1, "d5": d5,
                     "line": f"{r['name']} {_level_text(r)} ({d1})"})  # fmt: skip
    return {
        "snapshot": rows,
        "briefing": [
            {**b, "sources": [by_id[i] for i in b.get("headline_ids", []) if i in by_id]} for b in briefing or []
        ],  # fmt: skip
    }
