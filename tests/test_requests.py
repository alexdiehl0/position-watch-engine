"""What the client asks for: classified once, stored, and honoured by the watchlist."""

import json
from datetime import date

import pytest

from position_watch import client_requests, people, reasoning
from position_watch.analysis import news, pool
from position_watch.documents import render as documents
from tests.fakes import FakeClaude

TODAY = date(2026, 1, 2)
MSG = [{"message_id": "<m1@example.com>", "name": "Client", "role": "client", "from": "client@example.com",
        "date": "Fri, 2 Jan 2026", "subject": "Portfolio feedback", "text": "Focus on US energy stocks tomorrow."}]  # fmt: skip


def _pool():
    def stock(industry, score, yld=3.0, cap=50_000):
        return {"industry": industry, "name": f"{industry} Co",
                "screen": {"passed": True, "score": score, "dividend_yield_pct": yld, "market_cap_usd_m": cap}}  # fmt: skip

    return {"stocks": {
        "XOM": stock("Oil & Gas", 0.9), "CVX": stock("Oil & Gas", 0.8), "COP": stock("Energy", 0.7),
        "VLO": stock("Oil & Gas Refining", 0.6), "SLB": stock("Energy Equipment", 0.5, yld=1.0),
        "KO": stock("Beverages", 1.5), "PM": stock("Tobacco", 1.4), "JNJ": stock("Pharmaceuticals", 1.3),
        "AAPL": stock("Technology", 0.2), "SO": stock("Utilities", 1.2),
    }}  # fmt: skip


UNIVERSE = {"watchlist": {"top": 6, "rotation": 2, "focus": 4}, "seeds": {"Energy": ["XOM"]}, "pool_cap": 150}


def _request(kind, value, scope="standing"):
    return {"kind": kind, "value": value, "scope": scope, "asked_on": "2026-01-02"}


# ---- classification ------------------------------------------------------


def test_classify_keeps_only_real_messages_and_known_kinds():
    answer = {"requests": [
        {"message_id": "<m1@example.com>", "kind": "sector", "value": "energy", "scope": "once", "operation": "set",
         "text": "Focus on US energy stocks tomorrow."},
        {"message_id": "<nope@example.com>", "kind": "sector", "value": "banks", "scope": "standing", "operation": "set", "text": ""},
        {"message_id": "<m1@example.com>", "kind": "invented", "value": "x", "scope": "standing", "operation": "set", "text": ""},
    ]}  # fmt: skip
    changes, usage = reasoning.classify_requests(MSG, "2026-01-02", client=FakeClaude(answer))
    assert [c["kind"] for c in changes] == ["sector"] and changes[0]["scope"] == "once"
    assert usage["model"] == "claude-opus-5"
    assert reasoning.classify_requests([], "2026-01-02", client=FakeClaude(answer)) == ([], {})


def test_the_classifier_is_told_every_kind_and_its_effect():
    client = FakeClaude({"requests": []})
    reasoning.classify_requests(MSG, "2026-01-02", client=client)
    system = client.requests[0]["system"]
    for kind in client_requests.NAMES:
        assert kind in system
    assert "holdings are untouched" in system and "already passed is spent" in system


# ---- storage -------------------------------------------------------------


def test_a_request_is_stored_replaced_and_retired(workspace):
    senders = {"<m1@example.com>": "client@example.com"}
    lines = people.apply_requests(
        [{"message_id": "<m1@example.com>", "kind": "sector", "value": "energy", "scope": "once", "text": "..."}],
        senders,
        "2026-01-02",
    )
    assert lines == ["sector: energy (one run)"]
    assert [r["value"] for r in people.active_requests()] == ["energy"]

    people.apply_requests(  # the newest of a kind wins
        [{"message_id": "<m1@example.com>", "kind": "sector", "value": "healthcare", "operation": "set"}],
        senders,
        "2026-01-03",
    )
    assert [(r["kind"], r["value"], r["scope"]) for r in people.active_requests()] == [
        ("sector", "healthcare", "standing")
    ]

    people.apply_requests(
        [{"message_id": "<m1@example.com>", "kind": "sector", "value": "", "operation": "clear"}], senders, "2026-01-04"
    )
    assert people.active_requests() == []
    history = json.loads((workspace / "config" / "people.json").read_text())
    assert "cleared sector" in history["people"][1]["preferences"]["history"][-1]["change"]


def test_one_run_requests_are_consumed(workspace):
    people.apply_requests(
        [{"message_id": "<m1@example.com>", "kind": "sector", "value": "energy", "scope": "once"}],
        {"<m1@example.com>": "client@example.com"},
        "2026-01-02",
    )
    assert people.consume_once_requests("2026-01-02")[0]["value"] == "energy"
    assert people.active_requests() == []


def test_only_a_trusted_sender_can_widen_who_is_trusted(workspace):
    change = [{"message_id": "<m1@example.com>", "kind": "add_sender", "value": "work@example.com"}]
    assert people.apply_requests(change, {"<m1@example.com>": "stranger@example.com"}, "2026-01-02") == []
    assert people.who_sent("work@example.com") is None

    lines = people.apply_requests(change, {"<m1@example.com>": "client@example.com"}, "2026-01-02")
    assert lines == ["accept mail from work@example.com as Client"]
    assert people.who_sent("work@example.com") == {"name": "Client", "role": "client"}


