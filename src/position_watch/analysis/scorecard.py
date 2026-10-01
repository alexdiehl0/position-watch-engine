"""Has any of this beaten simply buying the index?

The honest comparison for a portfolio built up over years is not "your return
vs the index's return over the same calendar window" -- the money went in at
different times. So this replays the client's own cash flows into the index:
every purchase buys index units at that day's close, every sale sells them.
What those units are worth today is what the same money would have made doing
nothing clever.

It also scores the daily calls: for each one, the stock's return since the call
against the index over exactly the same days, grouped by what was called.

And it picks the month's best ideas: the watchlist stocks first suggested during
the month (and not already held) that have risen most since that day, each with
the index over the same days and today's call -- for a client who may want to buy
what has been working. A strong run is not a reason in itself, so the latest call
travels with every figure.

Plain arithmetic on live prices; no judgement here.
"""

import csv
from collections import defaultdict
from datetime import date

from position_watch import settings, suggestion_log
from position_watch.sources import fx, yahoo

BENCHMARK = "^GSPC"
BENCHMARK_NAME = "S&P 500"
MIN_DAYS_FOR_CALLS = 21  # a call needs a few weeks before its return means anything
MIN_DAYS_FOR_IDEAS = 5  # a move over a day or two is noise, not "the month's best idea"


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _price_on(series: list, day: str):
    """The close on `day`, or the last one before it (markets close at weekends)."""
    earlier = [close for when, close in series if when <= day]
    return earlier[-1] if earlier else None


def cash_flows(transactions: list) -> tuple[list, list]:
    """[(date, usd)] -- money in positive, money out negative -- and any gaps."""
    flows, gaps, rates = [], [], {}
    for row in transactions:
        total, day = _f(row.get("total")), (row.get("date") or "")[:10]
        currency = (row.get("total_currency") or "USD").upper()
        if total is None or not day:
            gaps.append(f"transaction {row.get('symbol')} {day or '?'}: no total")
            continue
        if currency != "USD":
            key = (currency, day)
            if key not in rates:
                rate, err = fx.get_rate_on(day, currency, "USD")
                rates[key] = (rate or {}).get("rate")
                if err:
                    gaps.append(f"{currency}->USD on {day}: {err}")
            if not rates[key]:
                continue
            total *= rates[key]
        flows.append((day, total if (row.get("side") or "").lower() == "buy" else -total))
    return sorted(flows), gaps


def index_equivalent(flows: list, series: list) -> dict | None:
    """What the same cash, on the same days, would be worth in the index today."""
    if not flows or not series:
        return None
    units, invested = 0.0, 0.0
    for day, usd in flows:
        price = _price_on(series, day)
        if not price:
            continue
        units += usd / price
        invested += usd
    last_day, last_price = series[-1]
    return {"value_usd": round(units * last_price, 2), "net_invested_usd": round(invested, 2),
            "return_pct": round((units * last_price / invested - 1) * 100, 1) if invested > 0 else None,
            "as_of": last_day}  # fmt: skip


def against_index(transactions: list, totals: dict, series: list) -> dict | None:
    """The portfolio against the same cash flows in the index. `totals` is pnl.compute()'s."""
    flows, gaps = cash_flows(transactions)
    index = index_equivalent(flows, series)
    if not index or not index["net_invested_usd"]:
        return None
    # What the client holds now, plus the dividends those positions have paid.
    portfolio_value = (totals.get("value_usd") or 0) + (totals.get("dividends_usd") or 0)
    portfolio_return = round((portfolio_value / index["net_invested_usd"] - 1) * 100, 1)
    return {
        "benchmark": BENCHMARK_NAME,
        "net_invested_usd": index["net_invested_usd"],
        "portfolio_value_usd": round(portfolio_value, 2),
        "portfolio_return_pct": portfolio_return,
        "index_value_usd": index["value_usd"],
        "index_return_pct": index["return_pct"],
        "difference_pct": round(portfolio_return - (index["return_pct"] or 0), 1),
        "first_trade": flows[0][0],
        "as_of": index["as_of"],
        "gaps": gaps or None,
    }


