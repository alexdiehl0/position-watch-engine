"""What counts as belonging to a sector or theme.

`core` words are industry-level -- authoritative, worth two ticks; `signals` are
the business itself, what the company drills, refines, mines or builds, and
count one tick each. A stock needs MIN_TICKS, so a utility called "NextEra
Energy" is not an energy stock, while an oil producer whose industry line is
vague still gets in on its words. A portfolio adds its own under
`sector_keywords` in config/stock_universe.json.
"""

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
