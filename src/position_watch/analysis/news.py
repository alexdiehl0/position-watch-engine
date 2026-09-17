"""News, gathered and filtered by plain code:

- company_news(): headlines that are actually about one company. Finnhub
  (US companies) and Google News (everyone, including Paris shares) are
  merged; a headline is kept only if it names the company, then duplicates of
  the same story from different outlets are dropped.
- market_news(): market-wide and geopolitical headlines -- central banks,
  energy, wars and sanctions, trade -- from Finnhub's market feed (Reuters,
  CNBC...) and Google News topic searches, minus stock-tip clickbait. Each
  gets an id (M1, M2...) so the day's reasoning can cite what it relied on.

Which of these matter for which holding is the model's judgement, not this
module's.
"""

import re
from datetime import datetime, timedelta, timezone

from position_watch.sources import finnhub, gnews

# Words dropped from the end of a company's legal name to get the name the press uses:
# "Philip Morris International Inc" -> "Philip Morris", "Credo Technology Group Holding Ltd" -> "Credo".
_SUFFIXES = {"inc", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "sa", "nv", "ag", "se", "spa",
             "group", "holding", "holdings", "international", "technology", "technologies", "the"}  # fmt: skip

MARKET_TOPICS = (
    "Federal Reserve OR ECB OR central bank interest rates",
    "oil prices OR OPEC OR energy markets",
    "sanctions OR tariffs OR export controls OR trade war",
    "geopolitics OR war markets stocks",
)

# Market-wide searches return every site on the web; for world news only these outlets are kept.
# (Company news stays open: a Paris insurer's news comes from specialist and local outlets.)
MARKET_OUTLETS = {
    "reuters", "bloomberg", "bloomberg.com", "financial times", "ft.com", "the wall street journal", "wsj",
    "ap news", "associated press", "cnbc", "bbc", "bbc.com", "the economist", "marketwatch", "barron's",
    "yahoo finance", "axios", "politico", "the guardian", "the new york times", "the washington post", "cnn",
    "npr", "nikkei asia", "south china morning post", "al jazeera", "le monde", "les echos", "fortune",
    "business insider", "financial post", "the globe and mail", "handelsblatt", "dw", "euronews", "semafor",
}  # fmt: skip

# Stock-tip and self-promotion headlines: no information about the world.
_NOISE = re.compile(
    r"cramer|motley fool|zacks|stocks? to buy|should you buy|buy now|best .{0,40}stocks|top \d+ .{0,30}stocks|"
    r"millionaire|price prediction|\bi (?:bought|sold)\b|dividend stocks? (?:for|to)|"
    # machine-written price-move blurbs: the day's move is already in the evidence
    r"heads into the open|(?:under|out)performs .{0,60}compared to competitors|"
    r"\bstock (?:rises|falls|gains|slips|drops|jumps|climbs|sinks)\b.{0,30}\d(?:\.\d+)? ?(?:%|percent)",
    re.I,
)
# Press-release wires and auto-generated content farms.
_SKIP_SOURCES = {"globenewswire", "pr newswire", "business wire", "accesswire", "newsfile", "ad-hoc-news.de",
                 "marketbeat", "defense world", "etf daily news", "ticker report", "the enterprise leader"}  # fmt: skip


def _useful(item: dict) -> bool:
    return (
        bool(item["headline"]) and not _NOISE.search(item["headline"]) and item["source"].lower() not in _SKIP_SOURCES
    )


def short_name(legal_name: str | None) -> str:
    words = re.sub(r"[.,]", " ", legal_name or "").split()
    while words and words[-1].lower() in _SUFFIXES:
        words.pop()
    if words and words[0].lower() == "the":
        words.pop(0)
    return " ".join(words)


def _norm(text: str) -> str:
    return re.sub(r"[-‐–]", " ", text or "").lower()


