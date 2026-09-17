"""The stock pool: every stock the system knows about and might put on the
watchlist, kept in state/stock_pool.json.

Each day portfolio_review.py:
  1. refreshes the pool if it is older than `refresh_days` (config/stock_universe.json):
     the seed list, plus Finnhub's similar companies for each stock held and
     for each watchlist stock rated Buy or Add in the past week;
  2. screens every pool stock with one Finnhub call (P/E vs its own 5-year
     median, dividend yield, payout ratio, market cap; volatility and beta
     are recorded for display, not scored) and scores it on the
     client's stated preferences: strongly undervalued, high dividend;
  3. sends the top scorers plus a couple of rotation picks (the pool stocks it
     has looked at least recently) to the full evaluation as the watchlist.

Every stock ever put on the watchlist stays in the pool with a count of how
often, so the pool is also the record of what has been suggested.

Deterministic code only: the score is plain arithmetic on live figures, and
the Buy/Hold call on each watchlist stock is still made by the daily routine.
"""

import json
import re
import statistics
from datetime import date, timedelta

from position_watch import client_requests, settings, suggestion_log
from position_watch.analysis import volatility
from position_watch.sources import finnhub, fmp, fx, yahoo


def universe_path():
    return settings.config_dir() / "stock_universe.json"


def pool_path():
    return settings.state_dir() / "stock_pool.json"


# Finnhub's free plan covers US listings; peers such as 9618.HK are skipped.
US_TICKER = re.compile(r"[A-Z]{1,5}")


def load_universe():
    with open(universe_path()) as f:
        return json.load(f)


def load():
    if not pool_path().exists():
        return {"refreshed": None, "stocks": {}}
    with open(pool_path()) as f:
        return json.load(f)


def save(pool):
    pool_path().parent.mkdir(parents=True, exist_ok=True)
    pool["_about"] = (
        "Stocks the watchlist is picked from. Written by position_watch/analysis/pool.py during the "
        "daily review; settings and seed list are in config/stock_universe.json."
    )
    with open(pool_path(), "w") as f:
        json.dump(pool, f, indent=2, sort_keys=True)
        f.write("\n")


def needs_refresh(pool, universe, today):
    last = pool.get("refreshed")
    return not last or date.fromisoformat(last) <= today - timedelta(days=universe["refresh_days"])


def _recent_buy_adds(today, days=7):
    since = (today - timedelta(days=days)).isoformat()
    return sorted(
        {
            e["symbol"]
            for e in suggestion_log.load_entries()
            if e.get("scope") == "candidate"
            and e.get("date", "") >= since
            and (e.get("action") or "").lower() in ("buy", "add")
        }
    )


def refresh(pool, universe, held, today):
    """Adds seeds and similar companies, fetches names for new entries,
    drops stocks that failed the last screen (unless seeded or ever on the
    watchlist), and trims to `pool_cap`. Returns a list of notes."""
    stocks, notes, added = pool.setdefault("stocks", {}), [], 0
    today_s = today.isoformat()

    def add(symbol, via, market="us"):
        nonlocal added
        if symbol in stocks or (market == "us" and not US_TICKER.fullmatch(symbol)):
            return
        stocks[symbol] = {"added": today_s, "via": via, **({"market": market} if market != "us" else {})}
        added += 1

    seeded = set()
    for group, symbols in universe["seeds"].items():
        market = "europe" if group.strip().lower() == "europe" else "us"  # screened through Yahoo
        for s in symbols:
            seeded.add(s)
            add(s, f"starting list: {group}", market)
    for s in stocks:
        stocks[s]["seed"] = s in seeded

    # A whole index, when the data plan allows it: 500 names beats any hand-written
    # list. The free plan refuses this, and the seed list above carries the pool.
    for index in universe.get("index_seeds") or []:
        members, err = fmp.get_index_constituents(index)
        if err:
            notes.append(f"{index} constituents: {err}")
            continue
        for member in members or []:
            symbol = (member.get("symbol") or "").upper()
            seeded.add(symbol)
            add(symbol, f"in the {index.upper()}")

    sources = [s for s in held if US_TICKER.fullmatch(s)] + _recent_buy_adds(today)
    for src in dict.fromkeys(sources):
        for grouping in ("industry", "sector"):
            peers, err = finnhub.get_peers(src, grouping)
            if err:
                notes.append(f"similar companies for {src} ({grouping}): {err}")
                continue
            for p in peers:
                if p != src:
                    add(p, f"similar to {src}")

    for s in [
        s
        for s, e in stocks.items()
        if not e.get("seed") and not e.get("shortlisted") and e.get("screen") and not e["screen"].get("passed")
    ]:
        del stocks[s]

    if len(stocks) > universe["pool_cap"]:

        def keep(e):
            return bool(e.get("seed") or e.get("shortlisted"))

        others = sorted(
            (s for s, e in stocks.items() if not keep(e)),
            key=lambda s: -(stocks[s].get("screen") or {}).get("score", 99),
        )
        room = max(0, universe["pool_cap"] - sum(keep(e) for e in stocks.values()))
        for s in others[room:]:
            del stocks[s]

    for s, e in stocks.items():
        if "name" in e:
            continue
        if e.get("market") == "europe":  # Finnhub's free plan has no non-US profiles
            info, err = yahoo.get_info(s)
            e["name"] = (info or {}).get("longName") or (info or {}).get("shortName")
            e["industry"] = (info or {}).get("industry") or (info or {}).get("sector")
        else:
            profile, err = finnhub.get_company_profile(s)
            e["name"] = (profile or {}).get("name") or None
            e["industry"] = (profile or {}).get("finnhubIndustry") or None

    pool["refreshed"] = today_s
    notes.insert(0, f"refreshed {today_s}: {added} added, {len(stocks)} in pool")
    return notes


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


