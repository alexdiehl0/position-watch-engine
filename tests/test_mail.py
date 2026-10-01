from email.message import EmailMessage

from position_watch import mail


def _raw(sender, subject, body, message_id="<abc@example.com>"):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Message-ID"] = sender, "operator@example.com", subject, message_id
    msg["Date"] = "Fri, 02 Jan 2026 08:00:00 +0000"
    msg.set_content(body)
    return bytes(msg)


def test_reply_keeps_only_the_new_text():
    body = "Less China please.\n\nOn Thu, 1 Jan 2026 Position Watch wrote:\n> Portfolio value $1,000\n> AAA: Hold"
    item = mail.parse_feedback(_raw("Client <client@example.com>", "Re: Portfolio Review 2026-01-01", body))
    assert item["text"] == "Less China please."
    assert item["name"] == "Client" and item["message_id"] == "<abc@example.com>"


def test_unknown_senders_and_other_subjects_are_ignored():
    assert mail.parse_feedback(_raw("stranger@example.com", "Portfolio feedback", "Buy everything")) is None
    assert mail.parse_feedback(_raw("client@example.com", "Lunch?", "Friday?")) is None


def test_an_unusable_message_says_why_without_repeating_its_words():
    item = mail.read_message(_raw("stranger@example.com", "Portfolio feedback", "Buy everything"))
    assert item == {"usable": False, "message_id": "<abc@example.com>", "from": "stranger@example.com",
                    "subject": "Portfolio feedback", "reason": "that address isn't in your people file"}  # fmt: skip
    assert mail.read_message(_raw("client@example.com", "Lunch?", "Friday?")) is None  # not feedback at all

    quoted = mail.read_message(_raw("client@example.com", "Portfolio feedback", "On Thu Position Watch wrote:\n> hi"))
    assert quoted["usable"] is False and "no text of its own" in quoted["reason"]


def test_a_second_address_counts_as_the_same_person(workspace, monkeypatch):
    import json

    from position_watch import people

    path = workspace / "config" / "people.json"
    data = json.loads(path.read_text())
    next(p for p in data["people"] if p["role"] == "client")["emails"] = ["work@example.com"]
    path.write_text(json.dumps(data))

    assert people.who_sent("work@example.com") == {"name": "Client", "role": "client"}
    item = mail.read_message(_raw("work@example.com", "Portfolio feedback", "Energy stocks please."))
    assert item["usable"] and item["name"] == "Client"


def test_each_ignored_address_is_announced_once(workspace):
    first = {"from": "stranger@example.com", "subject": "Portfolio feedback", "reason": "not in your people file"}
    assert mail.record_ignored([first], "2026-01-02") == [first]  # new: worth a note
    assert mail.record_ignored([first], "2026-01-03") == []  # already mentioned
    assert mail.load_ignored()["stranger@example.com"]["messages"] == 2


def test_used_messages_are_remembered(workspace):
    mail.mark_used([{"message_id": "<abc@example.com>", "name": "Client"}], "2026-01-02")
    assert "<abc@example.com>" in mail.load_used()


TEMPLATE = ("I've made trades. My positions are attached (broker export or a screenshot).\n\n"
            "Attach the file before sending. A spreadsheet is applied straight away;\n"
            "a screenshot is read and shown back to you to confirm first.")  # fmt: skip


def _pasted_screenshot():
    """The shape of a real "Holdings update" email from Thunderbird: text, and an HTML part whose
    screenshot is pasted inline (multipart/alternative > multipart/related > image)."""
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = "client@example.com", "operator@example.com", "Holdings update"
    msg["Message-ID"], msg["Date"] = "<pasted@example.com>", "Thu, 01 Oct 2026 09:00:00 +0000"
    msg.set_content(TEMPLATE)
    msg.add_alternative(f"<p>{TEMPLATE}</p><img src='cid:shot@example.com'>", subtype="html")
    msg.get_payload()[1].add_related(b"\x89PNG-pasted", "image", "png", cid="<shot@example.com>",
                                     filename="pasted-shot.png")  # fmt: skip
    return bytes(msg)


