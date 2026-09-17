"""What the client can ask for, and what each kind of request changes.

One entry per kind: the words he might use, what code does about it, and where
that happens. The model only sorts a message into these boxes --
`reasoning.classify_requests()` builds both its instructions and its answer
format from this table -- so an effect is never invented, and anything that
fits no box comes back as `other`, which is reported rather than lost.

Adding a kind is one entry here plus the code that reads it; the prompt, the
JSON schema and the dashboard's "what you can ask for" all follow.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Kind:
    name: str
    effect: str  # what code does about it, in one line
    value: str  # what `value` must contain
    examples: tuple
    once: bool = True  # can it be asked for a single run?


KINDS = (
    Kind(
        "sector",
        "most of the watchlist is filled from this sector",
        "the sector, e.g. 'energy'",
        ("focus on US-listed energy stocks", "more banks on the watchlist"),
    ),
    Kind(
        "theme",
        "most of the watchlist is filled with stocks matching this theme",
        "a few words, e.g. 'defence' or 'lithium'",
        ("anything in defence", "lithium plays"),
    ),
    Kind(
        "tickers",
        "these stocks are added to the pool and put on the watchlist",
        "tickers separated by commas, e.g. 'ENPH,FSLR'",
        ("look at ENPH and FSLR", "what about Shell?"),
    ),
    Kind(
        "avoid",
        "matching stocks are never suggested as new ideas (holdings are untouched)",
        "a sector, theme or ticker",
        ("no tobacco", "stop suggesting Chinese stocks"),
    ),
    Kind(
        "metric_floor",
        "candidates below the floor are left out",
        "'<metric> >= <number>', metric one of dividend_yield, market_cap_usd_m; e.g. 'dividend_yield >= 4'",
        ("only yields above 4%", "nothing smaller than $10bn"),
    ),
    Kind("size", "how many stocks the watchlist holds (2-12)", "a number", ("show me 10 ideas", "just 4 is plenty")),
    Kind(
        "news_topic",
        "an extra news search, and matching headlines lead the market briefing",
        "the topic, e.g. 'lithium supply'",
        ("keep an eye on lithium news", "watch the French elections"),
    ),
    Kind(
        "confirm_holdings",
        "the holdings change read from his screenshot is applied",
        "'yes'",
        ("confirmed", "yes, apply it", "that's right"),
        once=False,
    ),
    Kind(
        "add_sender",
        "that address counts as the sender's own from then on",
        "one email address",
        ("add finance@lionbulkers.com",),
        once=False,
    ),
    Kind(
        "other",
        "kept and shown, but nothing happens automatically yet",
        "his words, shortened",
        ("switch me to weekly emails",),
        once=False,
    ),
)

BY_NAME = {k.name: k for k in KINDS}
NAMES = tuple(k.name for k in KINDS)
FOCUS = ("sector", "theme", "tickers")  # kinds that steer which stocks are shortlisted
ACTIONS = ("add_sender", "confirm_holdings")  # carried out once, not kept as a standing request
SCOPES = ("standing", "once")


def prompt_section() -> str:
    """The list of kinds for the classifier's instructions."""
    lines = []
    for k in KINDS:
        examples = "; ".join(f'"{e}"' for e in k.examples)
        lines.append(
            f"- {k.name}: {k.effect}. value = {k.value}. Said as: {examples}." + ("" if k.once else " Never `once`.")
        )
    return "\n".join(lines)


def describe(request: dict) -> str:
    """One line for the email, report and dashboard."""
    kind, value = request.get("kind"), request.get("value") or ""
    label = {"sector": f"{value} stocks", "theme": value, "tickers": value, "avoid": f"avoid {value}",
             "metric_floor": value, "size": f"{value} ideas a day", "news_topic": f"news on {value}",
             "add_sender": f"accept mail from {value}", "confirm_holdings": "holdings update confirmed",
             "other": value}.get(kind, value)  # fmt: skip
    when = "just for one run" if request.get("scope") == "once" else f"since {request.get('asked_on', '?')}"
    return f"{label} ({when})"