FOCUS_KINDS = client_requests.FOCUS


def screen_subset(pool, universe, today, symbols):
    """Screens only these pool stocks (used after an on-demand expansion)."""
    subset = {"stocks": {s: pool["stocks"][s] for s in symbols if s in pool["stocks"]}}
    screen(subset, universe, today)
    for s, entry in subset["stocks"].items():
        pool["stocks"][s] = entry


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _screen_non_us(symbol, entry, universe, today, closes, rates):
    """Screens one non-US stock and stores the result, the same shape as the US path."""
    values, gaps = _screen_yahoo(symbol, rates)
    vol = volatility.from_closes((closes or {}).get(symbol) or [])
    result = {
        "date": today.isoformat(),
        "pe": values.get("pe"),
        "five_yr_median_pe": None,
        "dividend_yield_pct": values.get("dividend_yield_pct"),
        "payout_ratio_pct": values.get("payout_ratio_pct"),
        "market_cap_usd_m": values.get("market_cap_usd_m"),
        "volatility_3m_pct": vol,
        "volatility_source": volatility.SOURCE if vol is not None else None,
        "beta": values.get("beta"),
        "sector": values.get("sector"),
        "currency": values.get("currency"),
        "score": None,
        "why": None,
        "passed": False,
        "filtered_out": None,
        "gaps": gaps or None,
    }
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
    return {
        "pe": _f(info.get("trailingPE")),
        "forward_pe": _f(info.get("forwardPE")),
        "five_yr_median_pe": None,
        "dividend_yield_pct": _to_pct(_f(info.get("dividendYield"))),
        "payout_ratio_pct": (_f(info.get("payoutRatio")) or 0) * 100 if info.get("payoutRatio") is not None else None,
        "market_cap_usd_m": cap / 1e6 if cap else None,
        "sector": info.get("sector"),
        "currency": currency,
        "volatility_3m_pct": None,
        "beta": _f(info.get("beta3Year") or info.get("beta")),
    }, gaps


def _to_pct(value):
    """Yahoo reports a yield either as 4.5 or as 0.045, depending on the field's day."""
    if value is None:
        return None
    return round(value * 100, 3) if value < 1 else round(value, 3)


def screen(pool, universe, today):
    """One Finnhub call per US pool stock (Yahoo fundamentals for the rest),
    plus one Yahoo request for everyone's daily prices (volatility is computed
    from those, the vendor's figure is the fallback). Stores each result on the entry."""
    closes, _ = yahoo.get_closes_many(list(pool["stocks"]))
    rates: dict = {}
    for symbol, entry in pool["stocks"].items():
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

        result = {
            "date": today.isoformat(),
            "pe": pe,
            "five_yr_median_pe": median_pe,
            "dividend_yield_pct": yld,
            "payout_ratio_pct": payout,
            "market_cap_usd_m": mcap,
            "volatility_3m_pct": vol if vol is not None else m.get("3MonthADReturnStd"),
            "volatility_source": volatility.SOURCE
            if vol is not None
            else "Finnhub"
            if m.get("3MonthADReturnStd")
            else None,
            "beta": m.get("beta"),
            "score": None,
            "why": None,
            "passed": False,
            "filtered_out": None,
        }
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


