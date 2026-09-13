"""Single-stock evidence for a valuation judgement.

evaluate_stock() grounds every judgement in the metrics listed under
"Analysis framework" in CLAUDE.md, pulled live from a waterfall of sources
(FMP -> Finnhub -> yfinance) for each metric. The waterfall exists because
FMP's free plan refuses most symbols with HTTP 402 (see sources/fmp.py) and
Finnhub's free tier returns empty fundamentals for ETFs.
Whichever source returns a field first is used, and the source is recorded
in `sources` for citation. Nothing is ever filled in from training
knowledge: a field none of the three sources return is set to null with a
data_gap note, never guessed.

This function does NOT decide buy/sell -- it assembles the grounded
evidence (fundamentals, dividend quality, forecast signals, recent news
headlines) that a buy/sell/hold judgement should be based on. Turning that
evidence into a portfolio-wide suggestion happens one level up.
"""

from datetime import date, datetime, timezone

from position_watch.sources import finnhub, fmp, yahoo


def _latest(data):
    """FMP annual-period endpoints return most-recent-first lists."""
    return data[0] if data else None


def _pct_diff(price, target):
    if price in (None, 0) or target is None:
        return None
    return round((target - price) / price * 100, 2)


def _dividend_growth_streak(events):
    """events: list of {"date": "YYYY-MM-DD", "amount": float}, any order.
    Consecutive calendar years with a higher total dividend paid than the
    year before, counting back from the most recent full year."""
    if not events:
        return None

    by_year = {}
    for event in events:
        d, amount = event.get("date"), event.get("amount")
        if not d or amount is None:
            continue
        year = int(d[:4])
        by_year[year] = by_year.get(year, 0) + amount

    years = sorted(by_year.keys(), reverse=True)
    if len(years) < 2:
        return None

    current_year = datetime.now(timezone.utc).year
    if years[0] == current_year:
        years = years[1:]
    if len(years) < 2:
        return None

    streak = 0
    for i in range(len(years) - 1):
        if by_year[years[i]] > by_year[years[i + 1]]:
            streak += 1
        else:
            break
    return streak


def _earnings_surprise_pattern(earnings_events):
    reported = [
        e for e in (earnings_events or []) if e.get("epsActual") is not None and e.get("epsEstimated") is not None
    ]
    if not reported:
        return None
    reported = reported[:4]  # most recent first
    pattern = []
    for e in reported:
        actual, estimated = e["epsActual"], e["epsEstimated"]
        if actual > estimated:
            pattern.append("beat")
        elif actual < estimated:
            pattern.append("miss")
        else:
            pattern.append("in-line")
    return ", ".join(pattern) + " (most recent first)"


# ---- Per-source normalization -------------------------------------------
# Each _*_fields() returns a flat dict of normalized values (None where the
# source doesn't have that field). No exceptions escape here -- a failed
# API call just leaves every field None so the waterfall moves on.


