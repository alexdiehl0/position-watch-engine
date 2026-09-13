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


def test_used_messages_are_remembered(workspace):
    mail.mark_used([{"message_id": "<abc@example.com>", "name": "Client"}], "2026-01-02")
    assert "<abc@example.com>" in mail.load_used()
