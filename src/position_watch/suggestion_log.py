"""History of daily suggestions, for the monthly tracking routine.

Each daily run records one row per symbol it evaluated (holding,
ETF or watchlist candidate) to the workspace's state/suggestion_log.csv. This is deliberately a separate,
permanent, structured file rather than something derived by re-parsing
markdown reports each month -- the monthly tracker needs clean historical
data to spot patterns (a symbol suggested "Buy" for 10 days running, an
action that flipped from Hold to Sell, etc.) without re-deriving it from
prose.

Deterministic file I/O only -- the actual action/reasoning is decided by
the daily routine's synthesis step, not here.
"""

import csv

from position_watch import settings


def log_path():
    return settings.state_dir() / "suggestion_log.csv"


FIELDS = ["date", "scope", "symbol", "action", "one_line"]


def append_entries(date: str, holdings: dict, candidates: dict, etfs: dict = None):
    """holdings/candidates/etfs: {symbol: {"action":..., "one_line":...}}

    A second run on the same date replaces that date's rows, so a re-run never
    counts a day's calls twice."""
    log_path().parent.mkdir(parents=True, exist_ok=True)
    kept = [row for row in load_entries() if row.get("date") != date]

    with open(log_path(), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept)
        for scope, entries in (("holding", holdings), ("etf", etfs or {}), ("candidate", candidates)):
            for symbol, data in entries.items():
                writer.writerow(
                    {
                        "date": date,
                        "scope": scope,
                        "symbol": symbol,
                        "action": data.get("action") or "",
                        "one_line": (data.get("one_line") or "").replace("\n", " "),
                    }
                )


def load_entries():
    if not log_path().exists():
        return []
    with open(log_path(), newline="") as f:
        return list(csv.DictReader(f))