# What counts as belonging to a sector or theme. `core` words are industry-level
# (authoritative, worth two ticks); `signals` are the business itself -- what the
# company drills, refines, mines or builds -- and count one tick each. A stock
# needs two ticks, so a utility called "NextEra Energy" is not an energy stock,
# while an oil producer whose industry line is vague still gets in on its words.
# A portfolio can add its own under `sector_keywords` in config/stock_universe.json.
SECTORS = {
    "energy": {
        # "energy" is safe as a core word: core words are matched against the
        # industry line only, never the company name.
        "core": ("energy", "oil", "gas", "refin", "pipeline", "coal", "drill", "petroleum", "fuel"),
        "signals": (
            "crude",
            "lng",
            "midstream",
            "upstream",
            "downstream",
            "oilfield",
            "exploration",
            "shale",
            "offshore",
            "tanker",
            "petro",
        ),  # fmt: skip
    },
    "renewables": {
        "core": ("renewable", "solar", "wind", "clean energy", "hydrogen"),
        "signals": ("photovoltaic", "turbine", "battery", "storage", "biofuel", "geothermal", "electrolys"),
    },
    "utilities": {"core": ("utilit", "electric power", "water"), "signals": ("grid", "transmission", "nuclear")},
    "financials": {
        "core": ("bank", "financ", "insur", "capital markets", "asset manage", "credit", "exchange"),
        "signals": ("lending", "mortgage", "brokerage", "payments", "wealth"),
    },
    "banks": {"core": ("bank",), "signals": ("lending", "deposits", "mortgage")},
    "insurance": {"core": ("insur",), "signals": ("underwrit", "reinsur", "annuit", "life", "casualty")},
    "healthcare": {
        "core": ("health", "pharma", "biotech", "medical", "life sciences"),
        "signals": ("drug", "therapeutic", "clinical", "diagnostic", "vaccine", "device", "hospital"),
    },
    "technology": {
        "core": ("tech", "software", "semiconduct", "hardware", "internet", "it services"),
        "signals": ("cloud", "data", "chip", "platform", "cyber", "saas", "ai"),
    },
    "semiconductors": {"core": ("semiconduct",), "signals": ("chip", "wafer", "foundry", "lithograph", "memory")},
    "industrials": {
        "core": ("industrial", "machin", "aerospace", "defen", "transport", "construct", "electrical equipment"),
        "signals": ("logistics", "rail", "freight", "engineering", "infrastructure", "automation"),
    },
    "defence": {
        "core": ("defen", "aerospace"),
        "signals": ("military", "missile", "radar", "armour", "armor", "naval"),
    },
    "consumer staples": {
        "core": ("staple", "food", "beverage", "household", "tobacco", "consumer defensive", "personal products"),
        "signals": ("grocery", "snack", "brewer", "distiller", "dairy", "cigarette"),
    },
    "consumer": {
        "core": ("consumer", "retail", "apparel", "restaur", "leisure", "auto", "hotel"),
        "signals": ("brand", "e-commerce", "footwear", "luxury", "travel", "gaming"),
    },
    "materials": {
        "core": ("material", "chemical", "metal", "mining", "steel", "paper", "packaging"),
        "signals": ("copper", "gold", "lithium", "aluminium", "aluminum", "cement", "fertiliz", "fertiliser"),
    },
    "mining": {"core": ("mining", "metal"), "signals": ("copper", "gold", "silver", "lithium", "nickel", "ore")},
    "real estate": {"core": ("real estate", "reit", "propert"), "signals": ("landlord", "leasing", "warehouse")},
    "telecoms": {
        "core": ("telecom", "communication", "media", "wireless"),
        "signals": ("broadband", "5g", "fibre", "fiber"),
    },
}

MIN_TICKS = 2  # what it takes to belong: one industry hit (worth 2), or two of its business words


