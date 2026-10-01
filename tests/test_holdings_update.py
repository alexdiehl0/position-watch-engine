"""Trades sent in: a broker export is applied; a screenshot's trades are applied once
every one checks out; a positions screenshot waits for a yes. Every outcome is logged
for the email, report and dashboard."""

import base64
import csv
import io
import json
import zipfile
from datetime import date

from position_watch import settings
from position_watch.analysis import holdings_update as update
from tests.fakes import FakeClaude

EXPORT = ("Symbol,Description,Quantity,Average Cost,Currency\n"
          "USDS,US Stock Inc,150,11.00,USD\n"
          "EURS,Euro Stock SA,100,20.00,EUR\n"
          "NEW,Newly Bought Plc,\"1,200\",4.25,USD\n")  # fmt: skip

POSITIONS_ANSWER = {
    "view": "positions",
    "rows_stated": "",
    "trades": [],
    "positions": [
        {"symbol": "USDS", "name": "US Stock Inc", "quantity": "150", "avg_price": "11.00", "currency": "USD"},
        {"symbol": "blurry", "name": "", "quantity": "", "avg_price": "", "currency": ""},
    ],
    "unreadable": "the third row was cut off",
}


def _trade(symbol, side, when, qty, price, total, currency="USD", name="", exchange="NYSE"):
    return {"symbol": symbol, "name": name, "exchange": exchange, "side": side, "date": when, "qty": qty,
            "price": price, "price_currency": currency, "commission": "0", "commission_currency": currency,
            "total": total, "total_currency": currency}  # fmt: skip


# A broker's Transactions page as the model reads it (synthetic; the shapes that matter:
# a K-abbreviated total, a thousands separator, a date like "Sep 21, 2026").
TRANSACTIONS_ANSWER = {
    "view": "transactions",
    "rows_stated": "3 transactions",
    "trades": [
        _trade("GOLD", "Buy", "Sep 25, 2026", "1", "2,431.50", "2.43 K", exchange="OTC"),
        _trade("NEWCO", "Buy", "Sep 21, 2026", "250", "48.20", "12.05 K", name="New Co"),
    ],
    "positions": [],
    "unreadable": "",
}
TODAY = date(2026, 10, 2)


def _message(filename, data, content_type="text/csv", subject="Holdings update"):
    return [{"message_id": "<m1@example.com>", "name": "Client", "role": "client", "from": "client@example.com",
             "text": "", "date": "Fri, 2 Oct 2026 08:00:00 +0000", "subject": subject,
             "attachments": [{"filename": filename, "content_type": content_type, "data": data}]}]  # fmt: skip


def _holdings():
    with open(settings.holdings_csv(), newline="") as f:
        return {r["symbol"]: r for r in csv.DictReader(f)}


def _transactions(rows):
    path = settings.transactions_csv()
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=update.TRANSACTION_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _on_file():
    return update.load_transactions()


def _picture(answer, subject="Holdings update", data=b"\x89PNG-1"):
    return update.process(_message("pasted-image1.png", data, "image/png", subject), "2026-10-02",
                          client=FakeClaude(answer), today=TODAY)  # fmt: skip


# ---- A screenshot of the Transactions page ---------------------------------


def test_trades_read_from_a_screenshot_are_in_the_holdings_the_same_morning(workspace):
    _transactions([{**_trade("GOLD", "Buy", "2026-02-04", "1", "500", "500"), "name": "Gold Kilo"}])
    out = _picture(TRANSACTIONS_ANSWER)

    rows = _holdings()
    assert rows["NEWCO"]["quantity_est"] == "250" and rows["NEWCO"]["avg_price"] == "48.2"
    assert rows["NEWCO"]["status"] == "open" and rows["NEWCO"]["currency"] == "USD"
    assert rows["NEWCO"]["invested_usd"] == "12050"
    assert rows["GOLD"]["quantity_est"] == "2"
    assert rows["GOLD"]["avg_price"] == "1465.75"  # (1 × 500 + 1 × 2,431.50) / 2
    assert rows["GOLD"]["invested_usd"] == "2931.5"
    assert out["applied"][0] == "Bought 250 NEWCO at 48.20 USD on 21 Sep 2026 — new position"
    assert out["applied"][1].startswith("Bought 1 GOLD at 2,431.50 USD on 25 Sep 2026 — 1 → 2 held")

    trades = _on_file()
    assert [t["symbol"] for t in trades] == ["GOLD", "NEWCO", "GOLD"]  # newest first
    assert trades[1] == {**_trade("NEWCO", "Buy", "2026-09-21", "250", "48.2", "12050", name="New Co"),
                         "commission": "0"}  # fmt: skip

    # only the changed rows change: no Windows line endings rewriting every line of the file
    assert "\r" not in settings.holdings_csv().read_text() and "\r" not in settings.transactions_csv().read_text()
    assert trades[0]["name"] == "Gold Kilo"  # the name on file, not the screenshot's spelling

    (event,) = update.load_events()
    assert event["status"] == "applied" and event["source"] == "pasted-image1.png"
    assert event["checks"] == ["Your broker lists 3 transactions, and so does the portfolio file now."]
    assert update.load_pending() is None  # nothing waits: every trade checked out


