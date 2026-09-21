from position_watch import llm
from tests.fakes import FakeClaude


def _request():
    return llm.base_request("rules", "evidence", {"type": "object"})


def test_batch_is_the_default_and_bills_half():
    fake = FakeClaude()
    message, batched = llm.ask(_request(), client=fake)
    assert batched and not fake.requests
    params = fake.batch_requests[0]["params"]
    assert "fallbacks" not in params and "betas" not in params  # not accepted by the Batches API
    assert params["model"] == "claude-opus-5" and params["output_config"]["effort"] == "high"
    assert llm.usage(message, batched)["estimated_usd"] == 0.15  # (20k x $5 + 8k x $25) / 1M, halved


def test_refused_batch_retries_directly_with_fallbacks():
    fake = FakeClaude(batch_outcome="refused")
    message, batched = llm.ask(_request(), client=fake)
    assert not batched and fake.requests[0]["fallbacks"] == "default"
    assert llm.usage(message, batched)["estimated_usd"] == 0.3


def test_slow_batch_is_cancelled_then_sent_directly():
    fake = FakeClaude(batch_outcome="slow")
    message, batched = llm.ask(_request(), client=fake, max_wait=60, poll=30, sleep=lambda s: None)
    assert fake.cancelled == ["batch_1"] and not batched and fake.requests


def test_standard_mode_skips_the_batch():
    fake = FakeClaude()
    _, batched = llm.ask(_request(), client=fake, mode="standard")
    assert not batched and not fake.batch_requests


def test_the_judgement_is_opus_and_the_side_jobs_are_not():
    assert llm.base_request("rules", "evidence", {})["model"] == "claude-opus-5"
    small = llm.base_request("rules", "evidence", {}, model=llm.SMALL_MODEL)
    assert small["model"] == llm.SMALL_MODEL
    assert llm.PRICES[llm.SMALL_MODEL] < llm.PRICES[llm.MODEL]


def test_a_small_model_call_is_priced_as_one():
    fake = FakeClaude()
    message, batched = llm.ask(llm.base_request("r", "e", {}, model=llm.SMALL_MODEL), client=fake)
    message.model = llm.SMALL_MODEL
    assert llm.usage(message, batched)["estimated_usd"] == 0.03  # (20k x $1 + 8k x $5) / 1M, halved


def test_the_small_model_gets_no_adaptive_thinking_or_effort():
    # Haiku refuses both outright ("adaptive thinking is not supported on this
    # model"), so a request carrying them fails with a 400 before it starts.
    small = llm.base_request("rules", "evidence", {}, model=llm.SMALL_MODEL)
    assert "thinking" not in small
    assert "effort" not in small["output_config"]
    assert small["output_config"]["format"]["type"] == "json_schema"

    big = llm.base_request("rules", "evidence", {})
    assert big["thinking"] == {"type": "adaptive"}
    assert big["output_config"]["effort"] == "high"
