"""Updating the portfolio from what the client sends in.

He trades, so `holdings.csv` has to follow. He emails his broker's export or a
photograph of his positions to the usual address; the next run keeps the file
in `data/raw/uploads/` (the audit trail, never edited afterwards) and turns it
into a *proposed* change with a plain-language diff.

A spreadsheet is read by code and applied straight away -- the numbers are
machine-read, and the diff says what changed. A screenshot is transcribed by
one Claude call and waits for a one-word confirmation, because a misread digit
would quietly corrupt the portfolio. Nothing is ever inferred: a column that
isn't there stays as it was.
"""

import base64
import csv
import json
import re
from datetime import date

from position_watch import llm, settings

QUANTITY = ("quantity", "qty", "shares", "units", "position", "holding")
PRICE = ("avg price", "average price", "avg cost", "average cost", "cost basis", "price paid", "book cost")
SYMBOL = ("symbol", "ticker", "instrument", "code")
NAME = ("name", "description", "security")
CURRENCY = ("currency", "ccy")

SCHEMA = {
    "type": "object",
    "properties": {
        "positions": {
            "type": "array",
            "description": "One per position visible in the file; leave a field out rather than guessing it.",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "name": {"type": "string"},
                    "quantity": {"type": "string", "description": "Shares held, as shown; '' if not shown."},
                    "avg_price": {"type": "string", "description": "Average cost per share as shown; '' if not shown."},
                    "currency": {"type": "string", "description": "3-letter code if shown, else ''."},
                },
                "required": ["symbol", "name", "quantity", "avg_price", "currency"],
                "additionalProperties": False,
            },
        },
        "unreadable": {"type": "string", "description": "What could not be read, or ''."},
    },
    "required": ["positions", "unreadable"],
    "additionalProperties": False,
}

SYSTEM = """You transcribe a screenshot or PDF of someone's brokerage positions into data. You copy what is \
visible and nothing else: no prices from memory, no filled-in blanks, no tidying of odd numbers. If a figure is \
cut off, blurred or ambiguous, leave that field empty and say so in `unreadable`. You never comment on the \
portfolio."""


def uploads_dir():
    return settings.data_dir() / "raw" / "uploads"


def save_uploads(feedback: list, day: str) -> list:
    """Keeps every attachment as it arrived. Returns [{path, filename, content_type, from}]."""
    saved = []
    for message in feedback:
        for item in message.get("attachments") or []:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "-", item["filename"])[:60]
            path = uploads_dir() / f"{day}-{safe}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item["data"])
            saved.append({"path": path, "filename": item["filename"], "content_type": item["content_type"],
                          "from": message.get("name") or message.get("from")})  # fmt: skip
    return saved


def _number(value):
    """'1,234.50 EUR' -> 1234.5; anything unreadable -> None (never a guess)."""
    if value is None:
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", str(value).replace(",", ""))
    try:
        return float(cleaned)
    except ValueError:
        return None


def _column(headers: list, wanted: tuple):
    for header in headers:
        if any(word in (header or "").strip().lower() for word in wanted):
            return header
    return None


def from_spreadsheet(path) -> tuple[list, list]:
    """Reads a broker's CSV export into positions. Returns (positions, notes)."""
    try:
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            rows = list(csv.DictReader(f))
    except OSError as exc:
        return [], [f"{path.name}: could not be read ({exc})"]
    if not rows:
        return [], [f"{path.name}: no rows"]
    headers = list(rows[0])
    symbol_col, qty_col = _column(headers, SYMBOL), _column(headers, QUANTITY)
    if not symbol_col or not qty_col:
        return [], [f"{path.name}: no symbol or quantity column (found {', '.join(headers[:6])})"]
    price_col, name_col, currency_col = _column(headers, PRICE), _column(headers, NAME), _column(headers, CURRENCY)
    positions = []
    for row in rows:
        symbol = (row.get(symbol_col) or "").strip().upper()
        quantity = _number(row.get(qty_col))
        if not symbol or quantity is None:
            continue
        positions.append({
            "symbol": symbol,
            "name": (row.get(name_col) or "").strip() if name_col else "",
            "quantity": quantity,
            "avg_price": _number(row.get(price_col)) if price_col else None,
            "currency": (row.get(currency_col) or "").strip().upper() if currency_col else "",
        })  # fmt: skip
    return positions, ([] if positions else [f"{path.name}: no positions could be read"])


def from_picture(uploads: list, client=None, mode: str = "direct") -> tuple[list, list, dict]:
    """Transcribes screenshots or PDFs with one Claude call. Returns (positions, notes, usage)."""
    files = [(u["content_type"], base64.b64encode(u["path"].read_bytes()).decode()) for u in uploads]
    user = ("Here " + ("is a file" if len(files) == 1 else f"are {len(files)} files") +
            " showing the client's brokerage positions. Transcribe every position you can read.")  # fmt: skip
    request = llm.request_with_files(SYSTEM, user, SCHEMA, files, model=llm.SMALL_MODEL)
    message, batched = llm.ask(request, client=client, mode=mode)
    answer = llm.json_answer(message)
    positions = []
    for row in answer.get("positions", []):
        quantity = _number(row.get("quantity"))
        if row.get("symbol") and quantity is not None:
            positions.append({"symbol": row["symbol"].strip().upper(), "name": (row.get("name") or "").strip(),
                              "quantity": quantity, "avg_price": _number(row.get("avg_price")),
                              "currency": (row.get("currency") or "").strip().upper()})  # fmt: skip
    notes = [f"could not read: {answer['unreadable']}"] if answer.get("unreadable") else []
    return positions, notes, llm.usage(message, batched)