def test_the_same_screenshot_sent_twice_adds_nothing_twice(workspace):
    _picture(TRANSACTIONS_ANSWER)
    before = (_holdings(), _on_file())
    _picture(TRANSACTIONS_ANSWER)
    assert (_holdings(), _on_file()) == before
    assert update.load_events()[-1]["status"] == "nothing_new"


def test_an_earlier_screenshot_then_a_fuller_one_adds_only_the_new_trade(workspace):
    first = {**TRANSACTIONS_ANSWER, "rows_stated": "", "trades": TRANSACTIONS_ANSWER["trades"][1:]}
    _picture(first)
    _picture(TRANSACTIONS_ANSWER, data=b"\x89PNG-2")
    assert len(_on_file()) == 2 and _holdings()["NEWCO"]["quantity_est"] == "250"
    assert "1 trade(s) in it were already on file" in update.load_events()[-1]["checks"][-1]


def test_a_misread_row_is_set_aside_and_named_while_the_good_rows_apply(workspace):
    misread = _trade("NEWCO", "Buy", "Sep 21, 2026", "280", "48.20", "12.05 K")  # 5 read as 8
    answer = {**TRANSACTIONS_ANSWER, "rows_stated": "", "trades": [TRANSACTIONS_ANSWER["trades"][0], misread]}
    out = _picture(answer)

    assert "NEWCO" not in _holdings() and _holdings()["GOLD"]["quantity_est"] == "2"
    assert len(out["applied"]) == 1
    (problem,) = update.load_events()[-1]["problems"]
    assert problem.startswith("not applied — NEWCO 21 Sep 2026: 280 × 48.20 = 13,496.00, which doesn't match")


def test_a_sale_of_more_than_is_held_is_refused(workspace):
    sale = _trade("USDS", "Sell", "Sep 1, 2026", "500", "12", "6,000")
    _picture({**TRANSACTIONS_ANSWER, "rows_stated": "", "trades": [sale]})
    assert _holdings()["USDS"]["quantity_est"] == "100" and _on_file() == []
    event = update.load_events()[-1]
    assert event["status"] == "unreadable" and "more than the 100 on file" in event["problems"][0]


def test_a_full_sale_closes_the_position(workspace):
    sale = _trade("USDS", "Sell", "Sep 1, 2026", "100", "12", "1,200")
    out = _picture({**TRANSACTIONS_ANSWER, "rows_stated": "", "trades": [sale]})
    row = _holdings()["USDS"]
    assert row["status"] == "closed" and row["quantity_est"] == "0" and row["allocation_pct"] == ""
    assert out["applied"] == ["Sold 100 USDS at 12.00 USD on 1 Sep 2026 — 100 → 0 held (closed)"]


def test_a_currency_that_differs_from_the_position_is_not_netted(workspace):
    buy = _trade("EURS", "Buy", "Sep 1, 2026", "10", "21", "210", currency="USD")
    _picture({**TRANSACTIONS_ANSWER, "rows_stated": "", "trades": [buy]})
    assert _holdings()["EURS"]["quantity_est"] == "100"
    assert "held in EUR" in update.load_events()[-1]["problems"][0]


def test_a_broker_count_that_doesnt_match_asks_for_the_rest(workspace):
    _picture({**TRANSACTIONS_ANSWER, "rows_stated": "21 transactions"})
    check = update.load_events()[-1]["checks"][0]
    assert check.startswith("Your broker lists 21 transactions; the portfolio file has 2.")


def test_a_picture_that_isnt_a_trade_list_says_so_only_when_he_sent_an_update(workspace):
    other = {"view": "other", "rows_stated": "", "trades": [], "positions": [], "unreadable": ""}
    _picture(other, subject="Re: AI STOCK PORTFOLIO REVIEW")  # a logo in a reply
    assert update.load_events() == []
    _picture(other)
    assert update.load_events()[-1]["problems"] == ["it doesn't show a transactions or positions list"]


