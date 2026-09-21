"""Screening and scoring: one row of figures per pool stock, once a day.

US listings come from Finnhub (P/E against its own five-year median, dividend
yield, payout ratio, market cap); everything else comes from Yahoo, which has
no P/E history, so value there rests on forward against trailing P/E. Volatility
and beta are recorded for display, not scored.

The score is plain arithmetic on live figures fitted to the client's stated
preferences -- strongly undervalued, high dividend. Nothing here decides
Buy or Hold; that is the one AI step, in reasoning.py.
"""

import statistics

from position_watch.analysis import numbers, volatility
from position_watch.analysis.pool.state import US_TICKER
from position_watch.sources import finnhub, fx, yahoo

# Above this P/E a stock isn't "strongly undervalued" whatever its own
# history says (a P/E of 130 can still be half its past level).
MAX_PE_FOR_VALUE = 40


def _score(pe, median_pe, yld, payout, forward_pe=None):
    """Plain-arithmetic fit to the client's preferences, roughly -2 to +2:
    discount to its own 5-year median P/E (up to +/-1, capped at 50%; no
    credit above MAX_PE_FOR_VALUE), dividend yield (up to +1 at 8%), minus
    a penalty for a stretched payout."""
    parts, score = [], 0.0
    if pe is not None and pe <= 0:
        score -= 0.5
        parts.append("loss-making (negative P/E)")
    elif pe is not None and median_pe and median_pe > 0:
        gap = (median_pe - pe) / median_pe
        value = max(-0.5, min(0.5, gap)) * 2
        if pe > MAX_PE_FOR_VALUE:
            value = min(value, 0.0)
        score += value
        parts.append(
            f"P/E {pe:.1f} vs 5-yr median {median_pe:.1f} ({abs(gap) * 100:.0f}% {'below' if gap >= 0 else 'above'})"
            + (f", but over {MAX_PE_FOR_VALUE}" if pe > MAX_PE_FOR_VALUE and gap > 0 else "")
        )
    elif pe is not None and forward_pe and pe > 0:
        # No P/E history (the free data plans have none outside the US): the
        # most we can say is whether earnings are expected to grow into it.
        gap = (pe - forward_pe) / pe
        score += max(-0.3, min(0.3, gap))
        parts.append(f"P/E {pe:.1f} vs forward {forward_pe:.1f}, no 5-yr history")
    elif pe is not None:
        parts.append(f"P/E {pe:.1f} (no 5-yr history)")
    if yld:
        score += min(yld, 8) / 8
        parts.append(f"yield {yld:.1f}%")
    if payout is not None and yld:
        if payout > 100:
            score -= 0.5
            parts.append(f"payout {payout:.0f}% (over 100%)")
        elif payout >= 80:
            score -= 0.2
            parts.append(f"payout {payout:.0f}% (high)")
        else:
            parts.append(f"payout {payout:.0f}%")
    return round(score, 3), " · ".join(parts)


def screen_subset(pool, universe, today, symbols):
    """Screens only these pool stocks (used after an on-demand expansion)."""
    subset = {"stocks": {s: pool["stocks"][s] for s in symbols if s in pool["stocks"]}}
    screen(subset, universe, today)
    for s, entry in subset["stocks"].items():
        pool["stocks"][s] = entry


_f = numbers.to_float


def _result(today, **fields) -> dict:
    """One shape for a screen result, whichever source filled it.

    The US and non-US paths read different vendors but must store the same row:
    the dashboard, the score and `summary()` all index it by key. Built here so
    a new field is added once rather than in two places that can drift."""
    row = {
        "date": today.isoformat(),
        "pe": None,
        "five_yr_median_pe": None,
        "dividend_yield_pct": None,
        "payout_ratio_pct": None,
        "market_cap_usd_m": None,
        "volatility_3m_pct": None,
        "volatility_source": None,
        "beta": None,
        "sector": None,
        "currency": None,
        "score": None,
        "why": None,
        "passed": False,
        "filtered_out": None,
        "gaps": None,
    }
    row.update(fields)
    return row


def _screen_non_us(symbol, entry, universe, today, closes, rates):
    """Screens one non-US stock and stores the result, the same shape as the US path."""
    values, gaps = _screen_yahoo(symbol, rates)
    vol = volatility.from_closes((closes or {}).get(symbol) or [])
    result = _result(
        today,
        pe=values.get("pe"),
        dividend_yield_pct=values.get("dividend_yield_pct"),
        payout_ratio_pct=values.get("payout_ratio_pct"),
        market_cap_usd_m=values.get("market_cap_usd_m"),
        volatility_3m_pct=vol,
        volatility_source=volatility.SOURCE if vol is not None else None,
        beta=values.get("beta"),
        sector=values.get("sector"),
        currency=values.get("currency"),
        gaps=gaps or None,
    )
    cap, floor = result["market_cap_usd_m"], universe["min_market_cap_usd_m"]
    if not values:
        result["filtered_out"] = "no data from Yahoo"
    elif cap is not None and cap < floor:
        result["filtered_out"] = f"market cap under ${floor / 1000:.0f}B"
    elif result["pe"] is None and not result["dividend_yield_pct"]:
        result["filtered_out"] = "no P/E or dividend to judge"
    else:  # a missing market cap is a gap, not a reason to drop it
        result["score"], result["why"] = _score(result["pe"], None, result["dividend_yield_pct"],
                                                result["payout_ratio_pct"], values.get("forward_pe"))  # fmt: skip
        result["passed"] = True
    entry["screen"] = result