def load_holdings() -> list:
    with open(settings.holdings_csv(), newline="") as f:
        return list(csv.DictReader(f))


def diff(current: list, positions: list) -> list:
    """What would change, in plain words. Nothing is closed for being absent."""
    by_symbol = {(r.get("symbol") or "").upper(): r for r in current}
    lines, seen = [], set()
    for p in positions:
        seen.add(p["symbol"])
        row = by_symbol.get(p["symbol"])
        if not row:
            lines.append(f"{p['symbol']}: new position, {p['quantity']:g} shares")
            continue
        was = _number(row.get("quantity_est"))
        if was is not None and abs(was - p["quantity"]) > 0.0001:
            moved = "bought" if p["quantity"] > was else "sold"
            lines.append(f"{p['symbol']}: {was:g} → {p['quantity']:g} shares ({moved} {abs(p['quantity'] - was):g})")
        old_price = _number(row.get("avg_price"))
        if p["avg_price"] and old_price and abs(old_price - p["avg_price"]) / old_price > 0.001:
            lines.append(f"{p['symbol']}: average cost {old_price:g} → {p['avg_price']:g}")
    missing = [s for s, r in by_symbol.items() if s not in seen and (r.get("status") or "open") == "open"]
    if missing:
        lines.append(f"not in the upload, left unchanged: {', '.join(sorted(missing))}")
    return lines


def apply(positions: list) -> list:
    """Writes the update into holdings.csv and returns what changed."""
    current = load_holdings()
    changed = diff(current, positions)
    by_symbol = {(r.get("symbol") or "").upper(): r for r in current}
    fields = list(current[0]) if current else ["symbol", "name", "currency", "quantity_est", "avg_price", "status"]
    for p in positions:
        row = by_symbol.get(p["symbol"])
        if not row:
            row = dict.fromkeys(fields, "")
            row.update({"symbol": p["symbol"], "name": p["name"], "status": "open"})
            current.append(row)
            by_symbol[p["symbol"]] = row
        row["quantity_est"] = f"{p['quantity']:g}"
        if p["avg_price"]:
            row["avg_price"] = f"{p['avg_price']:g}"
        if p["currency"]:
            row["currency"] = p["currency"]
        row["status"] = "open"
    with open(settings.holdings_csv(), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(current)
    return changed


def _pending_path():
    return settings.state_dir() / "pending_holdings.json"


def save_pending(positions: list, changed: list, source: str, day: str):
    """A transcription waits here until he confirms it."""
    _pending_path().parent.mkdir(parents=True, exist_ok=True)
    _pending_path().write_text(json.dumps({"date": day, "source": source, "positions": positions,
                                           "changes": changed}, indent=2) + "\n")  # fmt: skip


def load_pending() -> dict | None:
    path = _pending_path()
    return json.loads(path.read_text()) if path.exists() else None


def clear_pending():
    _pending_path().unlink(missing_ok=True)


def process(feedback: list, day: str | None = None, client=None) -> dict:
    """Saves what arrived, applies a spreadsheet, holds a picture for confirmation.
    Returns {"notes": [...], "applied": [...], "pending": [...], "usage": {...}}."""
    day = day or date.today().isoformat()
    uploads = save_uploads(feedback, day)
    out = {"notes": [], "applied": [], "pending": [], "usage": {}}
    if not uploads:
        return out

    sheets = [u for u in uploads if u["filename"].lower().endswith((".csv", ".txt")) or "csv" in u["content_type"]]
    pictures = [u for u in uploads if u not in sheets]
    positions, notes = [], []
    for sheet in sheets:
        found, sheet_notes = from_spreadsheet(sheet["path"])
        positions += found
        notes += sheet_notes
    if positions:
        out["applied"] = apply(positions)
        out["notes"].append(
            f"Your holdings were updated from {', '.join(s['filename'] for s in sheets)}: "
            + ("; ".join(out["applied"]) if out["applied"] else "nothing had changed")
        )
    if pictures:
        found, picture_notes, usage = from_picture(pictures, client=client)
        notes += picture_notes
        out["usage"] = usage
        if found:
            changes = diff(load_holdings(), found)
            save_pending(found, changes, ", ".join(p["filename"] for p in pictures), day)
            out["pending"] = changes
            out["notes"].append(
                "Read from "
                + ", ".join(p["filename"] for p in pictures)
                + ": "
                + ("; ".join(changes) if changes else "no change against what's on file")
                + '. Reply "confirmed" to apply it.'
            )
    out["notes"] += notes
    return out


def apply_pending(day: str) -> list:
    """Applies the transcription he confirmed."""
    pending = load_pending()
    if not pending:
        return []
    changed = apply(pending["positions"])
    clear_pending()
    return changed
