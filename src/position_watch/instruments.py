"""Per-holding settings from the workspace's config/instruments.json: whether
a symbol is a stock, an ETF or a commodity, and its Yahoo Finance ticker.
Keeping this in the workspace means a new ETF or foreign listing is a
config line, not a code change."""

import json
from functools import cache

from position_watch import settings

STOCK, ETF, COMMODITY = "stock", "etf", "commodity"


@cache
def _load(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f).get("instruments", {})
    except FileNotFoundError:
        return {}


def _all() -> dict:
    return _load(str(settings.config_dir() / "instruments.json"))


def kind(symbol: str) -> str:
    return _all().get(symbol, {}).get("type", STOCK)


def yahoo_symbol(symbol: str) -> str:
    return _all().get(symbol, {}).get("yahoo", symbol)


def symbols_of(kind_name: str) -> set:
    return {s for s, v in _all().items() if v.get("type", STOCK) == kind_name}