def _fmp_fields(symbol: str) -> dict:
    out = {
        k: None
        for k in (
            "price",
            "pe",
            "forward_pe",
            "five_yr_avg_pe",
            "sector_avg_pe",
            "peg",
            "fcf_yield",
            "dividend_yield",
            "payout_ratio",
            "target_consensus",
            "recommendation_text",
            "eps_growth_pct",
            "sector",
            "dividend_events",
            "earnings_events",
            "company_name",
            "industry",
            "description",
        )
    }

    quote_data, quote_err = fmp.get_quote(symbol)
    if quote_err and (" 402 " in quote_err or " 403 " in quote_err or "quota" in quote_err):
        # The plan doesn't cover this symbol (or the quota is gone): the other
        # nine endpoints would refuse too, so don't spend calls on them.
        return out
    profile_data, profile_err = fmp.get_profile(symbol)
    ratios_data, ratios_err = fmp.get_ratios(symbol)
    key_metrics_data, key_metrics_err = fmp.get_key_metrics(symbol)
    target_data, target_err = fmp.get_price_target_consensus(symbol)
    estimates_data, estimates_err = fmp.get_analyst_estimates(symbol)
    grades_data, grades_err = fmp.get_grades_consensus(symbol)
    dividends_data, dividends_err = fmp.get_dividends(symbol)
    earnings_data, earnings_err = fmp.get_earnings(symbol)

    if not quote_err:
        q = _latest(quote_data) if isinstance(quote_data, list) else quote_data
        out["price"] = q.get("price") if q else None

    profile = _latest(profile_data) if not profile_err else None
    if profile:
        out["sector"] = profile.get("sector")
        out["company_name"] = profile.get("companyName")
        out["industry"] = profile.get("industry")
        out["description"] = _short_description(profile.get("description"))
        if out["price"] is None:
            out["price"] = profile.get("price")

    latest_ratios = _latest(ratios_data) if not ratios_err else None
    if latest_ratios:
        out["pe"] = latest_ratios.get("priceToEarningsRatio")
        out["peg"] = latest_ratios.get("priceToEarningsGrowthRatio")
        out["dividend_yield"] = _to_pct(latest_ratios.get("dividendYield"), already_pct=False)
        out["payout_ratio"] = _to_pct(latest_ratios.get("dividendPayoutRatio"), already_pct=False)

    if not ratios_err and ratios_data:
        pe_series = [r.get("priceToEarningsRatio") for r in ratios_data if r.get("priceToEarningsRatio") is not None]
        if len(pe_series) >= 2:
            out["five_yr_avg_pe"] = round(sum(pe_series) / len(pe_series), 2)

    if not estimates_err:
        next_estimate = _latest(estimates_data)
        eps_avg = next_estimate.get("epsAvg") if next_estimate else None
        if out["price"] is not None and eps_avg:
            out["forward_pe"] = round(out["price"] / eps_avg, 2)
        if estimates_data and len(estimates_data) >= 2:
            eps_now, eps_next = estimates_data[0].get("epsAvg"), estimates_data[1].get("epsAvg")
            if eps_now is not None and eps_next not in (None, 0):
                out["eps_growth_pct"] = round((eps_now - eps_next) / abs(eps_next) * 100, 1)

    if profile and profile.get("sector"):
        sector_snapshot, sector_err = fmp.get_sector_pe_snapshot(
            profile.get("exchange", "NASDAQ"), date.today().isoformat()
        )
        if not sector_err:
            match = next((s for s in sector_snapshot if s.get("sector") == profile["sector"]), None)
            out["sector_avg_pe"] = match.get("pe") if match else None

    if not key_metrics_err:
        km = _latest(key_metrics_data)
        out["fcf_yield"] = km.get("freeCashFlowYield") if km else None

    if not target_err:
        t = _latest(target_data)
        out["target_consensus"] = t.get("targetConsensus") if t else None

    if not grades_err:
        g = _latest(grades_data)
        if g and g.get("consensus"):
            out["recommendation_text"] = g["consensus"]

    if not dividends_err and dividends_data:
        out["dividend_events"] = [
            {"date": e.get("date"), "amount": e.get("adjDividend", e.get("dividend"))}
            for e in dividends_data
            if e.get("date") is not None
        ]

    if not earnings_err:
        out["earnings_events"] = earnings_data

    return out


def _finnhub_fields(symbol: str) -> dict:
    out = {
        k: None
        for k in (
            "price",
            "pe",
            "forward_pe",
            "five_yr_avg_pe",
            "peg",
            "dividend_yield",
            "payout_ratio",
            "target_consensus",
            "recommendation_text",
            "trailing_eps_growth_pct",
            "dividend_events",
            "company_name",
            "industry",
            "beta",
            "volatility_3m_pct",
            "week52_high",
            "week52_low",
        )
    }

    profile_data, profile_err = finnhub.get_company_profile(symbol)
    if not profile_err and isinstance(profile_data, dict):
        out["company_name"] = profile_data.get("name") or None
        out["industry"] = profile_data.get("finnhubIndustry") or None

    quote_data, quote_err = finnhub.get_quote(symbol)
    if not quote_err and isinstance(quote_data, dict):
        out["price"] = quote_data.get("c") or None

    metric_data, metric_err = finnhub.get_ratios(symbol)
    if not metric_err:
        m = (metric_data or {}).get("metric", {})
        out["pe"] = m.get("peTTM") or m.get("peAnnual") or m.get("peNormalizedAnnual")
        out["peg"] = m.get("pegTTM") or m.get("forwardPEG")
        out["dividend_yield"] = m.get("currentDividendYieldTTM") or m.get("dividendYieldIndicatedAnnual")
        out["payout_ratio"] = m.get("payoutRatioTTM") or m.get("payoutRatioAnnual")
        out["beta"] = m.get("beta")
        # Standard deviation of daily returns over 3 months, annualised, in %.
        out["volatility_3m_pct"] = m.get("3MonthADReturnStd")
        out["week52_high"] = m.get("52WeekHigh")
        out["week52_low"] = m.get("52WeekLow")

        pe_series = (metric_data or {}).get("series", {}).get("annual", {}).get("pe", [])
        values = [p["v"] for p in pe_series[:5] if p.get("v") is not None]
        if len(values) >= 2:
            out["five_yr_avg_pe"] = round(sum(values) / len(values), 2)

        # NOTE: this is trailing/reported EPS growth (annual actuals), not a
        # forward analyst-consensus estimate -- Finnhub's free tier doesn't
        # expose forward estimates. Kept as a separate field so it is never
        # presented as "consensus" growth (see eps_growth_pct, FMP-only).
        eps_series = (metric_data or {}).get("series", {}).get("annual", {}).get("eps", [])
        eps_values = [p["v"] for p in eps_series[:2] if p.get("v") is not None]
        if len(eps_values) == 2 and eps_values[1] not in (None, 0):
            out["trailing_eps_growth_pct"] = round((eps_values[0] - eps_values[1]) / abs(eps_values[1]) * 100, 1)

    rec_data, rec_err = finnhub.get_recommendation_trends(symbol)
    if not rec_err and rec_data:
        latest = rec_data[0]
        out["recommendation_text"] = (
            f"{latest.get('strongBuy', 0)} strong buy, {latest.get('buy', 0)} buy, "
            f"{latest.get('hold', 0)} hold, {latest.get('sell', 0)} sell, "
            f"{latest.get('strongSell', 0)} strong sell (period {latest.get('period')})"
        )

    target_data, target_err = finnhub.get_price_target(symbol)
    if not target_err and isinstance(target_data, dict):
        out["target_consensus"] = target_data.get("targetMean") or target_data.get("targetMedian")

    dividends_data, dividends_err = finnhub.get_dividends(symbol)
    if not dividends_err and dividends_data:
        out["dividend_events"] = [
            {"date": e.get("date") or e.get("payDate"), "amount": e.get("amount")}
            for e in dividends_data
            if (e.get("date") or e.get("payDate")) is not None
        ]

    return out