def sector_words(universe: dict | None = None) -> dict:
    """The keyword table, with any additions from the portfolio's config."""
    extra = (universe or {}).get("sector_keywords") or {}
    merged = {k: {"core": tuple(v["core"]), "signals": tuple(v["signals"])} for k, v in SECTORS.items()}
    for name, words in extra.items():
        current = merged.get(name.lower(), {"core": (), "signals": ()})
        merged[name.lower()] = {"core": tuple({*current["core"], *(words.get("core") or ())}),
                                "signals": tuple({*current["signals"], *(words.get("signals") or ())})}  # fmt: skip
    return merged


def _entry_for(value: str, universe=None) -> dict:
    """The keyword entry for what was asked for; an unknown word is its own signal."""
    value = (value or "").strip().lower()
    table = sector_words(universe)
    if value in table:
        return table[value]
    for words in table.values():
        if any(value == w or (len(value) > 3 and value in w) for w in (*words["core"], *words["signals"])):
            return words
    return {"core": (), "signals": (value,) if value else ()}


def ticks(symbol: str, entry: dict, request: dict, universe=None) -> tuple[int, list]:
    """How many boxes this stock ticks for the request, and which words did it."""
    value = (request.get("value") or "").strip().lower()
    words = _entry_for(value, universe)
    screen = entry.get("screen") or {}
    industry = " ".join(str(x).lower() for x in (entry.get("industry"), screen.get("sector")) if x)
    name = str(entry.get("name") or "").lower()
    score, hit = 0, []
    if any(w in industry for w in words["core"]):
        score += 2  # the industry itself says so
        hit += [w for w in words["core"] if w in industry]
    elif any(w in name for w in words["core"]):
        score += 1  # only in the name: worth a tick, not a verdict
        hit += [w for w in words["core"] if w in name]
    for w in words["signals"]:
        if w in industry or w in name:
            score += 1
            hit.append(w)
    if request.get("kind") != "sector" and value and value in f"{industry} {name}":
        score += 2  # a theme he named himself, e.g. "lithium" in the company's name
        hit.append(value)
    return score, sorted(set(hit))


def matches_request(symbol: str, entry: dict, request: dict, universe=None) -> bool:
    """Does this stock answer the request? Tickers are exact; everything else
    has to tick MIN_TICKS boxes (see SECTORS), so a utility called "NextEra
    Energy" is not an energy stock while an oil producer with a vague industry
    line still qualifies."""
    value = (request.get("value") or "").strip()
    if request.get("kind") == "tickers" or "," in value:
        return symbol.upper() in {v.strip().upper() for v in value.split(",") if v.strip()}
    return ticks(symbol, entry, request, universe)[0] >= MIN_TICKS


def _requested(kind: str, requests: list) -> dict | None:
    return next((r for r in requests if r.get("kind") == kind and (r.get("value") or "").strip()), None)


def pick(pool, universe, exclude, today, requests=()):
    """The day's watchlist. With a focus request (sector, theme or tickers) the
    first `focus` slots go to the best-scoring stocks that answer it, then the
    best overall, then `rotation` picks the whole pool gets looked at through.
    Anything the client asked to avoid is never eligible. Returns [(symbol, slot)]."""
    requests = list(requests)
    wanted = universe["watchlist"]
    size = _requested("size", requests)
    limit = min(12, max(2, int(size["value"]))) if size and str(size["value"]).strip().isdigit() else None

    avoid = [r for r in requests if r.get("kind") == "avoid"]
    floors = _floors(requests)
    eligible = [
        s
        for s, e in pool["stocks"].items()
        if s not in exclude
        and (e.get("screen") or {}).get("passed")
        and not any(matches_request(s, e, r, universe) for r in avoid)
        and _clears_floors(e, floors)
    ]
    by_score = sorted(eligible, key=lambda s: -pool["stocks"][s]["screen"]["score"])

    focus_request = next((r for r in requests if r.get("kind") in FOCUS_KINDS), None)
    focus = []
    if focus_request:
        room = wanted.get("focus", 4)
        focus = [s for s in by_score if matches_request(s, pool["stocks"][s], focus_request, universe)][:room]

    others = wanted["top"] - len(focus) if focus else wanted["top"]
    top = [s for s in by_score if s not in focus][: max(0, others)]
    rest = [s for s in by_score if s not in focus and s not in top]
    rest.sort(key=lambda s: (pool["stocks"][s].get("shortlisted") or {}).get("last") or "")
    rotation = rest[: wanted["rotation"]]

    picks = [(s, "focus") for s in focus] + [(s, "top") for s in top] + [(s, "rotation") for s in rotation]
    if limit:
        picks = picks[:limit]
    for s, _ in picks:
        record = pool["stocks"][s].setdefault("shortlisted", {"times": 0, "first": today.isoformat()})
        record["times"] += 1
        record["last"] = today.isoformat()
    return picks


