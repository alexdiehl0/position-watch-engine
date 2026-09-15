"""Google News search over its public RSS feed: free, no key.

Used for companies Finnhub's free plan doesn't cover (non-US listings such as
Paris shares) and for market-wide topic searches (rates, energy, sanctions...).
Headlines and links only -- no article text.

Same contract as the other sources: every function returns (data, error).
"""

import email.utils
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

URL = "https://news.google.com/rss/search"


def search(query: str, days: int = 3):
    """Headlines matching `query` from the last `days` days, newest as returned by Google."""
    params = {"q": f"{query} when:{days}d", "hl": "en-US", "gl": "US", "ceid": "US:en"}
    request = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(params)}", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as r:
            root = ET.fromstring(r.read())
    except Exception as exc:
        return None, f"Google News search {query!r} failed: {exc}"

    items = []
    for item in root.iter("item"):
        title, source = (item.findtext("title") or "").strip(), (item.findtext("source") or "").strip()
        if source and title.endswith(f" - {source}"):  # Google appends the publisher to every title
            title = title[: -len(source) - 3].strip()
        try:
            published = email.utils.parsedate_to_datetime(item.findtext("pubDate")).isoformat()
        except (TypeError, ValueError):
            published = None
        items.append({"headline": title, "source": source or "Google News", "published": published,
                      "url": item.findtext("link"), "summary": ""})  # fmt: skip
    return items, None