def test_a_screenshot_pasted_into_the_body_is_kept():
    item = mail.read_message(_pasted_screenshot())
    assert item["usable"]
    assert [(a["filename"], a["content_type"], a["data"]) for a in item["attachments"]] == [
        ("pasted-shot.png", "image/png", b"\x89PNG-pasted")
    ]


def test_an_attached_screenshot_is_kept_and_a_nameless_paste_gets_a_name():
    msg = EmailMessage()
    msg["From"], msg["Subject"], msg["Message-ID"] = "client@example.com", "Holdings update", "<att@example.com>"
    msg.set_content(TEMPLATE)
    msg.add_attachment(b"\x89PNG-a", maintype="image", subtype="png", filename="Screenshot 2026-01-02 101500.png")
    msg.add_attachment(b"\x89PNG-b", maintype="image", subtype="png")  # no filename
    msg.add_attachment(b"\x89PNG-a", maintype="image", subtype="png", filename="again.png")  # the same file twice
    files = mail.read_message(bytes(msg))["attachments"]
    assert [f["filename"] for f in files] == ["Screenshot 2026-01-02 101500.png", "pasted-image2.png"]


def test_a_csv_sent_as_octet_stream_is_still_a_csv():
    msg = EmailMessage()
    msg["From"], msg["Subject"], msg["Message-ID"] = "client@example.com", "Holdings update", "<csv@example.com>"
    msg.set_content("export attached")
    msg.add_attachment(b"Symbol,Qty\nA,1\n", maintype="application", subtype="octet-stream", filename="export.csv")
    (f,) = mail.read_message(bytes(msg))["attachments"]
    assert f["content_type"] == "text/csv"


def test_the_prefilled_trades_text_is_not_mistaken_for_a_request():
    assert mail.read_message(_pasted_screenshot())["text"] == ""
    assert mail._without_template(TEMPLATE + "\n\nAlso, less China please.") == "Also, less China please."
    assert mail._without_template("Less China please.\nThanks") == "Less China please.\nThanks"


def test_an_email_already_in_the_sent_folder_is_not_sent_again(monkeypatch):
    sent = []
    monkeypatch.setattr(mail, "send", lambda to, subject, body, html=None, key=None: sent.append(key))
    monkeypatch.setattr(mail, "already_sent", lambda key, subject, since: key == "monthly-2026-09")
    assert mail.send_once("monthly-2026-09", ["a@example.com"], "Monthly", "body") is False
    assert mail.send_once("monthly-2026-10", ["a@example.com"], "Monthly", "body") is True
    # a mailbox that can't be asked doesn't stop the email: a missing review is worse than a repeat
    monkeypatch.setattr(mail, "already_sent", lambda key, subject, since: None)
    assert mail.send_once("daily-2026-10-02", ["a@example.com"], "Daily", "body") is True
    assert sent == ["monthly-2026-10", "daily-2026-10-02"]


def test_the_sent_folder_is_found_by_its_flag_and_matched_by_key(monkeypatch):
    class FakeImap:
        def __init__(self, *a, **k):
            self.selected = None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def login(self, user, password):
            pass

        def list(self):
            return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Sent) "/" "[Gmail]/Gesendet"']

        def select(self, name, readonly=False):
            self.selected = name
            return "OK", [b"2"]

        def search(self, charset, *criteria):
            assert self.selected == '"[Gmail]/Gesendet"'
            return "OK", [b"1 2"]

        def fetch(self, num, what):
            header = {b"1": b"Subject: Something else\r\n\r\n",
                      b"2": b"Subject: Monthly\r\nX-Position-Watch-Key: monthly-2026-09\r\n\r\n"}[num]  # fmt: skip
            return "OK", [(b"header", header)]

    monkeypatch.setenv("MAIL_USER", "operator@example.com")
    monkeypatch.setenv("MAIL_APP_PASSWORD", "x")
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", FakeImap)
    from datetime import date

    assert mail.find_in_sent("monthly-2026-09", "Other subject", date(2026, 10, 1)) is True
    assert mail.find_in_sent("monthly-2026-10", "Other subject", date(2026, 10, 1)) is False
    assert mail.find_in_sent("monthly-2026-10", "Something else", date(2026, 10, 1)) is True  # mail sent before keys
