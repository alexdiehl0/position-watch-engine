"""Email in and out, through the operator's Gmail with an app password.

Sending uses SMTP; reading feedback uses IMAP, the same two standard
protocols any provider offers, so switching to another mailbox is a matter of
MAIL_SMTP_HOST / MAIL_IMAP_HOST. Credentials come from the environment
(MAIL_USER, MAIL_APP_PASSWORD) and are never printed.

Feedback is every "Portfolio feedback" message and every reply to a daily
review email from the last 30 days, from someone in the people
file, that hasn't been used before. Used message IDs are recorded in the
workspace's state/feedback_used.json so nothing is applied twice.

A message that looks like feedback but can't be used -- an address that isn't
in the people file, or nothing left once quoted text is stripped -- is
reported rather than dropped in silence: fetch_feedback() returns those too
(sender and subject only, never the body, which is untrusted text), and
state/ignored_messages.json remembers which addresses have already been
mentioned so the daily email names each one only once.
"""

import email
import email.policy
import email.utils
import imaplib
import json
import os
import re
import smtplib
from datetime import date, timedelta
from email.message import EmailMessage

from position_watch import people, settings

# Replies to the daily email (current and earlier subject lines) and notes from the dashboard feedback box.
FEEDBACK_SUBJECTS = ("Portfolio feedback", "Re: AI STOCK PORTFOLIO REVIEW", "Re: Portfolio Review")
SENDER_NAME = "AI Stock Portfolio Review"


class MailNotConfigured(RuntimeError):
    pass


def _credentials():
    user, password = os.environ.get("MAIL_USER"), os.environ.get("MAIL_APP_PASSWORD")
    if not user or not password:
        raise MailNotConfigured("MAIL_USER and MAIL_APP_PASSWORD must be set to send or read email")
    return user, password


def send(to: list, subject: str, body: str, html: str | None = None):
    """Plain-text body, plus an HTML version when given (mail apps show the best they can)."""
    user, password = _credentials()
    msg = EmailMessage()
    msg["From"] = email.utils.formataddr((SENDER_NAME, user))
    msg["To"], msg["Subject"] = ", ".join(to), subject
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL(os.environ.get("MAIL_SMTP_HOST", "smtp.gmail.com"), 465, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)


# ---- Feedback ------------------------------------------------------------


def _used_path():
    return settings.state_dir() / "feedback_used.json"


def load_used() -> dict:
    path = _used_path()
    return json.loads(path.read_text()) if path.exists() else {}


def mark_used(messages: list, today: str):
    used = load_used()
    for m in messages:
        used[m["message_id"]] = {"date": today, "from": m["name"]}
    _used_path().parent.mkdir(parents=True, exist_ok=True)
    _used_path().write_text(json.dumps(used, indent=2, sort_keys=True) + "\n")


_QUOTE_START = re.compile(r"^(On .+wrote:|-----Original Message-----|From: .+)$")


def _own_words(text: str) -> str:
    """The new part of a reply: stops at the quoted original and drops '>' lines."""
    kept = []
    for line in text.splitlines():
        if _QUOTE_START.match(line.strip()):
            break
        if not line.lstrip().startswith(">"):
            kept.append(line)
    return "\n".join(kept).strip()


def _plain_text(msg) -> str:
    part = msg.get_body(preferencelist=("plain",)) if msg.is_multipart() else msg
    if part is None:
        return ""
    return (
        part.get_content() if hasattr(part, "get_content") else part.get_payload(decode=True).decode(errors="replace")
    )


def read_message(raw: bytes) -> dict | None:
    """One raw email -> the feedback in it, or why it can't be used. None if it
    isn't feedback at all (the subject doesn't match).

    Usable:   {"usable": True, message_id, name, role, date, subject, text}
    Not:      {"usable": False, "from", "subject", "reason"} -- no body text,
              since an unknown sender's words are not to be passed on."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = str(msg.get("Subject", ""))
    if not any(s.lower() in subject.lower() for s in FEEDBACK_SUBJECTS):
        return None
    sender = email.utils.parseaddr(str(msg.get("From", "")))[1]
    message_id = str(msg.get("Message-ID", "")).strip() or f"{sender}:{msg.get('Date')}"
    who = people.who_sent(sender)
    if not who:
        return {"usable": False, "message_id": message_id, "from": sender, "subject": subject,
                "reason": "that address isn't in your people file"}  # fmt: skip
    text = _own_words(_plain_text(msg))
    if not text:
        return {"usable": False, "message_id": message_id, "from": sender, "subject": subject,
                "reason": "the message had no text of its own (only quoted email)"}  # fmt: skip
    return {
        "usable": True,
        "message_id": message_id,
        "from": sender,
        "name": who["name"],
        "role": who["role"],
        "date": str(msg.get("Date", "")),
        "subject": subject,
        "text": text[:4000],
    }


def parse_feedback(raw: bytes) -> dict | None:
    """Usable feedback only, or None. (read_message() also says why not.)"""
    item = read_message(raw)
    return item if item and item["usable"] else None


def fetch_feedback(days: int = 30) -> tuple[list, list]:
    """(usable feedback, messages that couldn't be used) from the last `days`,
    skipping anything already applied."""
    user, password = _credentials()
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    used = load_used()
    found, ignored = {}, {}
    with imaplib.IMAP4_SSL(os.environ.get("MAIL_IMAP_HOST", "imap.gmail.com"), timeout=30) as imap:
        imap.login(user, password)
        imap.select("INBOX", readonly=True)
        for subject in FEEDBACK_SUBJECTS:
            status, data = imap.search(None, "SINCE", since, "SUBJECT", f'"{subject}"')
            if status != "OK":
                continue
            for num in data[0].split():
                status, parts = imap.fetch(num, "(RFC822)")
                if status == "OK" and parts and isinstance(parts[0], tuple):
                    item = read_message(parts[0][1])
                    if not item or item["message_id"] in used:
                        continue
                    (found if item["usable"] else ignored)[item["message_id"]] = item
    return list(found.values()), list(ignored.values())


def _ignored_path():
    return settings.state_dir() / "ignored_messages.json"


def load_ignored() -> dict:
    path = _ignored_path()
    return json.loads(path.read_text()) if path.exists() else {}


def record_ignored(items: list, today: str) -> list:
    """Remembers the addresses we couldn't accept and returns the ones not
    mentioned before, so the daily email names each address only once."""
    known = load_ignored()
    fresh = [i for i in items if i["from"].lower() not in known]
    for item in items:
        entry = known.setdefault(item["from"].lower(), {"first_seen": today, "messages": 0})
        entry["messages"] += 1
        entry["last_seen"], entry["last_subject"], entry["reason"] = today, item["subject"], item["reason"]
    if items:
        _ignored_path().parent.mkdir(parents=True, exist_ok=True)
        _ignored_path().write_text(json.dumps(known, indent=2, sort_keys=True) + "\n")
    return fresh
