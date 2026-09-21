import copy
import json

import pytest

from position_watch import reasoning
from tests.fakes import CALLS, FEEDBACK, FakeClaude


def test_request_uses_opus_5_with_fallbacks_structured_output_and_fenced_feedback(review_data):
    request = reasoning.build_request(review_data, {"goal": "Income", "history": [1]}, None, FEEDBACK, "2026-01-02")

    assert request["model"] == "claude-opus-5"
    assert request["fallbacks"] == "default" and request["betas"] == ["server-side-fallback-2026-07-01"]
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"]["format"]["type"] == "json_schema"
    user = request["messages"][0]["content"]
    assert '<message id="<m1@example.com>" from="Client (client)"' in user
    assert "Please avoid tobacco" in user
    assert '"history"' not in user  # preference history isn't sent
    evidence = json.loads(user.split("<evidence>\n")[1].split("\n</evidence>")[0])
    assert set(evidence) == {"markets", "holdings", "etfs", "candidates", "excluded"}
    assert evidence["markets"]["headlines"][0] == {"id": "M1", "headline": "Oil jumps as shipping lanes are attacked",
                                                   "source": "Reuters", "published": "2026-01-02T03:00",
                                                   "summary": "Brent rose 3%."}  # fmt: skip
    assert "url" not in evidence["markets"]["headlines"][1] and "summary" not in evidence["markets"]["headlines"][1]
    assert "sources" not in evidence["holdings"]["USDS"]  # trimmed digest
    assert evidence["candidates"]["AAA"]["why_on_watchlist"]["slot"] == "top"


def test_decide_returns_validated_calls_and_usage(review_data):
    calls, usage, notes = reasoning.decide(review_data, None, FEEDBACK, "2026-01-02", client=FakeClaude())
    assert calls["holdings"][0]["action"] == "hold"
    assert usage == {"model": "claude-opus-5", "batched": True, "input_tokens": 20000, "output_tokens": 8000,
                     "estimated_usd": 0.15}  # fmt: skip
    assert notes == []


def test_missing_symbol_is_rejected(review_data):
    calls = copy.deepcopy(CALLS)
    calls["holdings"] = calls["holdings"][:1]
    with pytest.raises(reasoning.ReasoningError, match="holdings"):
        reasoning.decide(review_data, None, FEEDBACK, "2026-01-02", client=FakeClaude(calls))


def test_a_symbol_answered_twice_keeps_the_first_call_and_says_so(review_data):
    # The 19 and 21 Sep 2026 failures: Claude repeated one array entry, and the
    # whole run was thrown away over it.
    calls = copy.deepcopy(CALLS)
    first = calls["holdings"][0]
    calls["holdings"].append({**first, "one_line": "a second, duplicate call for the same symbol"})

    result, _, notes = reasoning.decide(review_data, None, FEEDBACK, "2026-01-02", client=FakeClaude(calls))

    assert {c["symbol"] for c in result["holdings"]} == set(review_data["holdings"])
    assert len(result["holdings"]) == len(review_data["holdings"])
    assert result["holdings"][0]["one_line"] == first["one_line"]  # the first call, not the repeat
    assert len(notes) == 1 and first["symbol"] in notes[0]


def test_a_duplicate_hiding_a_missing_symbol_still_stops_the_run(review_data):
    calls = copy.deepcopy(CALLS)
    calls["holdings"][-1] = {**calls["holdings"][0], "one_line": "duplicate in place of the last symbol"}
    with pytest.raises(reasoning.ReasoningError, match="holdings"):
        reasoning.decide(review_data, None, FEEDBACK, "2026-01-02", client=FakeClaude(calls))


def test_schema_pins_each_section_to_the_days_symbols(review_data):
    section = reasoning.schema(review_data)["properties"]["etfs"]
    assert section["items"]["properties"]["symbol"]["enum"] == sorted(review_data["etfs"])
    # Not minItems/maxItems: structured output rejects any minItems above 1, so
    # a schema carrying them fails the request outright (400). The count is
    # checked in validate() instead.
    assert "minItems" not in section and "maxItems" not in section


def test_preference_change_must_cite_real_feedback(review_data):
    calls = copy.deepcopy(CALLS)
    calls["preference_changes"][0]["message_id"] = "<invented@example.com>"
    with pytest.raises(reasoning.ReasoningError, match="unknown feedback"):
        reasoning.decide(review_data, None, FEEDBACK, "2026-01-02", client=FakeClaude(calls))


def test_refusal_stops_the_run(review_data):
    with pytest.raises(reasoning.ReasoningError, match="declined"):
        reasoning.decide(review_data, None, [], "2026-01-02", client=FakeClaude(stop_reason="refusal"))


def test_preference_changes_are_recorded_with_their_source(workspace):
    lines = reasoning.apply_preference_changes(CALLS, FEEDBACK)
    saved = json.loads((workspace / "config" / "people.json").read_text())
    prefs = next(p for p in saved["people"] if p["role"] == "client")["preferences"]
    assert prefs["avoid"] == ["tobacco"]
    assert prefs["history"][-1]["source"] == "feedback <m1@example.com> from Client"
    assert lines == ["avoid + tobacco"]


def test_market_briefing_keeps_only_real_headlines_and_symbols(review_data):
    calls, _, _ = reasoning.decide(review_data, None, FEEDBACK, "2026-01-02", client=FakeClaude())
    assert calls["market_briefing"] == [
        {"development": "Oil jumped after attacks on shipping lanes.", "impact": "Higher costs weigh on USDS.",
         "affects": ["USDS"], "headline_ids": ["M1"]}  # invented id, unknown symbol and invented event dropped
    ]  # fmt: skip


def test_a_briefing_item_names_at_most_three_symbols(review_data):
    calls = copy.deepcopy(CALLS)
    symbols = list(review_data["holdings"]) + list(review_data["etfs"]) + list(review_data["candidates"])
    calls["market_briefing"] = [
        {"development": "Rates moved.", "impact": "Broad.", "affects": symbols, "headline_ids": ["M1"]}
    ]
    result, _, _ = reasoning.decide(review_data, None, FEEDBACK, "2026-01-02", client=FakeClaude(calls))
    assert len(result["market_briefing"][0]["affects"]) == 3


def test_a_message_id_quoted_without_its_angle_brackets_still_matches():
    # A Message-ID is <abc@host> and the brackets are part of it, but a model
    # quoting one back sometimes drops them. Matched exactly, the client's
    # request was filtered out and nothing said a message had been ignored.
    known = {"<m1@example.com>"}
    assert reasoning.resolve_message_id("m1@example.com", known) == "<m1@example.com>"
    assert reasoning.resolve_message_id("<m1@example.com>", known) == "<m1@example.com>"
    assert reasoning.resolve_message_id(" m1@example.com ", known) == "<m1@example.com>"
    assert reasoning.resolve_message_id("someone-else@example.com", known) is None
    assert reasoning.resolve_message_id(None, known) is None


def test_a_preference_change_citing_a_bare_id_is_kept_and_renormalised(review_data):
    calls = copy.deepcopy(CALLS)
    calls["preference_changes"][0]["message_id"] = "m1@example.com"  # brackets dropped
    result, _, _ = reasoning.decide(review_data, None, FEEDBACK, "2026-01-02", client=FakeClaude(calls))
    assert result["preference_changes"][0]["message_id"] == "<m1@example.com>"
