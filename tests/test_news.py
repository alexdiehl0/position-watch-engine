from datetime import datetime, timezone

from position_watch.analysis import markets, news
from position_watch.sources import gnews

NOW = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)


def _item(headline, source="Reuters", hours_ago=1, summary=""):
    return {"headline": headline, "source": source, "summary": summary, "url": "https://example.com/x",
            "published": datetime.fromtimestamp(NOW.timestamp() - hours_ago * 3600, tz=timezone.utc).isoformat()}  # fmt: skip


def test_short_names_are_what_the_press_uses():
    assert news.short_name("Philip Morris International Inc") == "Philip Morris"
    assert news.short_name("Credo Technology Group Holding Ltd") == "Credo"
    assert news.short_name("AXA SA") == "AXA"
    assert news.short_name("The Coca-Cola Company") == "Coca-Cola"


def test_mentions_needs_the_company_not_a_lookalike():
    assert news.mentions("Philip Morris lifts its dividend", "Philip Morris", "PM")
    assert news.mentions("Alibaba (BABA) rallies", "", "BABA")
    assert news.mentions("Tobacco names slip, NYSE:PM included", "Philip Morris", "PM")
    assert not news.mentions("Markets close at 4 PM as tech slides", "Philip Morris", "PM")
    assert news.mentions("Coca Cola raises prices", "Coca-Cola", "KO")  # hyphen or not


def test_company_news_keeps_what_names_the_company_and_drops_repeats(monkeypatch):
    finnhub_items = [
        {"headline": "Apple launches a foldable phone", "source": "Yahoo", "datetime": 1767340000, "summary": ""},
        {"headline": "Philip Morris CEO upbeat on Zyn", "source": "CNBC", "datetime": 1767330000, "summary": ""},
    ]
    google_items = [
        _item("Philip Morris CEO upbeat on Zyn demand", "Bloomberg.com", 3),  # the same story again
        _item("Philip Morris stock heads into the open after a 2.0 percent rise", "ad-hoc-news.de", 2),
        _item("Philip Morris expands its Makati hub", "Manila Bulletin", 5),
    ]
    monkeypatch.setattr(news.finnhub, "get_company_news", lambda s: (finnhub_items, None))
    monkeypatch.setattr(
        news.gnews, "search", lambda q, days: (google_items, None) if q == '"Philip Morris"' else ([], None)
    )

    items, err = news.company_news("PM", "Philip Morris International Inc")
    assert err is None
    # newest first; the older copy of the Zyn story, the phone story and the auto-generated blurb are gone
    assert [n["headline"] for n in items] == [
        "Philip Morris CEO upbeat on Zyn demand",
        "Philip Morris expands its Makati hub",
    ]


def test_market_news_is_fresh_reputable_and_numbered(monkeypatch):
    finnhub_items = [
        {"headline": "Oil jumps as shipping lanes are attacked - Reuters", "source": "Reuters",
         "datetime": int(NOW.timestamp()) - 3600, "summary": "Brent rose 3%."},
        {"headline": "Jim Cramer's top 5 stocks to buy now", "source": "CNBC", "datetime": int(NOW.timestamp()) - 3600},
        {"headline": "Last week's rate decision, revisited", "source": "Reuters", "datetime": int(NOW.timestamp()) - 72 * 3600},
    ]  # fmt: skip
    google_items = [_item("Central bank signals a rate rise", "WSJ", 2), _item("Rates to rise, says blog", "Some Blog", 2),
                    _item("Oil jumps as shipping lanes attacked", "AP News", 4)]  # fmt: skip
    monkeypatch.setattr(news.finnhub, "get_general_news", lambda category: (finnhub_items, None))
    monkeypatch.setattr(news.gnews, "search", lambda q, days: (google_items, None))

    out = news.market_news(now=NOW)
    assert [(h["id"], h["headline"], h["source"]) for h in out["headlines"]] == [
        ("M1", "Oil jumps as shipping lanes are attacked", "Reuters"),
        ("M2", "Central bank signals a rate rise", "WSJ"),
    ]  # clickbait, a 3-day-old story, an unknown blog and the repeated oil story are gone


def test_snapshot_changes_in_percent_and_basis_points(monkeypatch):
    days = [f"2026-01-0{i}" for i in range(1, 8)]
    history = {"^GSPC": list(zip(days, [100, 101, 102, 103, 104, 105, 110], strict=True)),
               "^TNX": list(zip(days, [4.00, 4.02, 4.05, 4.1, 4.1, 4.2, 4.25], strict=True))}  # fmt: skip
    monkeypatch.setattr(markets.yahoo, "get_history_many", lambda symbols, period: (history, None))
    rows, err = markets.snapshot()
    sp, tnx = rows
    assert sp == {"name": "S&P 500", "level": 110, "unit": "index", "change_1d": 4.76, "change_5d": 8.91,
                  "as_of": "2026-01-07", "source": "yfinance"}  # fmt: skip
    assert (tnx["change_1d"], tnx["change_5d"]) == (5, 23)  # basis points
    assert "Nasdaq 100" in err  # missing series are named, not invented


def test_google_news_feed_is_parsed(monkeypatch):
    rss = b"""<rss><channel><item><title>Axa raises targets - Reuters</title><link>https://news.google.com/a</link>
      <pubDate>Fri, 02 Jan 2026 07:00:00 GMT</pubDate><source url="https://reuters.com">Reuters</source></item>
      </channel></rss>"""

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return rss

    monkeypatch.setattr(gnews.urllib.request, "urlopen", lambda req, timeout: Response())
    items, err = gnews.search('"AXA"')
    assert err is None
    assert items == [{"headline": "Axa raises targets", "source": "Reuters", "published": "2026-01-02T07:00:00+00:00",
                      "url": "https://news.google.com/a", "summary": ""}]  # fmt: skip
