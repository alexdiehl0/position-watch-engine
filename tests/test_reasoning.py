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
    assert set(evidence) == {"holdings", "etfs", "candidates", "excluded"}
    assert "sources" not in evidence["holdings"]["USDS"]  # trimmed digest
    assert evidence["candidates"]["AAA"]["why_on_watchlist"]["slot"] == "top"


def test_decide_returns_validated_calls_and_usage(review_data):
    calls, usage = reasoning.decide(review_data, None, FEEDBACK, "2026-01-02", client=FakeClaude())
    assert calls["holdings"][0]["action"] == "hold"
    assert usage == {"model": "claude-opus-5", "batched": True, "input_tokens": 20000, "output_tokens": 8000,
                     "estimated_usd": 0.15}  # fmt: skip


def test_missing_symbol_is_rejected(review_data):
    calls = copy.deepcopy(CALLS)
    calls["holdings"] = calls["holdings"][:1]
    with pytest.raises(reasoning.ReasoningError, match="holdings"):
        reasoning.decide(review_data, None, FEEDBACK, "2026-01-02", client=FakeClaude(calls))


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