def test_a_failed_reading_is_reported_and_the_file_kept(workspace):
    class Broken:
        beta = None

    out = update.process(_message("shot.png", b"\x89PNG", "image/png"), "2026-10-02", client=Broken(), today=TODAY)
    assert update.load_events()[-1]["status"] == "error"
    assert out["notes"][0].startswith("shot.png could not be read")
    assert (settings.data_dir() / "raw" / "uploads" / "2026-10-02-shot.png").read_bytes() == b"\x89PNG"


def test_the_reading_uses_the_main_model_and_carries_the_file(workspace):
    client = FakeClaude(TRANSACTIONS_ANSWER)
    update.process(_message("shot.png", b"\x89PNG", "image/png"), "2026-10-02", client=client, today=TODAY)
    request = client.requests[0]
    blocks = request["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png"
    assert base64.b64decode(blocks[0]["source"]["data"]) == b"\x89PNG"
    assert request["model"] == update.llm.MODEL and request["thinking"] == {"type": "adaptive"}
    assert "no prices from memory" in request["system"]


# ---- Checking what was read -------------------------------------------------


def test_each_row_is_checked_before_it_counts():
    ok, why = update.check_trade(_trade("A", "Buy", "Sep 21, 2026", "250", "48.20", "12.05 K"), TODAY)
    assert why is None and ok["date"] == "2026-09-21" and ok["total"] == 12050.0
    assert update.check_trade(_trade("A", "Buy", "Oct 9, 2026", "1", "2", "2"), TODAY)[1].endswith("in the future")
    assert "date not readable" in update.check_trade(_trade("A", "Buy", "03/04/2026", "1", "2", "2"), TODAY)[1]
    assert "buy or sell" in update.check_trade(_trade("A", "", "Sep 1, 2026", "1", "2", "2"), TODAY)[1]
    assert "currency" in update.check_trade(_trade("A", "Buy", "Sep 1, 2026", "1", "2", "2", currency=""), TODAY)[1]
    assert update.check_trade(_trade("A", "Sell", "1 Sep 2026", "1,000", "2,5", "2.5 K"), TODAY)[0]["price"] == 2.5


def test_messy_numbers_are_read_or_left_out():
    assert update._number("1,234.50") == 1234.5 and update._number("$1,200") == 1200.0
    assert update._number("12,50") == 12.5 and update._number("1.234,56") == 1234.56
    assert update._number("—") is None and update._number(None) is None
    assert update._amount("12.5 K USD") == (12500.0, 50.0)
    assert update._amount("1.2M")[0] == 1_200_000 and update._amount("-") == (None, None)


# ---- Spreadsheets ----------------------------------------------------------


def test_a_broker_export_of_positions_is_applied_and_explained(workspace):
    out = update.process(_message("positions.csv", EXPORT.encode()), "2026-01-02")

    rows = _holdings()
    assert rows["USDS"]["quantity_est"] == "150" and rows["NEW"]["quantity_est"] == "1200"  # "1,200" read properly
    assert rows["NEW"]["status"] == "open" and rows["EURS"]["currency"] == "EUR"
    assert "USDS: 100 → 150 shares (bought 50)" in out["applied"]
    assert "NEW: new position, 1200 shares" in out["applied"]
    assert out["pending"] == [] and update.load_events()[-1]["status"] == "applied"
    # the file it came from is kept exactly as it arrived
    assert (workspace / "data" / "raw" / "uploads" / "2026-01-02-positions.csv").read_text() == EXPORT


def test_a_broker_export_of_trades_is_netted_not_mistaken_for_positions(workspace):
    export = ("Symbol,Side,Date,Qty,Price,Currency,Commission,Total\n"
              "USDS,Buy,2026-09-01,50,12.00,USD,0,600\n"
              "USDS,Buy,2026-09-02,10,13.00,USD,0,130\n")  # fmt: skip
    update.process(_message("trades.csv", export.encode()), "2026-10-02", today=TODAY)
    assert _holdings()["USDS"]["quantity_est"] == "160"  # 100 + 50 + 10, not "10"
    assert len(_on_file()) == 2


def test_an_xlsx_export_is_read_without_extra_libraries(workspace):
    sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
             '<row r="1"><c r="A1" t="inlineStr"><is><t>Symbol</t></is></c><c r="B1" t="inlineStr"><is><t>Quantity</t>'
             '</is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>USDS</t></is></c><c r="B2"><v>175</v></c></row>'
             "</sheetData></worksheet>")  # fmt: skip
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    update.process(_message("positions.xlsx", buffer.getvalue(), media), "2026-10-02", today=TODAY)
    assert _holdings()["USDS"]["quantity_est"] == "175"


