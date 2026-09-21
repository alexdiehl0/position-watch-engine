"""The stock pool: every stock the system knows about and might put on the
watchlist, kept in state/stock_pool.json.

Each day review.py:
  1. refreshes the pool if it is older than `refresh_days` -- see `state`;
  2. screens and scores every pool stock not already screened today -- `screen`;
  3. sends the top scorers plus a couple of rotation picks to the full
     evaluation as the watchlist -- `pick`, with `sectors` deciding what
     answers a request for a sector or theme.

Deterministic code only: the score is plain arithmetic on live figures, and
the Buy/Hold call on each watchlist stock is still made by reasoning.py.

This module is the package's front door -- everything the rest of the engine
uses is re-exported here, so `from position_watch.analysis import pool` and
`pool.screen(...)` read the same as when this was one file.
"""

from position_watch.analysis.pool.pick import (
    FLOOR_FIELDS,
    FOCUS_KINDS,
    add_requested,
    expand_for,
    pick,
    summary,
)
from position_watch.analysis.pool.screening import (
    MAX_PE_FOR_VALUE,
    screen,
    screen_subset,
    screened_today,
)
from position_watch.analysis.pool.sectors import (
    MIN_TICKS,
    SECTORS,
    matches_request,
    sector_words,
    ticks,
)
from position_watch.analysis.pool.state import (
    US_TICKER,
    load,
    load_universe,
    needs_refresh,
    pool_path,
    refresh,
    save,
    universe_path,
)

__all__ = [
    "FLOOR_FIELDS", "FOCUS_KINDS", "MAX_PE_FOR_VALUE", "MIN_TICKS", "SECTORS", "US_TICKER",
    "add_requested", "expand_for", "load", "load_universe", "matches_request", "needs_refresh",
    "pick", "pool_path", "refresh", "save", "screen", "screen_subset", "screened_today",
    "sector_words", "summary", "ticks", "universe_path",
]  # fmt: skip