# ---- the watchlist -------------------------------------------------------


def test_a_sector_request_takes_four_slots_and_keeps_the_rest_broad():
    picks = pool.pick(_pool(), UNIVERSE, exclude=[], today=TODAY, requests=[_request("sector", "energy")])
    focus = [s for s, slot in picks if slot == "focus"]
    assert focus == ["XOM", "CVX", "COP", "VLO"]  # best-scoring energy names
    assert [s for s, slot in picks if slot == "top"] == ["KO", "PM"]  # best overall still shown
    assert [slot for _, slot in picks].count("rotation") == 2


def test_without_a_request_the_watchlist_is_the_old_shape():
    picks = pool.pick(_pool(), UNIVERSE, exclude=[], today=TODAY)
    assert [slot for _, slot in picks] == ["top"] * 6 + ["rotation"] * 2


def test_avoided_stocks_never_reach_the_watchlist():
    picks = pool.pick(_pool(), UNIVERSE, exclude=[], today=TODAY, requests=[_request("avoid", "tobacco")])
    assert "PM" not in [s for s, _ in picks]


def test_a_metric_floor_and_a_size_are_respected():
    picks = pool.pick(_pool(), UNIVERSE, exclude=[], today=TODAY,
                      requests=[_request("metric_floor", "dividend_yield >= 2"), _request("size", "3")])  # fmt: skip
    assert len(picks) == 3 and "SLB" not in [s for s, _ in picks]  # SLB yields 1%


def test_tickers_asked_for_join_the_pool_and_the_watchlist():
    p = _pool()
    assert pool.add_requested(p, _request("tickers", "ENPH, FSLR"), TODAY) == ["added ENPH, FSLR: you asked for them"]
    p["stocks"]["ENPH"]["screen"] = {"passed": True, "score": 0.1}
    p["stocks"]["FSLR"]["screen"] = {"passed": True, "score": 0.0}
    picks = pool.pick(p, UNIVERSE, exclude=[], today=TODAY, requests=[_request("tickers", "ENPH,FSLR")])
    assert [s for s, slot in picks if slot == "focus"] == ["ENPH", "FSLR"]


def test_a_thin_sector_pulls_in_peers(monkeypatch):
    p = {"stocks": {"XOM": {"industry": "Oil & Gas", "screen": {"passed": True, "score": 0.9}}}}
    monkeypatch.setattr(pool.finnhub, "get_peers", lambda s, grouping: (["XOM", "CVX", "COP"], None))
    monkeypatch.setattr(pool, "screen_subset", lambda *a: None)
    notes = pool.expand_for(p, UNIVERSE, _request("sector", "energy"), TODAY)
    assert notes == ["added 2 stocks to answer the request for energy"]
    assert p["stocks"]["CVX"]["via"] == "asked for energy: similar to XOM"


@pytest.mark.parametrize("value,symbol,expected", [
    ("energy", "VLO", True), ("energy", "KO", False), ("banks", "XOM", False),
    ("defence", "AAPL", False), ("technology", "AAPL", True),
])  # fmt: skip
def test_matching_reaches_the_whole_sector(value, symbol, expected):
    entry = _pool()["stocks"][symbol]
    assert pool.matches_request(symbol, entry, _request("sector", value)) is expected


# ---- news and documents --------------------------------------------------


def test_a_news_request_is_searched_and_kept_whatever_the_outlet(monkeypatch):
    monkeypatch.setattr(news.finnhub, "get_general_news", lambda category: ([], None))
    searched = []

    def search(query, days):
        searched.append(query)
        item = {"headline": f"About {query}", "source": "Mining Weekly", "summary": "",
                "url": "https://example.com/x", "published": "2026-01-02T11:00:00+00:00"}  # fmt: skip
        return ([item] if query == "lithium supply" else []), None

    monkeypatch.setattr(news.gnews, "search", search)
    out = news.market_news(now=__import__("datetime").datetime(2026, 1, 2, 12, tzinfo=__import__("datetime").timezone.utc),
                           extra_topics=["lithium supply"])  # fmt: skip
    assert "lithium supply" in searched
    assert out["headlines"][0]["asked_for"] == "lithium supply"  # a niche topic lives in niche press


def test_the_email_and_report_say_what_he_asked_for():
    msg = documents.email("2026-01-02", {"holdings": {}, "etfs": {}, "candidates": {}}, {"holdings": [], "etfs": [], "candidates": []},
                          {"value_usd": 1.0, "pnl_usd": 0.0, "pnl_pct": 0.0, "dividends_usd": 0.0}, None, None, False,
                          requests=[_request("sector", "energy"), _request("news_topic", "lithium")])  # fmt: skip
    assert "YOU ASKED FOR\nenergy stocks (since 2026-01-02) · news on lithium (since 2026-01-02)" in msg["text"]
    assert "YOU ASKED FOR" in msg["html"] and "energy stocks (since 2026-01-02)" in msg["html"]