def mentions(text: str, name: str, symbol: str) -> bool:
    """Does `text` name the company (short name, or an unambiguous ticker)?"""
    if name and re.search(rf"\b{re.escape(_norm(name))}\b", _norm(text)):
        return True
    if len(symbol) >= 3 and symbol.isalpha() and re.search(rf"\b{re.escape(symbol)}\b", text or ""):
        return True  # "BABA", not "PM" (which is also a time of day)
    return bool(re.search(rf"[(:]{re.escape(symbol)}\)?\b", text or ""))  # "(PM)", "NYSE:PM"


def _words(headline: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", headline.lower()) if len(w) > 2}


def _dedupe(items: list) -> list:
    """Drops the same story told by another outlet (most words in common)."""
    kept = []
    for item in items:
        words = _words(item["headline"])
        if all(len(words & k) / max(1, len(words | k)) < 0.5 for k in (_words(x["headline"]) for x in kept)):
            kept.append(item)
    return kept


def _from_finnhub(n: dict) -> dict:
    ts, source = n.get("datetime"), n.get("source") or "Finnhub"
    headline = (n.get("headline") or "").strip().removesuffix(f" - {source}")  # "... - Reuters"
    return {"headline": headline, "source": source,
            "published": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None,
            "url": n.get("url"), "summary": (n.get("summary") or "").strip()}  # fmt: skip


def _finish(item: dict, feed: str) -> dict:
    return {**item, "date": (item.get("published") or "")[:10] or None, "via": feed}


def company_news(symbol: str, legal_name: str | None, limit: int = 5):
    """Up to `limit` recent headlines naming the company, newest first. Returns (items, error)."""
    name = short_name(legal_name)
    found, errors = [], []
    data, err = finnhub.get_company_news(symbol)
    if err:
        errors.append(err)
    found += [_finish(_from_finnhub(n), "Finnhub") for n in data or []]
    if name:
        data, err = gnews.search(f'"{name}"', days=4)
        if err:
            errors.append(err)
        found += [_finish(n, "Google News") for n in data or []]

    found = [n for n in found if _useful(n)]
    about = [n for n in found if mentions(n["headline"], name, symbol)]
    if len(about) < 3:  # a thin day: also accept stories that name it in their summary
        about += [n for n in found if n not in about and mentions(n["summary"], name, symbol)]
    about.sort(key=lambda n: n.get("published") or "", reverse=True)
    items = [{k: n[k] for k in ("headline", "source", "date", "url")} for n in _dedupe(about)[:limit]]
    return items, ("; ".join(errors) if errors and not found else None)


def market_news(limit: int = 40, hours: int = 36, now: datetime | None = None, extra_topics=()) -> dict:
    """Market and geopolitical headlines from the last `hours`, newest first, with ids M1..Mn.
    `extra_topics` are the client's own standing news requests: searched the same
    way, but kept whatever the outlet, since a niche topic lives in niche press."""
    now = now or datetime.now(timezone.utc)
    found, errors = [], []
    data, err = finnhub.get_general_news("general")
    if err:
        errors.append(err)
    found += [_finish(_from_finnhub(n), "Finnhub") for n in data or []]
    for topic in MARKET_TOPICS:
        data, err = gnews.search(topic, days=2)
        if err:
            errors.append(err)
        found += [_finish(n, "Google News") for n in data or [] if n["source"].lower() in MARKET_OUTLETS]
    for topic in dict.fromkeys(t for t in extra_topics if t):
        data, err = gnews.search(topic, days=3)
        if err:
            errors.append(err)
        found += [{**_finish(n, "Google News"), "asked_for": topic} for n in data or []]

    cutoff = (now - timedelta(hours=hours)).isoformat()
    fresh = [n for n in found if (n.get("published") or "") >= cutoff and _useful(n)]
    fresh.sort(key=lambda n: n["published"], reverse=True)
    headlines = [
        {
            "id": f"M{i}",
            "headline": n["headline"],
            "source": n["source"],
            "published": n["published"],
            "url": n.get("url"),
            "summary": n["summary"][:240],
            **({"asked_for": n["asked_for"]} if n.get("asked_for") else {}),
        }  # fmt: skip
        for i, n in enumerate(_dedupe(fresh)[:limit], 1)
    ]
    return {"headlines": headlines, "errors": errors}