def test_a_position_missing_from_the_upload_is_left_alone(workspace):
    update.process(_message("positions.csv", b"Symbol,Quantity\nUSDS,150\n"), "2026-01-02")
    rows = _holdings()
    assert rows["EURS"]["quantity_est"] == "100"  # untouched, not closed
    assert rows["EURS"]["status"] == "open"


def test_an_unreadable_file_says_so_and_changes_nothing(workspace):
    before = _holdings()
    update.process(_message("notes.csv", b"one,two\n1,2\n"), "2026-01-02")
    assert _holdings() == before
    event = update.load_events()[-1]
    assert event["status"] == "unreadable" and "no symbol or quantity column" in event["problems"][0]


def test_two_files_with_the_same_name_are_both_kept(workspace):
    update.save_uploads(_message("image001.png", b"one", "image/png"), "2026-10-02")
    update.save_uploads(_message("image001.png", b"two", "image/png"), "2026-10-02")
    kept = sorted(p.read_bytes() for p in (workspace / "data" / "raw" / "uploads").iterdir())
    assert kept == [b"one", b"two"]


# ---- A positions screenshot waits for his OK --------------------------------


def test_a_positions_screenshot_is_transcribed_and_waits_for_a_yes(workspace):
    out = _picture(POSITIONS_ANSWER)

    assert _holdings()["USDS"]["quantity_est"] == "100"  # nothing applied yet
    assert out["pending"][:2] == ["USDS: 100 → 150 shares (bought 50)", "USDS: average cost 10 → 11"]
    assert out["pending"][2].startswith("not in the upload, left unchanged:")
    assert update.panel("2026-10-02")["items"][0]["status"] == "waiting"
    assert json.loads((workspace / "state" / "pending_holdings.json").read_text())["source"] == "pasted-image1.png"

    reply = [{"text": "Confirmed!", "date": "Sat, 3 Oct 2026 08:00:00 +0000"}]
    assert update.is_confirmation(reply)
    applied = update.apply_pending("2026-10-03")
    assert applied[0] == "USDS: 100 → 150 shares (bought 50)"
    assert _holdings()["USDS"]["quantity_est"] == "150"
    assert update.load_pending() is None and update.apply_pending("2026-10-04") == []
    assert update.panel("2026-10-03")["items"][0]["status"] == "confirmed"


def test_a_yes_from_before_the_reading_or_a_longer_message_doesnt_confirm(workspace):
    _picture(POSITIONS_ANSWER)
    assert not update.is_confirmation([{"text": "Confirmed", "date": "Mon, 1 Jan 2024 08:00:00 +0000"}])
    assert not update.is_confirmation([{"text": "yes but also look at energy", "date": ""}])


def test_a_reading_still_waiting_is_shown_on_later_days(workspace):
    _picture(POSITIONS_ANSWER)
    (item,) = update.panel("2026-10-05")["items"]
    assert item["status"] == "waiting" and item["heading"].startswith("Still waiting for your OK (read on 2 Oct 2026)")


def test_two_screenshots_sent_days_apart_are_read_one_by_one(workspace, monkeypatch):
    """An older screenshot shows a shorter list and a smaller count than a newer one. Read together,
    the older count was reported, and the check wrongly said the file had one too many."""
    _transactions([{**_trade(f"OLD{i}", "Buy", "2026-01-05", "1", "1", "1")} for i in range(7)])
    older = {**TRANSACTIONS_ANSWER, "rows_stated": "8 transactions", "trades": TRANSACTIONS_ANSWER["trades"][1:]}
    newer = {**TRANSACTIONS_ANSWER, "rows_stated": "9 transactions"}
    answers = iter([older, newer])
    seen = []

    def one_at_a_time(uploads, client=None, mode="direct", today=None):
        seen.append([u["filename"] for u in uploads])
        return _as_read(next(answers), today)

    monkeypatch.setattr(update, "from_picture", one_at_a_time)
    feedback = _message("a.png", b"\x89PNG-a", "image/png") + _message("b.png", b"\x89PNG-b", "image/png")
    update.process(feedback, "2026-10-02", today=TODAY)
    assert seen == [["a.png"], ["b.png"]]
    event = update.load_events()[-1]
    assert event["checks"][0] == "Your broker lists 9 transactions, and so does the portfolio file now."
    assert len(event["lines"]) == 2  # the trade in both screenshots counted once


def _as_read(answer, today):
    trades, set_aside = [], []
    for raw in answer["trades"]:
        trade, why = update.check_trade(raw, today)
        (trades.append(trade) if trade else set_aside.append(why))
    return ({"view": answer["view"], "rows_stated": answer["rows_stated"], "positions": [], "trades": trades,
             "set_aside": set_aside, "unreadable": ""}, {"input_tokens": 10})  # fmt: skip
