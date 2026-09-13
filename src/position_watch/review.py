"""Portfolio-wide evidence gathering for the daily review.

Evaluates every open holding in the workspace's holdings.csv (stocks through
analysis/stock.py, ETFs through analysis/etf.py, commodities listed as not
evaluated -- see config/instruments.json), picks the day's watchlist from the
stock pool, fetches exchange rates, and writes state/latest_review.json.

This module does NOT decide Buy/Hold/Sell. It only assembles evidence, each
figure tagged with the live source that returned it; the daily routine's
reasoning step turns it into suggestions. Nothing is estimated -- a gap is
reported, never filled in.
"""

import csv
import json
from datetime import date, datetime, timezone

from position_watch import instruments, settings
from position_watch.analysis import pool as stock_pool
from position_watch.analysis.etf import evaluate_etf
from position_watch.analysis.stock import evaluate_stock
from position_watch.sources import finnhub, fmp, fx


def load_open_holdings() -> list:
    with open(settings.holdings_csv(), newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("status") == "open"]


def run(include_candidates: bool = True) -> dict:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    finnhub.reset_run_state()
    fmp.reset_run_state()
    holdings = load_open_holdings()
    results = {"holdings": {}, "etfs": {}, "candidates": {}, "excluded": []}

    for row in holdings:
        symbol = row["symbol"]
        kind = instruments.kind(symbol)
        if kind == instruments.COMMODITY:
            results["excluded"].append(
                {
                    "symbol": symbol,
                    "reason": "commodity/FX position -- no equity valuation/dividend framework applies",
                }
            )
        elif kind == instruments.ETF:
            results["etfs"][symbol] = evaluate_etf(symbol, row)
        else:
            results["holdings"][symbol] = evaluate_stock(symbol, row)

    if include_candidates:
        today = date.today()
        held = {r["symbol"] for r in holdings}
        universe, pool = stock_pool.load_universe(), stock_pool.load()
        pool_notes = []
        if stock_pool.needs_refresh(pool, universe, today):
            pool_notes = stock_pool.refresh(pool, universe, sorted(results["holdings"]), today)
        stock_pool.screen(pool, universe, today)
        picks = stock_pool.pick(pool, universe, held, today)
        stock_pool.save(pool)
        for symbol, slot in picks:
            evidence = evaluate_stock(symbol)
            entry = pool["stocks"][symbol]
            evidence["pool"] = {
                "slot": slot,
                "score": entry["screen"]["score"],
                "screen": entry["screen"]["why"],
                "via": entry.get("via"),
                "times_on_watchlist": entry["shortlisted"]["times"],
            }
            results["candidates"][symbol] = evidence
        results["pool"] = stock_pool.summary(pool, picks, held, today)
        results["pool"]["notes"] = pool_notes

    # Rates for converting non-USD holdings into USD portfolio totals (P&L).
    results["fx"], results["fx_errors"] = {}, {}
    for currency in sorted({(r.get("currency") or "USD").upper() for r in holdings} - {"USD"}):
        rate, err = fx.get_rate(currency, "USD")
        if rate:
            results["fx"][currency] = rate
        else:
            results["fx_errors"][currency] = err

    results["run"] = {"started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return results


def save(results: dict):
    path = settings.latest_review_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(results, f, indent=2, default=str)


def summarize(results: dict) -> dict:
    """A short overview for the terminal; the full evidence is in the file."""

    def gaps(section):
        return {s: len(d.get("data_gaps") or []) for s, d in results.get(section, {}).items()}

    return {
        "written_to": str(settings.latest_review_path()),
        "holdings": gaps("holdings"),
        "etfs": {
            s: ("live price" if d.get("price") is not None else "no live data") for s, d in results["etfs"].items()
        },
        "watchlist": {s: (d.get("pool") or {}).get("slot") for s, d in results.get("candidates", {}).items()},
        "excluded": [e["symbol"] for e in results.get("excluded", [])],
        "fx": {c: f"{r['rate']} ({r['source']}, {r.get('date')})" for c, r in results.get("fx", {}).items()},
        "fx_errors": results.get("fx_errors", {}),
        "pool": {k: (results.get("pool") or {}).get(k) for k in ("size", "passed", "refreshed")},
    }