FLOOR_FIELDS = {"dividend_yield": "dividend_yield_pct", "market_cap_usd_m": "market_cap_usd_m", "pe": "pe"}


def _floors(requests: list) -> list:
    """'dividend_yield >= 4' -> [(screen field, 4.0)]; anything unparseable is ignored."""
    out = []
    for r in requests:
        if r.get("kind") != "metric_floor":
            continue
        match = re.match(r"\s*([a-z_]+)\s*>?=\s*([0-9.]+)\s*$", str(r.get("value", "")).lower())
        if match and match.group(1) in FLOOR_FIELDS:
            out.append((FLOOR_FIELDS[match.group(1)], float(match.group(2))))
    return out


def _clears_floors(entry: dict, floors: list) -> bool:
    screen = entry.get("screen") or {}
    return all((screen.get(field) or 0) >= floor for field, floor in floors)


def add_requested(pool, request: dict, today) -> list:
    """Puts tickers the client named into the pool (screened with everything else)."""
    added = []
    for symbol in {v.strip().upper() for v in str(request.get("value", "")).split(",") if v.strip()}:
        if symbol not in pool["stocks"]:
            pool["stocks"][symbol] = {"added": today.isoformat(), "via": "you asked for it"}
            added.append(symbol)
    return [f"added {', '.join(sorted(added))}: you asked for them"] if added else []


def expand_for(pool, universe, request: dict, today, needed: int = 4) -> list:
    """When too few pool stocks answer a focus request, adds peers of the ones
    that do (and of that sector's seed group) and screens just those. Notes what happened."""
    stocks = pool["stocks"]
    matching = [s for s, e in stocks.items() if matches_request(s, e, request, universe)]
    if len([s for s in matching if (stocks[s].get("screen") or {}).get("passed")]) >= needed:
        return []
    asked = _entry_for(request.get("value"), universe)
    seeds = [s for group, symbols in universe["seeds"].items()
             if any(w in group.lower() for w in (*asked["core"], *asked["signals"])) for s in symbols]  # fmt: skip
    added, notes = [], []
    for source in dict.fromkeys(matching[:6] + seeds[:6]):
        if not US_TICKER.fullmatch(source):
            continue
        peers, err = finnhub.get_peers(source, "industry")
        if err:
            continue
        for peer in peers or []:
            if peer not in stocks and US_TICKER.fullmatch(peer):
                stocks[peer] = {
                    "added": today.isoformat(),
                    "via": f"asked for {request.get('value')}: similar to {source}",
                }
                added.append(peer)
    if added:
        screen_subset(pool, universe, today, added)
        notes.append(f"added {len(added)} stocks to answer the request for {request.get('value')}")
    return notes


def summary(pool, picks, held, today):
    """What the dashboard shows: pool size, and one row per pool stock."""
    slot = dict(picks)
    rows = []
    for s, e in pool["stocks"].items():
        sc = e.get("screen") or {}
        shortlisted = e.get("shortlisted") or {}
        rows.append(
            {
                "symbol": s,
                "name": e.get("name"),
                "industry": e.get("industry"),
                "via": e.get("via"),
                "added": e.get("added"),
                **{
                    k: sc.get(k)
                    for k in (
                        "pe",
                        "five_yr_median_pe",
                        "dividend_yield_pct",
                        "payout_ratio_pct",
                        "market_cap_usd_m",
                        "volatility_3m_pct",
                        "beta",
                        "score",
                        "why",
                        "passed",
                        "filtered_out",
                    )
                },
                "times_on_watchlist": shortlisted.get("times", 0),
                "picked_today": slot.get(s),
                "first_time": shortlisted.get("first") == today.isoformat(),
                "held": s in held,
            }
        )
    rows.sort(key=lambda r: (not r["passed"], -(r["score"] or 0)))
    return {
        "refreshed": pool.get("refreshed"),
        "size": len(rows),
        "passed": sum(1 for r in rows if r["passed"]),
        "stocks": rows,
    }