def _screen_yahoo(symbol: str, rates: dict) -> tuple[dict, list]:
    """A non-US stock's screen, from Yahoo: the free US data plans don't cover
    them. Yahoo has no 5-year P/E history, so value rests on forward vs
    trailing P/E and the yield; what's missing is said, never filled in."""
    info, err = yahoo.get_info(symbol)
    if err or not info:
        return {}, [err or f"no Yahoo fundamentals for {symbol}"]
    gaps = ["five_yr_median_pe: Yahoo has no P/E history"]
    currency = (info.get("currency") or "USD").upper()
    if currency == "GBP":  # London prices come in pence
        currency = "GBP"
    cap = _f(info.get("marketCap"))
    if cap is not None and currency != "USD":
        if currency not in rates:
            rate, rate_err = fx.get_rate(currency, "USD")
            rates[currency] = (rate or {}).get("rate")
            if rate_err:
                gaps.append(f"{currency}->USD rate: {rate_err}")
        cap = cap * rates[currency] if rates.get(currency) else None
    if cap is None:
        gaps.append("market cap: not returned by Yahoo")
    yield_pct, yield_gap = numbers.dividend_yield_pct(info)
    if yield_gap:
        gaps.append(yield_gap)
    return {
        "pe": _f(info.get("trailingPE")),
        "forward_pe": _f(info.get("forwardPE")),
        "five_yr_median_pe": None,
        "dividend_yield_pct": yield_pct,
        "payout_ratio_pct": numbers.to_pct(info.get("payoutRatio"), already_pct=False),
        "market_cap_usd_m": cap / 1e6 if cap else None,
        "sector": info.get("sector"),
        "currency": currency,
        "volatility_3m_pct": None,
        "beta": _f(info.get("beta3Year") or info.get("beta")),
    }, gaps


def screened_today(entry, today) -> bool:
    return (entry.get("screen") or {}).get("date") == today.isoformat()


def screen(pool, universe, today, force: bool = False):
    """One Finnhub call per US pool stock (Yahoo fundamentals for the rest),
    plus one Yahoo request for everyone's daily prices (volatility is computed
    from those, the vendor's figure is the fallback). Stores each result on the entry.

    A stock already screened today is left alone. The figures are daily, so a
    second pass on the same date would spend the same ~420 calls to write the
    same numbers -- which is exactly what a re-run after a failure used to do:
    three failed runs on 19 Sept 2026 screened the whole pool three times over
    and produced no review at all. `force` re-screens regardless.
    """
    due = {s: e for s, e in pool["stocks"].items() if force or not screened_today(e, today)}
    if not due:
        return
    closes, _ = yahoo.get_closes_many(list(due))
    rates: dict = {}
    for symbol, entry in due.items():
        if entry.get("market") == "europe" or not US_TICKER.fullmatch(symbol):
            _screen_non_us(symbol, entry, universe, today, closes, rates)
            continue
        data, err = finnhub.get_ratios(symbol)
        m = (data or {}).get("metric") or {}
        pe_series = (data or {}).get("series", {}).get("annual", {}).get("pe", [])
        pe_values = [p["v"] for p in pe_series[:5] if p.get("v") is not None]
        pe = m.get("peTTM") or m.get("peAnnual")
        # Median, not mean: one freak year (tiny earnings -> P/E of 1,000+)
        # would otherwise make almost any current P/E look cheap.
        median_pe = round(statistics.median(pe_values), 2) if len(pe_values) >= 3 else None
        yld = m.get("currentDividendYieldTTM") or m.get("dividendYieldIndicatedAnnual")
        payout = m.get("payoutRatioTTM") or m.get("payoutRatioAnnual")
        mcap = m.get("marketCapitalization")
        vol = volatility.from_closes((closes or {}).get(symbol) or [])

        result = _result(
            today,
            pe=pe,
            five_yr_median_pe=median_pe,
            dividend_yield_pct=yld,
            payout_ratio_pct=payout,
            market_cap_usd_m=mcap,
            volatility_3m_pct=vol if vol is not None else m.get("3MonthADReturnStd"),
            volatility_source=(
                volatility.SOURCE if vol is not None else "Finnhub" if m.get("3MonthADReturnStd") else None
            ),
            beta=m.get("beta"),
        )
        if err or not m:
            result["filtered_out"] = "no data from Finnhub"
        elif not mcap or mcap < universe["min_market_cap_usd_m"]:
            result["filtered_out"] = f"market cap under ${universe['min_market_cap_usd_m'] / 1000:.0f}B"
        elif pe is None and not yld:
            result["filtered_out"] = "no P/E or dividend to judge"
        else:
            result["score"], result["why"] = _score(pe, median_pe, yld, payout)
            result["passed"] = True
        entry["screen"] = result
