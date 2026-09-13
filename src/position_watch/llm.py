"""One Claude request, as cheaply as the answer allows.

Batch mode (the default) sends the request through the Message Batches API,
which bills every token at half price. It is the same model, prompt and
effort, so the reasoning is the same; only the delivery is slower (usually
minutes, at most 24 hours). If the batch hasn't finished within `max_wait`,
or the model declines the request, the batch is cancelled and the request is
re-sent as a normal streamed call at full price -- with server-side
`fallbacks`, which the Batches API doesn't accept -- so a scheduled run still
completes. Set POSITION_WATCH_CLAUDE_MODE=standard to always use the direct call.
"""

import csv
import json
import os
import time

import anthropic

from position_watch import settings

MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
PRICES = {"claude-opus-5": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0)}  # $ per million tokens in / out
BATCH_DISCOUNT = 0.5


class ClaudeError(RuntimeError):
    """The answer can't be used; the caller stops and reports why."""


def base_request(system: str, user: str, schema: dict, max_tokens: int = 64000) -> dict:
    return {
        "model": MODEL,
        "max_tokens": max_tokens,
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": schema}},
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }


def ask(request: dict, client=None, mode: str | None = None, max_wait: int = 2700, poll: int = 30, sleep=time.sleep):
    """Returns (message, batched)."""
    client = client or anthropic.Anthropic()
    if (mode or os.environ.get("POSITION_WATCH_CLAUDE_MODE", "batch")) == "batch":
        message = _batch(client, request, max_wait, poll, sleep)
        if message is not None and message.stop_reason != "refusal":
            return message, True
    with client.beta.messages.stream(**request) as stream:
        return stream.get_final_message(), False


def _batch(client, request, max_wait, poll, sleep):
    params = {k: v for k, v in request.items() if k not in ("betas", "fallbacks")}
    batch = client.messages.batches.create(requests=[{"custom_id": "request", "params": params}])
    waited = 0
    while batch.processing_status != "ended":
        if waited >= max_wait:
            client.messages.batches.cancel(batch.id)
            print(f"batch still running after {max_wait // 60} min; sending a direct request instead", flush=True)
            return None
        sleep(poll)
        waited += poll
        batch = client.messages.batches.retrieve(batch.id)
    for result in client.messages.batches.results(batch.id):
        if result.result.type == "succeeded":
            return result.result.message
        print(f"batch request {result.result.type}; sending a direct request instead", flush=True)
    return None


def json_answer(message) -> dict:
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raise ClaudeError(f"Claude declined the request ({getattr(details, 'category', None) or 'no category'})")
    if message.stop_reason == "max_tokens":
        raise ClaudeError("Claude's answer hit the token limit before finishing")
    text = "".join(b.text for b in message.content if b.type == "text")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClaudeError(f"answer was not valid JSON: {exc}") from exc


def usage(message, batched: bool) -> dict:
    u = message.usage
    price_in, price_out = PRICES.get(message.model, PRICES[MODEL])
    cost = (u.input_tokens * price_in + u.output_tokens * price_out) / 1_000_000
    return {
        "model": message.model,
        "batched": batched,
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "estimated_usd": round(cost * (BATCH_DISCOUNT if batched else 1), 4),
    }


def log_usage(day: str, record: dict):
    """Appends one row per Claude call to state/api_usage.csv: the running record of what the model costs."""
    path = settings.state_dir() / "api_usage.csv"
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="") as f:
        writer = csv.writer(f)
        if new:
            writer.writerow(["date", "task", "model", "batched", "input_tokens", "output_tokens", "estimated_usd"])
        writer.writerow([day, record.get("task", ""), record["model"], record["batched"],
                         record["input_tokens"], record["output_tokens"], record["estimated_usd"]])  # fmt: skip