def calls_against_index(entries: list, prices: dict, today: date) -> dict:
    """Every call's return since it was made, against the index over the same
    days, grouped by what was called. Only calls at least MIN_DAYS_FOR_CALLS old."""
    index = prices.get(BENCHMARK) or []
    buckets, skipped = defaultdict(list), 0
    first_seen = {}
    for entry in entries:
        key = (entry.get("symbol"), (entry.get("action") or "").lower())
        if key[0] and key[1] and key not in first_seen:
            first_seen[key] = entry.get("date", "")
    for (symbol, action), when in first_seen.items():
        series = prices.get(symbol) or []
        start, index_start = _price_on(series, when), _price_on(index, when)
        if not start or not index_start or (today - date.fromisoformat(when)).days < MIN_DAYS_FOR_CALLS:
            skipped += 1
            continue
        stock_pct = (series[-1][1] / start - 1) * 100
        index_pct = (index[-1][1] / index_start - 1) * 100
        buckets[action].append({"symbol": symbol, "since": when, "stock_pct": round(stock_pct, 1),
                                "index_pct": round(index_pct, 1), "difference_pct": round(stock_pct - index_pct, 1)})  # fmt: skip
    rows = []
    for action, calls in sorted(buckets.items()):
        rows.append(
            {
                "action": action,
                "calls": len(calls),
                "average_difference_pct": round(sum(c["difference_pct"] for c in calls) / len(calls), 1),
                "beat_the_index": sum(1 for c in calls if c["difference_pct"] > 0),
                "detail": sorted(calls, key=lambda c: -c["difference_pct"]),
            }
        )
    return {"by_action": rows, "too_recent": skipped, "benchmark": BENCHMARK_NAME}


def best_ideas(entries: list, prices: dict, month_start: str, month_end: str, held: set,
               limit: int = 5) -> dict:  # fmt: skip
    """The month's watchlist ideas that did best since first suggested, best first.
    Only ideas that are up are listed; `considered` says how many there were."""
    index = prices.get(BENCHMARK) or []
    first, latest = {}, {}
    for e in sorted(entries, key=lambda e: e.get("date") or ""):
        symbol = e.get("symbol")
        if e.get("scope") != "candidate" or not symbol:
            continue
        latest[symbol] = e
        if month_start <= (e.get("date") or "") <= month_end and symbol not in first:
            first[symbol] = e
    last_day = max((e.get("date") or "" for e in entries), default="")
    ideas, unpriced = [], []
    for symbol, e in first.items():
        if symbol in held:
            continue
        series = prices.get(symbol) or []
        start, index_start = _price_on(series, e["date"]), _price_on(index, e["date"])
        if not start or not index_start or not series:
            unpriced.append(symbol)
            continue
        if (date.fromisoformat(series[-1][0][:10]) - date.fromisoformat(e["date"])).days < MIN_DAYS_FOR_IDEAS:
            continue
        stock_pct = (series[-1][1] / start - 1) * 100
        index_pct = (index[-1][1] / index_start - 1) * 100
        now = latest[symbol]
        ideas.append({
            "symbol": symbol, "since": e["date"], "first_action": (e.get("action") or "").lower(),
            "stock_pct": round(stock_pct, 1), "index_pct": round(index_pct, 1),
            "difference_pct": round(stock_pct - index_pct, 1), "as_of": series[-1][0],
            "days": (date.fromisoformat(series[-1][0][:10]) - date.fromisoformat(e["date"])).days,
            "latest_action": (now.get("action") or "").lower(), "latest_date": now.get("date"),
            "latest_one_line": now.get("one_line") or "", "on_watchlist_now": now.get("date") == last_day,
        })  # fmt: skip
    ideas.sort(key=lambda i: -i["stock_pct"])
    return {"considered": len(first) - len(held & set(first)), "ideas": [i for i in ideas if i["stock_pct"] > 0][:limit],
            "unpriced": unpriced, "benchmark": BENCHMARK_NAME}  # fmt: skip


def load_transactions() -> list:
    """The trade history, or [] when the portfolio has none recorded."""
    path = settings.transactions_csv()
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def compute(transactions: list | None = None, totals: dict | None = None, today: date | None = None,
            month: tuple | None = None, held: set | None = None) -> dict:  # fmt: skip
    """The whole scorecard: one Yahoo request for the index and every symbol ever called.
    `month`: (first day, last day) as ISO dates, for the month's best ideas."""
    today = today or date.today()
    transactions = load_transactions() if transactions is None else transactions
    entries = suggestion_log.load_entries()
    symbols = sorted({e["symbol"] for e in entries if e.get("symbol")})
    prices, err = yahoo.get_history_many([BENCHMARK, *symbols], period="10y")
    if err or not (prices or {}).get(BENCHMARK):
        return {"error": err or f"no {BENCHMARK_NAME} prices returned", "against_index": None, "calls": None,
                "best_ideas": None}  # fmt: skip
    return {
        "against_index": against_index(transactions, totals, prices[BENCHMARK]),
        "calls": calls_against_index(entries, prices, today),
        "best_ideas": best_ideas(entries, prices, *month, held or set()) if month else None,
        "error": None,
    }
