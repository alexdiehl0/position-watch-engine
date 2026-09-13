"""Deterministic P&L for open positions: each holding in its own currency,
plus portfolio totals in USD. No network calls -- inputs are passed in.

Price per holding: the live price the daily review fetched for it
(state/latest_review.json), otherwise the broker's `last_price` from
holdings.csv, labelled "broker snapshot". A live price more than 50% away from
the broker price is treated as a probable unit or currency mismatch: the
broker price is used instead and the row is flagged.

Shares are `quantity_est`, netted from transactions.csv.

`python -m position_watch pnl` prints the totals plus the one-line summary
that opens the daily email.

Currency: per-holding values stay in the holding's currency. USD totals
convert non-USD positions with an explicit rate from `fx` (source and date
are carried through for display); if no rate is available, that position's
USD value falls back to the broker's own USD figure and is flagged. Cost in
USD is the broker's `invested_usd`, so USD P&L includes currency moves.
"""

MAX_LIVE_VS_BROKER_GAP = 0.5


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute(holdings_rows, review=None, fx=None):
    """holdings_rows: open rows from holdings.csv. review: latest_review.json
    dict (optional). fx: {"EUR": {"rate", "date", "source"}, ...} (optional).
    Returns {"positions": [...], "totals": {...}, "fx_used": {...}}."""
    evaluated = {**(review or {}).get("holdings", {}), **(review or {}).get("etfs", {})}
    fx = fx or {}

    positions, fx_used = [], {}
    for row in holdings_rows:
        symbol = row["symbol"]
        currency = (row.get("currency") or "USD").upper()
        shares = _f(row.get("quantity_est"))
        avg_cost = _f(row.get("avg_price"))
        broker_price = _f(row.get("last_price"))
        invested_usd = _f(row.get("invested_usd"))
        flags = []

        live = evaluated.get(symbol, {})
        live_price = _f(live.get("price"))
        price, price_source = broker_price, "broker snapshot"
        if live_price:
            if broker_price and abs(live_price - broker_price) / broker_price > MAX_LIVE_VS_BROKER_GAP:
                flags.append(
                    f"live price {live_price:,.2f} is more than 50% from the broker's "
                    f"{broker_price:,.2f}; using the broker price (check units/currency)"
                )
            else:
                price = live_price
                price_source = f"live [{(live.get('sources') or {}).get('price') or 'source n/a'}]"

        cost = shares * avg_cost if shares is not None and avg_cost is not None else None
        value = shares * price if shares is not None and price is not None else None
        pnl = value - cost if value is not None and cost is not None else None
        pnl_pct = pnl / cost * 100 if pnl is not None and cost else None

        if currency == "USD":
            value_usd = value
        elif currency in fx and value is not None:
            value_usd = value * fx[currency]["rate"]
            fx_used[currency] = fx[currency]
        else:
            broker_unrealized = _f(row.get("unrealized_gain_usd"))
            value_usd = (
                invested_usd + broker_unrealized if invested_usd is not None and broker_unrealized is not None else None
            )
            flags.append(f"no {currency}->USD rate available; USD value is the broker's snapshot figure")

        pnl_usd = value_usd - invested_usd if value_usd is not None and invested_usd is not None else None

        positions.append(
            {
                "symbol": symbol,
                "name": live.get("company_name") or live.get("name") or row.get("name"),
                "currency": currency,
                "shares": shares,
                "avg_cost": avg_cost,
                "price": price,
                "price_source": price_source,
                "cost": cost,
                "value": value,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "invested_usd": invested_usd,
                "value_usd": value_usd,
                "pnl_usd": pnl_usd,
                "dividends_usd": _f(row.get("total_dividend_usd")) or 0.0,
                "flags": flags,
            }
        )

    invested = sum(p["invested_usd"] or 0 for p in positions)
    value = sum(p["value_usd"] or 0 for p in positions)
    dividends = sum(p["dividends_usd"] for p in positions)
    pnl = value - invested
    totals = {
        "invested_usd": invested,
        "value_usd": value,
        "pnl_usd": pnl,
        "pnl_pct": pnl / invested * 100 if invested else None,
        "dividends_usd": dividends,
        "total_return_usd": pnl + dividends,
        "total_return_pct": (pnl + dividends) / invested * 100 if invested else None,
        "live_priced": sum(p["price_source"].startswith("live") for p in positions),
        "positions": len(positions),
    }
    return {"positions": positions, "totals": totals, "fx_used": fx_used}


def summary_line(totals):
    """The email's opening line: value, return on cost, dividends, total return."""

    def usd(v, signed=False):
        return f"{'+' if signed and v > 0 else '-' if v < 0 else ''}${abs(v):,.0f}"

    def pc(v):
        return f"{'+' if v > 0 else ''}{v:.1f}%" if v is not None else "n/a"

    line = (
        f"Portfolio value {usd(totals['value_usd'])} · return {usd(totals['pnl_usd'], True)} "
        f"({pc(totals['pnl_pct'])}) on {usd(totals['invested_usd'])} invested · "
        f"dividends received {usd(totals['dividends_usd'])} · "
        f"total return {usd(totals['total_return_usd'], True)} ({pc(totals['total_return_pct'])})"
    )
    if totals["live_priced"] < totals["positions"]:
        line += f" [{totals['live_priced']} of {totals['positions']} positions at live prices, the rest at the broker's last price]"
    return line
