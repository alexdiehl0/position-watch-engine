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
