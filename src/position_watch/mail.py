"""Email in and out, through the operator's Gmail with an app password.

Sending uses SMTP; reading feedback uses IMAP, the same two standard
protocols any provider offers, so switching to another mailbox is a matter of
MAIL_SMTP_HOST / MAIL_IMAP_HOST. Credentials come from the environment
(MAIL_USER, MAIL_APP_PASSWORD) and are never printed.

Feedback is every "Portfolio feedback" message and every reply to a
"Portfolio Review" email from the last 30 days, from someone in the people
file, that hasn't been used before. Used message IDs are recorded in the
workspace's state/feedback_used.json so nothing is applied twice.
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

FEEDBACK_SUBJECTS = ("Portfolio feedback", "Re: Portfolio Review")


class MailNotConfigured(RuntimeError):
    pass


def _credentials():
    user, password = os.environ.get("MAIL_USER"), os.environ.get("MAIL_APP_PASSWORD")
    if not user or not password:
        raise MailNotConfigured("MAIL_USER and MAIL_APP_PASSWORD must be set to send or read email")
    return user, password


def send(to: list, subject: str, body: str):
    user, password = _credentials()
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, ", ".join(to), subject
    msg.set_content(body)
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


def parse_feedback(raw: bytes) -> dict | None:
    """One raw email -> a feedback dict, or None if it isn't feedback from a known person."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = str(msg.get("Subject", ""))
    if not any(s.lower() in subject.lower() for s in FEEDBACK_SUBJECTS):
        return None
    sender = email.utils.parseaddr(str(msg.get("From", "")))[1]
    who = people.who_sent(sender)
    if not who:
        return None
    text = _own_words(_plain_text(msg))
    if not text:
        return None
    return {
        "message_id": str(msg.get("Message-ID", "")).strip() or f"{sender}:{msg.get('Date')}",
        "name": who["name"],
        "role": who["role"],
        "date": str(msg.get("Date", "")),
        "subject": subject,
        "text": text[:4000],
    }


def fetch_feedback(days: int = 30) -> list:
    user, password = _credentials()
    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
    used = load_used()
    found = {}
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
                    item = parse_feedback(parts[0][1])
                    if item and item["message_id"] not in used:
                        found[item["message_id"]] = item
    return list(found.values())
