"""Deterministic access to config/people.json: who uses the system, who gets
the daily email, whose inbox holds the feedback, and the client's preference
profile. A person may have several addresses (`email` plus `emails`); feedback
counts from any of them, and the first is where the daily email goes.

The daily routine decides *what* a piece of feedback means; this module only
reads and writes the file. A lasting preference found in feedback (e.g. "no
more China exposure") is recorded with update_preferences(), which appends a
dated history entry naming the source, so every change to the profile can be
traced back to the message that caused it.

`python -m position_watch people` prints the file for the routine to read.
"""

import json
import os
from datetime import date

from position_watch import client_requests, settings


def people_path():
    return settings.config_dir() / "people.json"


LIST_FIELDS = ("favours", "preferred_sectors", "avoid", "other_notes")
SCALAR_FIELDS = ("investment_horizon", "goal", "risk_tolerance")


def load():
    with open(people_path()) as f:
        return json.load(f)


def save(data):
    with open(people_path(), "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def people():
    return load()["people"]


def recipients():
    """Emails of everyone who should get the daily review (their first address)."""
    return [p["email"] for p in people() if p.get("receives_daily_email") and p.get("email")]


def alert_recipients():
    """Who hears about a failed run: the operators, or everyone on the daily email if none is listed."""
    return [p["email"] for p in people() if p.get("role") == "operator" and p.get("email")] or recipients()


def operator():
    return next(p for p in people() if p.get("role") == "operator")


def client():
    return next(p for p in people() if p.get("role") == "client")


def routine_owner():
    """The person whose claude.ai account runs the routines (`routines_run_on`)."""
    owner_id = load().get("routines_run_on") or operator()["id"]
    return next(p for p in people() if p["id"] == owner_id)


def feedback_inbox():
    """The inbox the daily run reads feedback from (MAIL_USER when set, else the
    owner named in `routines_run_on`), so the dashboard's feedback box sends there."""
    return os.environ.get("MAIL_USER") or routine_owner()["email"]


def addresses(person: dict) -> list:
    """Every address that counts as this person: `email` plus any in `emails`."""
    listed = [person.get("email"), *(person.get("emails") or [])]
    return [a.strip().lower() for a in listed if a]


def who_sent(email):
    """Name and role for a feedback sender's address, or None if unknown."""
    email = (email or "").strip().lower()
    for p in people():
        if email in addresses(p):
            return {"name": p["name"], "role": p["role"]}
    return None


# ---- Standing requests --------------------------------------------------


def active_requests() -> list:
    """The client's live requests, newest first. See client_requests.py for what each kind does."""
    return list(reversed(client().get("requests") or []))


def apply_requests(changes: list, senders: dict, today: str) -> list:
    """Records the requests a message asked for and returns one line per change.

    changes: [{message_id, kind, value, scope, operation}] as classified from
    feedback; `senders` maps message_id -> that message's sender address.
    A `set` replaces the live request of the same kind (the newest wins);
    `clear` drops it. `add_sender` adds an address to the sender's own entry,
    so only someone already trusted can widen who is trusted."""
    data = load()
    person = next(p for p in data["people"] if p.get("role") == "client")
    requests = person.setdefault("requests", [])
    lines = []
    for change in changes:
        kind, value = change["kind"], (change.get("value") or "").strip()
        operation, message_id = change.get("operation", "set"), change["message_id"]
        if kind not in client_requests.BY_NAME:
            continue
        if operation == "clear":
            before = len(requests)
            requests[:] = [r for r in requests if r["kind"] != kind]
            if len(requests) < before:
                lines.append(f"cleared {kind}")
            continue
        if not value:
            continue
        if kind == "add_sender":
            lines += _add_sender(data, senders.get(message_id), value)
            continue
        scope = change.get("scope") if client_requests.BY_NAME[kind].once else "standing"
        requests[:] = [r for r in requests if r["kind"] != kind]  # newest of a kind wins
        requests.append({"kind": kind, "value": value, "scope": scope if scope in client_requests.SCOPES else "standing",
                         "text": (change.get("text") or "")[:300], "asked_on": today,
                         "source_message": message_id})  # fmt: skip
        lines.append(f"{kind}: {value}" + (" (one run)" if scope == "once" else ""))
    if lines:
        person.setdefault("preferences", {})
        _log_history(person, f"requests: {'; '.join(lines)}", f"feedback on {today}", today)
        save(data)
    return lines


def _add_sender(data: dict, asked_from: str | None, address: str) -> list:
    """Adds an address to the entry of the person who asked, if they are the client or operator."""
    address = address.strip().lower()
    if "@" not in address or " " in address:
        return []
    owner = next((p for p in data["people"] if asked_from and asked_from.lower() in addresses(p)), None)
    if not owner or owner.get("role") not in ("client", "operator"):
        return []
    extra = owner.setdefault("emails", [])
    if address in addresses(owner) or len(extra) >= 4:
        return []
    extra.append(address)
    return [f"accept mail from {address} as {owner['name']}"]


def consume_once_requests(today: str) -> list:
    """Drops the one-run requests after the run that used them. Returns what went."""
    data = load()
    person = next(p for p in data["people"] if p.get("role") == "client")
    requests = person.get("requests") or []
    spent = [r for r in requests if r.get("scope") == "once"]
    if spent:
        person["requests"] = [r for r in requests if r.get("scope") != "once"]
        _log_history(person, f"one-run requests done: {'; '.join(r['kind'] + ': ' + r['value'] for r in spent)}",
                     f"run of {today}", today)  # fmt: skip
        save(data)
    return spent


def _log_history(person: dict, change: str, source: str, when: str):
    history = person.setdefault("preferences", {}).setdefault("history", [])
    history.append({"date": when, "change": change, "source": source})


def update_preferences(changes: dict, source: str, when: str = None):
    """Applies `changes` to the client's preferences and logs them.

    changes: scalar fields (investment_horizon, goal, risk_tolerance) are
    replaced; list fields (favours, preferred_sectors, avoid, other_notes)
    take {"add": [...], "remove": [...]}. source: where the change came from,
    e.g. "feedback email <message id> from <name>". Returns the summary line
    written to history."""
    data = load()
    prefs = next(p for p in data["people"] if p.get("role") == "client")["preferences"]
    summary = []
    for key, value in changes.items():
        if key in SCALAR_FIELDS:
            prefs[key] = value
            summary.append(f"{key} -> {value}")
        elif key in LIST_FIELDS:
            current = prefs.setdefault(key, [])
            for item in value.get("add", []):
                if item not in current:
                    current.append(item)
                    summary.append(f"{key} + {item}")
            for item in value.get("remove", []):
                if item in current:
                    current.remove(item)
                    summary.append(f"{key} - {item}")
        else:
            raise ValueError(f"unknown preference field: {key}")
    if summary:
        line = "; ".join(summary)
        prefs.setdefault("history", []).append(
            {"date": when or date.today().isoformat(), "change": line, "source": source}
        )
        save(data)
        return line
    return None
