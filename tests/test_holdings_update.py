"""Trades sent in: a broker export is applied, a screenshot waits for a yes."""

import base64
import csv
import json

from position_watch import settings
from position_watch.analysis import holdings_update as update
from tests.fakes import FakeClaude

EXPORT = ("Symbol,Description,Quantity,Average Cost,Currency\n"
          "USDS,US Stock Inc,150,11.00,USD\n"
          "EURS,Euro Stock SA,100,20.00,EUR\n"
          "NEW,Newly Bought Plc,\"1,200\",4.25,USD\n")  # fmt: skip

PICTURE_ANSWER = {
    "positions": [
        {"symbol": "USDS", "name": "US Stock Inc", "quantity": "150", "avg_price": "11.00", "currency": "USD"},
        {"symbol": "blurry", "name": "", "quantity": "", "avg_price": "", "currency": ""},
    ],
    "unreadable": "the third row was cut off",
}


def _message(filename, data, content_type="text/csv"):
    return [{"message_id": "<m1@example.com>", "name": "Client", "role": "client", "from": "client@example.com",
             "text": "here are my positions", "date": "Fri, 2 Jan 2026",
             "attachments": [{"filename": filename, "content_type": content_type, "data": data}]}]  # fmt: skip


def _holdings():
    with open(settings.holdings_csv(), newline="") as f:
        return {r["symbol"]: r for r in csv.DictReader(f)}


def test_a_broker_export_is_read_applied_and_explained(workspace):
    out = update.process(_message("positions.csv", EXPORT.encode()), "2026-01-02")

    rows = _holdings()
    assert rows["USDS"]["quantity_est"] == "150" and rows["NEW"]["quantity_est"] == "1200"  # "1,200" read properly
    assert rows["NEW"]["status"] == "open" and rows["EURS"]["currency"] == "EUR"
    assert "USDS: 100 → 150 shares (bought 50)" in out["applied"]
    assert "NEW: new position, 1200 shares" in out["applied"]
    assert out["pending"] == [] and out["notes"][0].startswith("Your holdings were updated from positions.csv")
    # the file it came from is kept exactly as it arrived
    assert (workspace / "data" / "raw" / "uploads" / "2026-01-02-positions.csv").read_text() == EXPORT


def test_a_position_missing_from_the_upload_is_left_alone(workspace):
    update.process(_message("positions.csv", b"Symbol,Quantity\nUSDS,150\n"), "2026-01-02")
    rows = _holdings()
    assert rows["EURS"]["quantity_est"] == "100"  # untouched, not closed
    assert rows["EURS"]["status"] == "open"


def test_an_unreadable_file_says_so_and_changes_nothing(workspace):
    before = _holdings()
    out = update.process(_message("notes.csv", b"one,two\n1,2\n"), "2026-01-02")
    assert _holdings() == before
    assert any("no symbol or quantity column" in note for note in out["notes"])


def test_a_screenshot_is_transcribed_and_waits_for_a_yes(workspace):
    picture = base64.b64decode("iVBORw0KGgo=")  # not a real image: the model is faked
    out = update.process(
        _message("positions.png", picture, "image/png"), "2026-01-02", client=FakeClaude(PICTURE_ANSWER)
    )

    assert _holdings()["USDS"]["quantity_est"] == "100"  # nothing applied yet
    assert out["pending"][:2] == ["USDS: 100 → 150 shares (bought 50)", "USDS: average cost 10 → 11"]
    assert out["pending"][2].startswith("not in the upload, left unchanged:")
    assert 'Reply "confirmed" to apply it.' in out["notes"][0]
    assert "could not read: the third row was cut off" in out["notes"]
    assert json.loads((workspace / "state" / "pending_holdings.json").read_text())["source"] == "positions.png"

    applied = update.apply_pending("2026-01-03")  # he replies "confirmed"
    assert applied[0] == "USDS: 100 → 150 shares (bought 50)"
    assert _holdings()["USDS"]["quantity_est"] == "150"
    assert update.load_pending() is None and update.apply_pending("2026-01-04") == []


def test_the_picture_request_carries_the_file_and_the_rules(workspace):
    client = FakeClaude(PICTURE_ANSWER)
    update.process(_message("positions.png", b"\x89PNG", "image/png"), "2026-01-02", client=client)
    blocks = client.requests[0]["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png"
    assert base64.b64decode(blocks[0]["source"]["data"]) == b"\x89PNG"
    assert "no prices from memory" in client.requests[0]["system"]


def test_messy_numbers_are_read_or_left_out():
    assert update._number("1,234.50") == 1234.5 and update._number("$1,200") == 1200.0
    assert update._number("—") is None and update._number(None) is None
