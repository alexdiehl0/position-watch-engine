"""Stand-ins for the Claude API and the mailbox, so the daily pipeline can be
tested with no network, no key and no cost."""

import json
from types import SimpleNamespace

CALLS = {
    "context_note": "Client asked to avoid tobacco.",
    "holdings": [
        {"symbol": "USDS", "action": "hold", "one_line": "Fairly valued.",
         "reasoning": "P/E 20.0 vs 5-yr avg 21.0 [Finnhub].", "watch_note": "check news"},
        {"symbol": "EURS", "action": "add", "one_line": "Cheap.", "reasoning": "Below its history.", "watch_note": ""},
    ],
    "etfs": [{"symbol": "ETFX", "action": "top_up", "one_line": "Below its average.",
              "reasoning": "10.0% vs cost.", "watch_note": ""}],
    "candidates": [{"symbol": "AAA", "action": "buy", "one_line": "Discount and yield.",
                    "reasoning": "P/E 10.0 vs median 14.0.", "watch_note": ""}],
    "market_briefing": [
        {"development": "Oil jumped after attacks on shipping lanes.", "impact": "Higher costs weigh on USDS.",
         "affects": ["USDS", "NOPE"], "headline_ids": ["M1", "M99"]},
        {"development": "Invented event.", "impact": "None.", "affects": ["USDS"], "headline_ids": ["M42"]},
    ],
    "notes_for_tomorrow": ["Re-check USDS headlines."],
    "feedback_applied": [{"message_id": "<m1@example.com>", "how": "Avoiding tobacco, as the client asked."}],
    "preference_changes": [{"message_id": "<m1@example.com>", "field": "avoid", "operation": "add", "value": "tobacco"}],
}  # fmt: skip

FEEDBACK = [{"message_id": "<m1@example.com>", "name": "Client", "role": "client", "date": "Fri, 2 Jan 2026",
             "subject": "Re: Portfolio Review 2026-01-01", "text": "Please avoid tobacco from now on."}]  # fmt: skip


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeClaude:
    """Records requests and returns a canned answer, through either the
    Batches API (batch_outcome: "succeeded", "refused" or "slow") or a
    direct streamed call."""

    def __init__(self, calls=None, stop_reason="end_turn", batch_outcome="succeeded"):
        self.requests, self.batch_requests, self.cancelled = [], [], []
        self.batch_outcome = batch_outcome
        self.message = self._message(calls, stop_reason)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
        self.messages = SimpleNamespace(
            batches=SimpleNamespace(
                create=self._create, retrieve=self._retrieve, results=self._results, cancel=self.cancelled.append
            )
        )

    @staticmethod
    def _message(calls, stop_reason):
        text = json.dumps(calls if calls is not None else CALLS)
        return SimpleNamespace(
            stop_reason=stop_reason,
            stop_details=None,
            model="claude-opus-5",
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=20000, output_tokens=8000, cache_read_input_tokens=0),
        )

    def _stream(self, **request):
        self.requests.append(request)
        return FakeStream(self.message)

    def _create(self, requests):
        self.batch_requests.extend(requests)
        return self._retrieve("batch_1")

    def _retrieve(self, batch_id):
        status = "in_progress" if self.batch_outcome == "slow" else "ended"
        return SimpleNamespace(id=batch_id, processing_status=status)

    def _results(self, batch_id):
        message = self.message
        if self.batch_outcome == "refused":
            message = self._message(None, "refusal")
        return [SimpleNamespace(custom_id="request", result=SimpleNamespace(type="succeeded", message=message))]