def _to_pct(value, already_pct: bool):
    if value is None:
        return None
    return round(value if already_pct else value * 100, 2)


def _short_description(text, max_chars: int = 280):
    """First sentence(s) of a provider's business summary, cut at a sentence
    boundary -- a deterministic trim of source text, never a rewrite."""
    if not text:
        return None
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    cut = text.rfind(". ", 0, max_chars)
    return text[: cut + 1] if cut > 0 else text[:max_chars].rsplit(" ", 1)[0] + "…"


def _yfinance_fields(symbol: str) -> dict:
    out = {
        k: None
        for k in (
            "price",
            "pe",
            "forward_pe",
            "peg",
            "fcf_yield",
            "dividend_yield",
            "payout_ratio",
            "target_consensus",
            "recommendation_text",
            "sector",
            "dividend_events",
            "company_name",
            "industry",
            "description",
            "beta",
            "week52_high",
            "week52_low",
        )
    }

    info, info_err = yahoo.get_info(symbol)
    if not info_err and info:
        out["price"] = info.get("currentPrice") or info.get("regularMarketPrice")
        out["pe"] = info.get("trailingPE")
        out["forward_pe"] = info.get("forwardPE")
        out["peg"] = info.get("pegRatio")
        out["dividend_yield"] = info.get("dividendYield")  # already percent, e.g. 3.1
        out["payout_ratio"] = _to_pct(info.get("payoutRatio"), already_pct=False)
        out["sector"] = info.get("sector")
        out["company_name"] = info.get("longName") or info.get("shortName")
        out["industry"] = info.get("industry")
        out["description"] = _short_description(info.get("longBusinessSummary"))
        out["beta"] = info.get("beta")
        out["week52_high"] = info.get("fiftyTwoWeekHigh")
        out["week52_low"] = info.get("fiftyTwoWeekLow")

        fcf, mcap = info.get("freeCashflow"), info.get("marketCap")
        if fcf is not None and mcap:
            out["fcf_yield"] = round(fcf / mcap * 100, 2)

        target = info.get("targetMeanPrice")
        if target is not None:
            out["target_consensus"] = target
            n = info.get("numberOfAnalystOpinions")
            key = info.get("recommendationKey")
            if key:
                out["recommendation_text"] = f"{key} ({n} analysts, mean target {target})" if n else key

    dividends_data, dividends_err = yahoo.get_dividends(symbol, limit=12)
    if not dividends_err and dividends_data:
        out["dividend_events"] = dividends_data

    return out


NUMERIC_WATERFALL_KEYS = (
    "price",
    "pe",
    "forward_pe",
    "five_yr_avg_pe",
    "sector_avg_pe",
    "peg",
    "fcf_yield",
    "dividend_yield",
    "payout_ratio",
    "target_consensus",
    "recommendation_text",
    "eps_growth_pct",
    "trailing_eps_growth_pct",
    "sector",
    "company_name",
    "industry",
    "description",
    "beta",
    "volatility_3m_pct",
    "week52_high",
    "week52_low",
)

# Plain convention for labelling annualised volatility; the broad US market
# usually runs around 15-20%.
VOLATILITY_BANDS = ((20, "low"), (35, "moderate"))


def volatility_level(vol_pct):
    if vol_pct is None:
        return None
    return next((label for limit, label in VOLATILITY_BANDS if vol_pct < limit), "high")


def _volatility_note(v, sources, price):
    parts = []
    if v["volatility_3m_pct"] is not None:
        parts.append(
            f"3-month volatility {v['volatility_3m_pct']:.1f}% annualised "
            f"({volatility_level(v['volatility_3m_pct'])}) [{sources['volatility_3m_pct']}]"
        )
    if v["beta"] is not None:
        parts.append(f"beta {v['beta']:.2f} vs the S&P 500 [{sources['beta']}]")
    high, low = v["week52_high"], v["week52_low"]
    if high and low:
        where = f", price {(price - low) / (high - low) * 100:.0f}% of the way up" if price and high > low else ""
        parts.append(
            f"52-week range {low:.2f}-{high:.2f} ({(high - low) / low * 100:.0f}% swing{where}) "
            f"[{sources['week52_high']}]"
        )
    return "; ".join(parts) if parts else None


def _gather_fundamentals(symbol: str):
    """Merges FMP -> Finnhub -> yfinance into one normalized dict, tracking
    which source served each field. Returns (values, sources, dividend_events,
    earnings_events, gaps)."""
    per_source = {
        "FMP": _fmp_fields(symbol),
        "Finnhub": _finnhub_fields(symbol),
        "yfinance": _yfinance_fields(symbol),
    }

    values, sources, gaps = {}, {}, []
    for key in NUMERIC_WATERFALL_KEYS:
        chosen, chosen_source = None, None
        for source_name in ("FMP", "Finnhub", "yfinance"):
            v = per_source[source_name].get(key)
            if v is not None:
                chosen, chosen_source = v, source_name
                break
        values[key] = chosen
        sources[key] = chosen_source
        if chosen is None:
            gaps.append(f"{key}: not returned by FMP, Finnhub, or yfinance")

    dividend_events, dividend_source = None, None
    for source_name in ("FMP", "Finnhub", "yfinance"):
        events = per_source[source_name].get("dividend_events")
        if events:
            dividend_events, dividend_source = events, source_name
            break
    if dividend_events is None:
        gaps.append("dividend history: not returned by FMP, Finnhub, or yfinance")

    earnings_events = per_source["FMP"].get("earnings_events")
    if not earnings_events:
        gaps.append("earnings surprise history: only FMP provides this and it returned nothing")

    return values, sources, dividend_events, dividend_source, earnings_events, gaps


def _recent_news(symbol: str, limit: int = 5):
    news_data, news_err = finnhub.get_company_news(symbol)
    if news_err:
        return None, news_err
    items = [
        {
            "headline": n.get("headline"),
            "source": n.get("source"),
            "date": datetime.fromtimestamp(n["datetime"], tz=timezone.utc).date().isoformat()
            if n.get("datetime")
            else None,
            "url": n.get("url"),
        }
        for n in news_data[:limit]
    ]
    return items, None


def evaluate_stock(symbol: str, holding_data: dict = None) -> dict:
    pulled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    v, sources, dividend_events, dividend_source, earnings_events, data_gaps = _gather_fundamentals(symbol)
    price = v["price"]

    target_spread_pct = _pct_diff(price, v["target_consensus"])

    valuation_signals = []
    if v["pe"] is not None and v["sector_avg_pe"] is not None:
        valuation_signals.append("undervalued" if v["pe"] < v["sector_avg_pe"] else "overvalued")
    if v["pe"] is not None and v["five_yr_avg_pe"] is not None:
        valuation_signals.append("undervalued" if v["pe"] < v["five_yr_avg_pe"] else "overvalued")
    if v["peg"] is not None and v["peg"] > 0:
        # A negative PEG (usually declining/negative earnings growth) is not
        # a valid undervalued/overvalued signal -- only a positive PEG is.
        valuation_signals.append("undervalued" if v["peg"] < 1 else "overvalued")
    if target_spread_pct is not None:
        valuation_signals.append("undervalued" if target_spread_pct > 0 else "overvalued")

    valuation, valuation_reason = None, None
    if not valuation_signals:
        data_gaps.append("valuation: no valuation metric could be computed from returned data")
    else:
        under, over = valuation_signals.count("undervalued"), valuation_signals.count("overvalued")
        valuation = "undervalued" if under > over else "overvalued" if over > under else "fairly valued"

        reasons = []
        if v["pe"] is not None and v["sector_avg_pe"] is not None:
            reasons.append(f"P/E {v['pe']:.2f} vs. sector avg {v['sector_avg_pe']:.2f} [{sources['sector_avg_pe']}]")
        if v["pe"] is not None and v["five_yr_avg_pe"] is not None:
            reasons.append(f"P/E {v['pe']:.2f} vs. 5-yr avg {v['five_yr_avg_pe']:.2f} [{sources['five_yr_avg_pe']}]")
        if v["peg"] is not None:
            caveat = " (negative -- not used as a signal, likely declining/negative earnings)" if v["peg"] <= 0 else ""
            reasons.append(f"PEG {v['peg']:.2f}{caveat} [{sources['peg']}]")
        if target_spread_pct is not None:
            reasons.append(
                f"price is {target_spread_pct:+.1f}% vs. analyst consensus target "
                f"{v['target_consensus']:.2f} [{sources['target_consensus']}]"
            )
        valuation_reason = "; ".join(reasons) + "." if reasons else None

    growth_streak = _dividend_growth_streak(dividend_events) if dividend_events else None
    if dividend_events is not None and growth_streak is None:
        data_gaps.append("dividend growth streak: insufficient dividend history to compute")

    dividend_quality_parts = []
    if v["dividend_yield"] is not None:
        dividend_quality_parts.append(f"yield {v['dividend_yield']:.2f}% [{sources['dividend_yield']}]")
    if v["payout_ratio"] is not None:
        flag = " — FLAG: near/over 100% payout" if v["payout_ratio"] >= 90 else ""
        dividend_quality_parts.append(f"payout ratio {v['payout_ratio']:.1f}%{flag} [{sources['payout_ratio']}]")
    if growth_streak is not None:
        dividend_quality_parts.append(f"{growth_streak}-year growth streak [{dividend_source}]")
    if v["fcf_yield"] is not None and v["payout_ratio"] is not None:
        dividend_quality_parts.append(
            "dividend appears covered by FCF" if v["fcf_yield"] > 0 else "dividend coverage by FCF unclear"
        )

    dividend_quality = "; ".join(dividend_quality_parts) if dividend_quality_parts else None
    if dividend_quality is None:
        data_gaps.append("dividend quality: no dividend metrics could be computed from returned data")

    forecast_parts = []
    if v["recommendation_text"]:
        forecast_parts.append(f"analyst consensus: {v['recommendation_text']} [{sources['recommendation_text']}]")
    if v["eps_growth_pct"] is not None:
        forecast_parts.append(
            f"consensus EPS growth (analyst estimate) {v['eps_growth_pct']:+.1f}% [{sources['eps_growth_pct']}]"
        )
    elif v["trailing_eps_growth_pct"] is not None:
        forecast_parts.append(
            f"trailing EPS growth (reported, not a forward estimate) "
            f"{v['trailing_eps_growth_pct']:+.1f}% [{sources['trailing_eps_growth_pct']}]"
        )

    surprise_pattern = _earnings_surprise_pattern(earnings_events)
    if surprise_pattern:
        forecast_parts.append(f"recent surprises: {surprise_pattern} [FMP]")

    forecast_note = "; ".join(forecast_parts) if forecast_parts else None
    if forecast_note is None:
        data_gaps.append("forecast: no analyst/consensus signal could be computed from returned data")

    news, news_err = _recent_news(symbol)
    if news_err:
        data_gaps.append(f"recent news: {news_err}")

    return {
        "symbol": symbol,
        "company_name": v["company_name"],
        "sector": v["sector"],
        "industry": v["industry"],
        "description": v["description"],
        "price": price,
        "valuation": valuation,
        "valuation_reason": valuation_reason,
        "dividend_quality": dividend_quality,
        "forecast_note": forecast_note,
        "volatility": volatility_level(v["volatility_3m_pct"]),
        "volatility_3m_pct": v["volatility_3m_pct"],
        "beta": v["beta"],
        "volatility_note": _volatility_note(v, sources, price),
        "recent_news": news,
        "sources": sources,
        "data_as_of": pulled_at,
        "data_gaps": data_gaps if data_gaps else None,
    }
