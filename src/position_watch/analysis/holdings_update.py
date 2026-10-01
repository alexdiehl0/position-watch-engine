"""Updating the portfolio from what the client sends in.

He trades, so `holdings.csv` and `transactions.csv` have to follow. He emails
the usual address with subject "Holdings update" and a file -- attached, or a
screenshot pasted into the body. The next run keeps the file in
`data/raw/uploads/` (the audit trail, never edited afterwards), works out what
it means, and that same morning's report, email and dashboard show it.

Two kinds of view arrive:

- **Transactions** (the broker's trade list: symbol, side, date, qty, price,
  total) -- what he actually sends. Each trade not already in
  `transactions.csv` is appended there and netted into `holdings.csv`: a buy
  adds shares and moves the average cost to the weighted average; a sell
  removes shares at the same average cost and closes the position at zero.
- **Positions** (what he holds: symbol, quantity, average cost). Quantities and
  costs are set to what the file shows; a position the file doesn't show is
  left alone, never closed.

A spreadsheet (CSV or XLSX) is read by code and applied straight away. A
screenshot or PDF is transcribed by one Claude call, and code then checks every
trade it read before anything is written: quantity x price against the total the
broker shows beside it, a readable date (not in the future) and side, the
currency, no sale of more than is held, and not already on file. A trade that
passes every check is applied that morning; one that fails any is set aside and
named in the email, never applied. When the page states the broker's own count
of transactions, ours is compared with it after the update. A *positions*
screenshot has no total to check a misread digit against, so it waits for his
"confirmed".

Every outcome -- applied, waiting, unreadable, already on file -- is logged in
`state/holdings_updates.json`, which the email, report and dashboard show.
Nothing is ever inferred: a column that isn't there stays as it was.
"""

import base64
import csv
import json
import re
import zipfile
from datetime import date, datetime, timezone
from xml.etree import ElementTree

from position_watch import llm, settings

QUANTITY = ("quantity", "qty", "shares", "units", "position", "holding")
PRICE = ("avg price", "average price", "avg cost", "average cost", "cost basis", "price paid", "book cost")
TRADE_PRICE = ("price",)
SYMBOL = ("symbol", "ticker", "instrument", "code")
NAME = ("name", "description", "security")
CURRENCY = ("currency", "ccy")
SIDE = ("side", "buy/sell", "direction", "action")
DATE = ("date",)
COMMISSION = ("commission", "fee")
TOTAL = ("total", "amount", "net value")
EXCHANGE = ("exchange", "market", "venue")

TRANSACTION_FIELDS = ["symbol", "name", "exchange", "side", "date", "qty", "price", "price_currency",
                      "commission", "commission_currency", "total", "total_currency"]  # fmt: skip
EVENTS_KEPT = 60

_TEXT = {"type": "string"}
SCHEMA = {
    "type": "object",
    "properties": {
        "view": {
            "type": "string",
            "enum": ["transactions", "positions", "other"],
            "description": "transactions: a list of individual buys/sells with dates. positions: current holdings "
            "with quantities. other: neither (a logo, a chart, an unrelated picture).",
        },
        "rows_stated": {
            "type": "string",
            "description": "The count the page itself states for the list, exactly as shown (e.g. '21 "
            "transactions'), or '' if none is shown.",
        },
        "trades": {
            "type": "array",
            "description": "transactions view only: one per visible trade row, in the order shown.",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "The ticker as shown, e.g. 'ABC'."},
                    "name": _TEXT,
                    "exchange": {"type": "string", "description": "As shown, e.g. 'NYSE', or ''."},
                    "side": {"type": "string", "enum": ["Buy", "Sell", ""]},
                    "date": {"type": "string", "description": "Exactly as shown, e.g. 'Sep 23, 2026'."},
                    "qty": {"type": "string", "description": "As shown, digits and separators only."},
                    "price": {"type": "string", "description": "Price per unit as shown, without the currency."},
                    "price_currency": {"type": "string", "description": "3-letter code shown with the price, or ''."},
                    "commission": {"type": "string", "description": "As shown without the currency, or ''."},
                    "commission_currency": _TEXT,
                    "total": {"type": "string", "description": "As shown, keeping any K/M suffix, e.g. '12.05 K'."},
                    "total_currency": _TEXT,
                },
                "required": [
                    "symbol",
                    "name",
                    "exchange",
                    "side",
                    "date",
                    "qty",
                    "price",
                    "price_currency",
                    "commission",
                    "commission_currency",
                    "total",
                    "total_currency",
                ],  # fmt: skip
                "additionalProperties": False,
            },
        },
        "positions": {
            "type": "array",
            "description": "positions view only: one per position visible; leave a field '' rather than guessing.",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": _TEXT,
                    "name": _TEXT,
                    "quantity": {"type": "string", "description": "Shares held, as shown; '' if not shown."},
                    "avg_price": {"type": "string", "description": "Average cost per share as shown; '' if not shown."},
                    "currency": {"type": "string", "description": "3-letter code if shown, else ''."},
                },
                "required": ["symbol", "name", "quantity", "avg_price", "currency"],
                "additionalProperties": False,
            },
        },
        "unreadable": {"type": "string", "description": "Visible rows that could not be read with certainty, or ''."},
    },
    "required": ["view", "rows_stated", "trades", "positions", "unreadable"],
    "additionalProperties": False,
}

SYSTEM = """You transcribe a screenshot or PDF from someone's brokerage account into data. It is usually either \
the Transactions list (individual buys and sells, with date, quantity, price and total) or the Positions list \
(what is held, with quantity and average cost). Say which in `view`, then copy every visible row of that list.

You copy what is visible and nothing else: no prices from memory, no filled-in blanks, no tidying of odd numbers, \
no arithmetic. Copy numbers digit for digit with their separators; keep a K or M suffix exactly as shown. A row \
that is cut off at the edge of the picture, blurred or ambiguous in any field you need is left out of the list and \
described in `unreadable`. Column headers, the page's own row count, icons and buttons are not rows. Rows that \
lie outside the picture (a list cropped to its newest entries) are not unreadable -- leave `unreadable` empty for \
them; it is only for rows you can see but cannot read with certainty. You never comment on the portfolio."""

# He says "confirmed" (or similar) to apply a positions screenshot. The classifier
# catches the long ways of saying it; these short replies are caught by code.
CONFIRM_WORDS = re.compile(
    r"^\W*(confirm(ed)?|yes|yes,? (please )?(apply|confirm(ed)?)( it)?|correct|that'?s (right|correct)|ok|okay|"
    r"approved?|go ahead)\W*$",
    re.IGNORECASE,
)


# ---- Files ---------------------------------------------------------------


def uploads_dir():
    return settings.data_dir() / "raw" / "uploads"


def save_uploads(feedback: list, day: str) -> list:
    """Keeps every attachment as it arrived (one copy of identical files), never
    overwriting another. Returns [{path, filename, content_type, from}]."""
    saved, seen = [], set()
    for message in feedback:
        for item in message.get("attachments") or []:
            if item["data"] in seen:
                continue
            seen.add(item["data"])
            safe = re.sub(r"[^A-Za-z0-9._-]+", "-", item["filename"])[:60]
            path, n = uploads_dir() / f"{day}-{safe}", 2
            while path.exists() and path.read_bytes() != item["data"]:
                path, n = uploads_dir() / f"{day}-{n}-{safe}", n + 1
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item["data"])
            saved.append({"path": path, "filename": item["filename"], "content_type": item["content_type"],
                          "from": message.get("name") or message.get("from"),
                          "asked": "holdings update" in (message.get("subject") or "").lower()})  # fmt: skip
    return saved


_XLSX = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.sub(r"\d", "", ref):
        n = n * 26 + ord(ch.upper()) - 64
    return n - 1


def _xlsx_rows(path) -> list:
    """The first sheet of an .xlsx as rows of strings, with the standard library only."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{_XLSX}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_XLSX}t")))
        sheets = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        if not sheets:
            return []
        rows = []
        for row in ElementTree.fromstring(z.read(sheets[0])).iter(f"{_XLSX}row"):
            cells = {}
            for c in row.iter(f"{_XLSX}c"):
                v, inline = c.find(f"{_XLSX}v"), c.find(f"{_XLSX}is")
                if c.get("t") == "s" and v is not None:
                    value = shared[int(v.text)]
                elif inline is not None:
                    value = "".join(t.text or "" for t in inline.iter(f"{_XLSX}t"))
                else:
                    value = v.text if v is not None else ""
                cells[_col_index(c.get("r", "A"))] = value or ""
            if cells:
                line = [""] * (max(cells) + 1)
                for i, value in cells.items():
                    line[i] = value
                rows.append(line)
        return rows


def read_table(path) -> list:
    """A CSV or XLSX as a list of {header: value} dicts."""
    if path.suffix.lower() == ".xlsx":
        grid = _xlsx_rows(path)
        if not grid:
            return []
        headers = [h.strip() for h in grid[0]]
        return [dict(zip(headers, r + [""] * (len(headers) - len(r)), strict=False)) for r in grid[1:] if any(r)]
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        return list(csv.DictReader(f))


# ---- Reading numbers, dates and sides -------------------------------------


def _number(value):
    """'1,234.50 EUR' -> 1234.5; '12,50' -> 12.5; anything unreadable -> None (never a guess)."""
    if value is None:
        return None
    text = re.sub(r"[^0-9.,\-]", "", str(value))
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})*,\d{1,2}", text):  # European: 1.234,56
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _amount(value):
    """A total as shown, K/M/B suffix allowed: '12.05 K' -> (12050.0, 5.0), the value and how far
    off the rounding shown could make it. (None, None) if unreadable."""
    text = re.sub(r"\b[A-Z]{3}\b", "", str(value or "").upper()).strip()  # drop a currency code
    match = re.search(r"\d\s*([KMB])\b", text)
    scale = {"K": 1e3, "M": 1e6, "B": 1e9}[match.group(1)] if match else 1.0
    digits = re.sub(r"[KMB]", "", text).strip()
    number = _number(digits)
    if number is None:
        return None, None
    decimals = len(digits.rsplit(".", 1)[1]) if "." in digits and "," not in digits.rsplit(".", 1)[1] else 0
    return number * scale, 0.5 * 10 ** (-decimals) * scale


_DATE_FORMATS = ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%d %b %Y", "%d %B %Y", "%d.%m.%Y",
                 "%Y/%m/%d", "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")  # fmt: skip


def _date(value) -> str | None:
    """An ISO date, or None when it can't be read without guessing (03/04/2026 could be either)."""
    text = " ".join(str(value or "").replace("Sept ", "Sep ").split())
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        a, b, y = (int(x) for x in m.groups())
        if a > 12 >= b:
            return date(y, b, a).isoformat()
        if b > 12 >= a or a == b:
            return date(y, a, b).isoformat()
    return None


def _side(value) -> str | None:
    text = str(value or "").strip().lower()
    if text in ("buy", "bought", "b", "purchase"):
        return "Buy"
    if text in ("sell", "sold", "s", "sale"):
        return "Sell"
    return None


def _fmt(value, decimals: int = 6) -> str:
    """1234567.0 -> '1234567', 152731.085 -> '152731.085' (never scientific notation)."""
    return f"{value:.{decimals}f}".rstrip("0").rstrip(".") if value is not None else ""


def _money(value) -> str:
    return f"{value:,.2f}"


def _when(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%-d %b %Y")


# ---- Trades: read, check, net into holdings ---------------------------------


def check_trade(raw: dict, today: date | None = None) -> tuple[dict | None, str | None]:
    """One row as read -> (trade, None), or (None, why it was set aside)."""
    symbol = (raw.get("symbol") or "").strip().upper()
    label = symbol or "a row"
    side, day = _side(raw.get("side")), _date(raw.get("date"))
    qty, price = _number(raw.get("qty")), _number(raw.get("price"))
    if not symbol:
        return None, "a row with no symbol"
    if not side:
        return None, f"{label}: buy or sell not readable ({raw.get('side') or 'blank'})"
    if not day:
        return None, f"{label}: date not readable ({raw.get('date') or 'blank'})"
    if day > (today or date.today()).isoformat():
        return None, f"{label}: dated {day}, which is in the future"
    if not qty or qty <= 0 or not price or price <= 0:
        return None, f"{label}: quantity or price not readable"
    currency = (raw.get("price_currency") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        return None, f"{label}: the price's currency isn't shown"
    commission = _number(raw.get("commission")) or 0.0
    commission_currency = (raw.get("commission_currency") or "").strip().upper() or currency
    gross = qty * price
    expected = gross + (commission if side == "Buy" else -commission) if commission_currency == currency else gross
    total_shown, rounding = _amount(raw.get("total"))
    total_currency = (raw.get("total_currency") or "").strip().upper() or currency
    if total_shown is not None and total_currency == currency:
        slack = max(rounding, 0.01) + 0.0005 * gross
        if min(abs(abs(total_shown) - expected), abs(abs(total_shown) - gross)) > slack:
            return None, (
                f"{label} {_when(day)}: {_fmt(qty)} × {_money(price)} = {_money(gross)}, which doesn't match "
                f"the total shown ({raw.get('total')}), so the row may have been misread"
            )
    return {
        "symbol": symbol,
        "name": (raw.get("name") or "").strip(),
        "exchange": (raw.get("exchange") or "").strip(),
        "side": side,
        "date": day,
        "qty": qty,
        "price": price,
        "price_currency": currency,
        "commission": commission,
        "commission_currency": commission_currency,
        "total": round(expected, 2),
        "total_currency": currency,
    }, None


def load_transactions() -> list:
    path = settings.transactions_csv()
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _same_trade(a: dict, b: dict) -> bool:
    pa, pb = _number(a.get("price")), _number(b.get("price"))
    qa, qb = _number(a.get("qty")), _number(b.get("qty"))
    if None in (pa, pb, qa, qb):
        return False
    return (
        (a.get("symbol") or "").upper() == (b.get("symbol") or "").upper()
        and (a.get("side") or "").lower() == (b.get("side") or "").lower()
        and str(a.get("date") or "")[:10] == str(b.get("date") or "")[:10]
        and abs(qa - qb) < 1e-6
        and abs(pa - pb) <= 0.0005 * max(pa, pb)
    )


def new_trades(trades: list, on_file: list | None = None) -> tuple[list, list]:
    """(trades not yet in transactions.csv, trades already there). Repeats within `trades` count once."""
    on_file = load_transactions() if on_file is None else on_file
    fresh, known = [], []
    for t in trades:
        if any(_same_trade(t, r) for r in on_file):
            known.append(t)
        elif not any(_same_trade(t, f) for f in fresh):
            fresh.append(t)
    return fresh, known


def trade_line(t: dict) -> str:
    verb = "Bought" if t["side"] == "Buy" else "Sold"
    return f"{verb} {_fmt(t['qty'])} {t['symbol']} at {_money(t['price'])} {t['price_currency']} on {_when(t['date'])}"


def net_trades(trades: list, write: bool = False) -> tuple[list, list, list]:
    """Nets trades into holdings.csv, and appends them to transactions.csv, when
    `write`. Returns (lines saying what changed, problems, trades netted). A trade
    that can't be netted cleanly -- a sale of more than is held, a currency that
    differs from the position's -- is left out and named."""
    current = load_holdings()
    fields = list(current[0]) if current else ["symbol", "name", "exchange", "currency", "quantity_est", "avg_price",
                                              "invested_usd", "status"]  # fmt: skip
    by_symbol = {(r.get("symbol") or "").upper(): r for r in current}
    lines, problems, done = [], [], []
    for t in sorted(trades, key=lambda t: t["date"]):
        row = by_symbol.get(t["symbol"])
        is_open = bool(row) and (row.get("status") or "open") == "open"
        held = (_number(row.get("quantity_est")) or 0.0) if is_open else 0.0
        if row and (row.get("currency") or t["price_currency"]).upper() != t["price_currency"]:
            problems.append(f"{trade_line(t)}: the position is held in {row.get('currency')} — not applied")
            continue
        if t["side"] == "Sell" and t["qty"] > held + 1e-9:
            problems.append(f"{trade_line(t)}: more than the {_fmt(held)} on file — not applied")
            continue
        cost_usd = t["qty"] * t["price"] if t["price_currency"] == "USD" else None
        if t["side"] == "Buy":
            if not row:
                row = dict.fromkeys(fields, "")
                row.update({"symbol": t["symbol"], "name": t["name"], "exchange": t["exchange"],
                            "currency": t["price_currency"]})  # fmt: skip
                current.append(row)
                by_symbol[t["symbol"]] = row
            old_avg = _number(row.get("avg_price")) if held else None
            new_held = held + t["qty"]
            if held and old_avg is None:
                problems.append(f"{t['symbol']}: no average cost on file, so it is left blank")
                new_avg = None
            else:
                new_avg = ((held * old_avg) if held else 0.0) + t["qty"] * t["price"]
                new_avg /= new_held
            invested = (_number(row.get("invested_usd")) if held else 0.0) if "invested_usd" in row else None
            if "invested_usd" in row:
                # Not USD: left blank, and the broker's own figure comes with the next export.
                row["invested_usd"] = _fmt(invested + cost_usd, 2) if None not in (invested, cost_usd) else ""
            row["quantity_est"], row["status"] = _fmt(new_held), "open"
            row["avg_price"] = _fmt(new_avg, 4) if new_avg is not None else ""
            if not held:
                lines.append(f"{trade_line(t)} — new position")
            else:
                moved = f", average cost {_money(old_avg)} → {_money(new_avg)}" if new_avg and old_avg else ""
                lines.append(f"{trade_line(t)} — {_fmt(held)} → {_fmt(new_held)} held{moved}")
        else:
            new_held = max(held - t["qty"], 0.0)
            invested = _number(row.get("invested_usd"))
            if invested is not None and held:
                row["invested_usd"] = _fmt(invested * new_held / held, 2)
            row["quantity_est"] = _fmt(new_held)
            closed = new_held <= 1e-9
            if closed:
                row["quantity_est"], row["status"] = "0", "closed"
                if "allocation_pct" in row:
                    row["allocation_pct"] = ""
            lines.append(f"{trade_line(t)} — {_fmt(held)} → {_fmt(new_held)} held" + (" (closed)" if closed else ""))
        done.append(t)
    if write and done:
        _write_holdings(current, fields)
        _append_transactions(done)
    return lines, problems, done


def _write_holdings(rows: list, fields: list):
    with open(settings.holdings_csv(), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _append_transactions(trades: list):
    """Adds the trades, newest first as the broker lists them; existing rows keep their order."""
    path = settings.transactions_csv()
    existing = load_transactions()
    fields = list(existing[0]) if existing else TRANSACTION_FIELDS
    names = {(r.get("symbol") or "").upper(): r.get("name") for r in load_holdings()}
    added = [
        {
            k: (_fmt(v) if isinstance(v, float) else v)
            for k, v in {**t, "name": names.get(t["symbol"]) or t["name"]}.items()
        }
        for t in trades
    ]  # the name already on file, so one stock reads the same everywhere
    rows = sorted(existing + added, key=lambda r: r.get("date") or "", reverse=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def count_check(rows_stated: str) -> str | None:
    """The broker's own count of transactions against ours (call after the update)."""
    m = re.search(r"(\d[\d,]*)", rows_stated or "")
    if not m:
        return None
    stated, ours = int(m.group(1).replace(",", "")), len(load_transactions())
    if stated == ours:
        return f"Your broker lists {stated} transactions, and so does the portfolio file now."
    if stated > ours:
        return (f"Your broker lists {stated} transactions; the portfolio file has {ours}. Some trades weren't in "
                "the screenshot — please send the rest, or the broker's export.")  # fmt: skip
    return f"Your broker lists {stated} transactions; the portfolio file has {ours}. Alex will check the difference."


# ---- Positions (a holdings view) -------------------------------------------


def _column(headers: list, wanted: tuple):
    for header in headers:
        if any(word in (header or "").strip().lower() for word in wanted):
            return header
    return None


def from_spreadsheet(path, today: date | None = None) -> tuple[list, list, list]:
    """A broker's export (CSV or XLSX): a trade list or a positions list.
    Returns (positions, trades, notes); the trades are already checked."""
    try:
        rows = read_table(path)
    except (OSError, zipfile.BadZipFile, ElementTree.ParseError, KeyError, ValueError, IndexError) as exc:
        return [], [], [f"{path.name}: could not be read ({type(exc).__name__})"]
    if not rows:
        return [], [], [f"{path.name}: no rows"]
    headers = list(rows[0])
    symbol_col, qty_col = _column(headers, SYMBOL), _column(headers, QUANTITY)
    if not symbol_col or not qty_col:
        return [], [], [f"{path.name}: no symbol or quantity column (found {', '.join(headers[:6])})"]
    name_col, currency_col = _column(headers, NAME), _column(headers, CURRENCY)
    side_col, date_col = _column(headers, SIDE), _column(headers, DATE)

    if side_col and date_col:  # a trade list
        cols = {k: _column(headers, words) for k, words in
                (("price", TRADE_PRICE), ("exchange", EXCHANGE), ("commission", COMMISSION), ("total", TOTAL))}  # fmt: skip
        trades, notes = [], []
        for row in rows:
            if not (row.get(symbol_col) or "").strip():
                continue
            raw = {"symbol": row.get(symbol_col), "name": row.get(name_col) if name_col else "",
                   "side": row.get(side_col), "date": row.get(date_col), "qty": row.get(qty_col),
                   "price_currency": row.get(currency_col) if currency_col else "",
                   **{k: (row.get(c) if c else "") for k, c in cols.items()}}  # fmt: skip
            trade, why = check_trade(raw, today)
            if trade:
                trades.append(trade)
            else:
                notes.append(why)
        return [], trades, notes + ([] if trades else ["no trades could be read"])

    price_col = _column(headers, PRICE)
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
    return positions, [], ([] if positions else [f"{path.name}: no positions could be read"])


def from_picture(uploads: list, client=None, mode: str = "direct", today: date | None = None) -> tuple[dict, dict]:
    """Transcribes screenshots or PDFs with one Claude call, then checks every row.
    Returns ({view, rows_stated, positions, trades, set_aside, unreadable}, usage).
    The main model, with thinking: a misread digit in the holdings is expensive and a
    screenshot is a small request."""
    files = [(u["content_type"], base64.b64encode(u["path"].read_bytes()).decode()) for u in uploads]
    user = ("Here " + ("is a file" if len(files) == 1 else f"are {len(files)} files") +
            " from the client's brokerage account. Transcribe every row you can read.")  # fmt: skip
    request = llm.request_with_files(SYSTEM, user, SCHEMA, files, max_tokens=16000)
    message, batched = llm.ask(request, client=client, mode=mode)
    answer = llm.json_answer(message)
    trades, set_aside = [], []
    for raw in answer.get("trades") or []:
        trade, why = check_trade(raw, today)
        if trade:
            trades.append(trade)
        else:
            set_aside.append(why)
    positions = []
    for row in answer.get("positions") or []:
        quantity = _number(row.get("quantity"))
        if row.get("symbol") and quantity is not None:
            positions.append({"symbol": row["symbol"].strip().upper(), "name": (row.get("name") or "").strip(),
                              "quantity": quantity, "avg_price": _number(row.get("avg_price")),
                              "currency": (row.get("currency") or "").strip().upper()})  # fmt: skip
    read = {
        "view": answer.get("view") or "other",
        "rows_stated": answer.get("rows_stated") or "",
        "positions": positions,
        "trades": trades,
        "set_aside": set_aside,
        "unreadable": answer.get("unreadable") or "",
    }
    return read, llm.usage(message, batched)


def _count(rows_stated: str) -> int:
    m = re.search(r"(\d[\d,]*)", rows_stated or "")
    return int(m.group(1).replace(",", "")) if m else -1


def read_pictures(pictures: list, client=None, today: date | None = None) -> tuple[dict, dict]:
    """Each picture read on its own -- two screenshots sent days apart each state their own
    count, and one reading of both can only report one -- then combined. The count kept is
    the largest: the newest screenshot shows the longest list."""
    combined = {"view": "other", "rows_stated": "", "positions": [], "trades": [], "set_aside": [], "unreadable": ""}
    usage, unreadable = {}, []
    for picture in pictures:
        read, used = from_picture([picture], client=client, today=today)
        for key in ("positions", "trades", "set_aside"):
            combined[key] += read[key]
        if read["view"] != "other" and combined["view"] in ("other", read["view"]):
            combined["view"] = read["view"]
        if _count(read["rows_stated"]) > _count(combined["rows_stated"]):
            combined["rows_stated"] = read["rows_stated"]
        if read["unreadable"]:
            unreadable.append(
                f"{picture['filename']}: {read['unreadable']}" if len(pictures) > 1 else read["unreadable"]
            )
        for k, v in (used or {}).items():
            usage[k] = usage.get(k, 0) + v if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    combined["unreadable"] = "; ".join(unreadable)
    return combined, usage


def load_holdings() -> list:
    with open(settings.holdings_csv(), newline="") as f:
        return list(csv.DictReader(f))


def diff(current: list, positions: list) -> list:
    """What a positions view would change, in plain words. Nothing is closed for being absent."""
    by_symbol = {(r.get("symbol") or "").upper(): r for r in current}
    lines, seen = [], set()
    for p in positions:
        seen.add(p["symbol"])
        row = by_symbol.get(p["symbol"])
        if not row:
            lines.append(f"{p['symbol']}: new position, {_fmt(p['quantity'])} shares")
            continue
        was = _number(row.get("quantity_est"))
        if was is not None and abs(was - p["quantity"]) > 0.0001:
            moved = "bought" if p["quantity"] > was else "sold"
            lines.append(
                f"{p['symbol']}: {_fmt(was)} → {_fmt(p['quantity'])} shares ({moved} {_fmt(abs(p['quantity'] - was))})"
            )
        old_price = _number(row.get("avg_price"))
        if p["avg_price"] and old_price and abs(old_price - p["avg_price"]) / old_price > 0.001:
            lines.append(f"{p['symbol']}: average cost {_fmt(old_price)} → {_fmt(p['avg_price'])}")
    missing = [s for s, r in by_symbol.items() if s not in seen and (r.get("status") or "open") == "open"]
    if missing:
        lines.append(f"not in the upload, left unchanged: {', '.join(sorted(missing))}")
    return lines


def apply(positions: list) -> list:
    """Writes a positions view into holdings.csv and returns what changed."""
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
        row["quantity_est"] = _fmt(p["quantity"])
        if p["avg_price"]:
            row["avg_price"] = _fmt(p["avg_price"])
        if p["currency"]:
            row["currency"] = p["currency"]
        row["status"] = "open"
    _write_holdings(current, fields)
    return changed


# ---- A positions screenshot waits for his OK --------------------------------


def _pending_path():
    return settings.state_dir() / "pending_holdings.json"


def save_pending(positions: list, changed: list, source: str, day: str):
    _pending_path().parent.mkdir(parents=True, exist_ok=True)
    _pending_path().write_text(json.dumps({
        "date": day, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": source,
        "positions": positions, "changes": changed,
    }, indent=2) + "\n")  # fmt: skip


def load_pending() -> dict | None:
    path = _pending_path()
    return json.loads(path.read_text()) if path.exists() else None


def clear_pending():
    _pending_path().unlink(missing_ok=True)


def is_confirmation(feedback: list) -> bool:
    """A short "confirmed" / "yes" reply sent after the reading was shown to him."""
    pending = load_pending()
    if not pending:
        return False
    created = pending.get("created") or f"{pending.get('date', '')}T00:00:00+00:00"
    for m in feedback:
        if not CONFIRM_WORDS.match((m.get("text") or "").strip()):
            continue
        try:
            sent = datetime.strptime(m.get("date") or "", "%a, %d %b %Y %H:%M:%S %z").astimezone(timezone.utc)
        except ValueError:
            return True  # an unparseable date doesn't make a clear "yes" less clear
        if sent.isoformat(timespec="seconds") >= created:
            return True
    return False


def apply_pending(day: str) -> list:
    """Applies the positions reading he confirmed."""
    pending = load_pending()
    if not pending:
        return []
    changed = apply(pending.get("positions") or [])
    log_event(day, "confirmed", pending.get("source", ""), changed)
    clear_pending()
    return changed


# ---- What happened, for the email, report and dashboard --------------------


def _events_path():
    return settings.state_dir() / "holdings_updates.json"


def load_events() -> list:
    path = _events_path()
    return json.loads(path.read_text()) if path.exists() else []


def log_event(day: str, status: str, source: str, lines: list, problems: list | None = None,
              checks: list | None = None):  # fmt: skip
    """status: applied | waiting | confirmed | nothing_new | unreadable | error."""
    events = load_events()
    events.append({"date": day, "status": status, "source": source, "lines": list(lines),
                   "problems": list(problems or []), "checks": list(checks or [])})  # fmt: skip
    _events_path().parent.mkdir(parents=True, exist_ok=True)
    _events_path().write_text(json.dumps(events[-EVENTS_KEPT:], indent=2, ensure_ascii=False) + "\n")


HEADINGS = {
    "applied": "Your trades are in — holdings updated",
    "confirmed": "Applied, as you confirmed",
    "waiting": "Read from your screenshot — reply “confirmed” to apply it",
    "nothing_new": "Your update arrived — everything in it was already on file",
    "unreadable": "We couldn’t read your update",
    "error": "Your update couldn’t be processed today",
}
TONE = {"applied": "good", "confirmed": "good", "waiting": "warn", "nothing_new": "none", "unreadable": "crit",
        "error": "crit"}  # fmt: skip


def panel(day: str) -> dict:
    """What to show about trades sent in: today's outcomes, and a reading still waiting for his OK."""
    items = [{**e, "heading": HEADINGS[e["status"]], "tone": TONE[e["status"]]} for e in load_events()
             if e["date"] == day]  # fmt: skip
    pending = load_pending()
    if pending and not any(e["status"] == "waiting" for e in items):
        items.append({"date": pending["date"], "status": "waiting", "source": pending.get("source", ""),
                      "lines": pending.get("changes", []), "problems": [], "checks": [], "tone": "warn",
                      "heading": f"Still waiting for your OK (read on {_when(pending['date'])}) — reply “confirmed”"})  # fmt: skip
    return {"items": items}


def recent_trades(limit: int = 12) -> list:
    """The newest trades on file, for the dashboard."""
    rows = sorted(load_transactions(), key=lambda r: r.get("date") or "", reverse=True)
    out = []
    for r in rows[:limit]:
        qty = _number(r.get("qty"))
        out.append({**r, "qty_text": f"{qty:,.6f}".rstrip("0").rstrip(".") if qty is not None else "–",
                    "price_n": _number(r.get("price")), "total_n": _number(r.get("total"))})  # fmt: skip
    return out


# ---- The whole step ---------------------------------------------------------


def _is_sheet(upload: dict) -> bool:
    media = upload["content_type"]
    return upload["path"].suffix.lower() in (".csv", ".txt", ".xlsx") or "csv" in media or "spreadsheetml" in media


def _apply_trades(day: str, source: str, trades: list, problems: list, rows_stated: str = "") -> list:
    """Applies checked trades and logs the outcome. Returns the lines that changed."""
    fresh, known = new_trades(trades)
    lines, net_problems, done = net_trades(fresh, write=True) if fresh else ([], [], [])
    problems = problems + net_problems
    checks = [c for c in [count_check(rows_stated)] if c]
    if known:
        checks.append(f"{len(known)} trade(s) in it were already on file and were not added again.")
    if done:
        log_event(day, "applied", source, lines, problems, checks)
    elif known and not problems:
        log_event(day, "nothing_new", source, [trade_line(t) for t in known], [], checks)
    else:
        log_event(day, "unreadable", source, [], problems or ["no trades could be read"], checks)
    return lines


def process(feedback: list, day: str | None = None, client=None, today: date | None = None) -> dict:
    """Saves what arrived and applies what passes the checks. Never raises: a file
    that can't be processed is reported to him and kept, never lost.
    Returns {"notes": [...], "applied": [...], "pending": [...], "usage": {...}}."""
    day = day or date.today().isoformat()
    today = today or date.fromisoformat(day)
    out = {"notes": [], "applied": [], "pending": [], "usage": {}}
    try:
        uploads = save_uploads(feedback, day)
    except OSError as exc:
        log_event(day, "error", "", [], [f"the file could not be saved ({type(exc).__name__})"])
        out["notes"].append(f"An emailed holdings file could not be saved: {settings.redact(exc)}")
        return out

    sheets = [u for u in uploads if _is_sheet(u)]
    pictures = [u for u in uploads if not _is_sheet(u)]

    if sheets:
        source = ", ".join(s["filename"] for s in sheets)
        try:
            positions, trades, notes = [], [], []
            for sheet in sheets:
                p, t, n = from_spreadsheet(sheet["path"], today)
                positions, trades, notes = positions + p, trades + t, notes + n
            if trades:
                out["applied"] += _apply_trades(day, source, trades, notes)
            if positions:
                lines = apply(positions)
                out["applied"] += lines
                log_event(day, "applied" if lines else "nothing_new", source, lines, notes)
            if not trades and not positions:
                log_event(day, "unreadable", source, [], notes or ["nothing could be read"])
        except Exception as exc:  # noqa: BLE001 -- reported, never lost
            log_event(day, "error", source, [], ["the file is kept, and Alex has been told"])
            out["notes"].append(f"{source} could not be processed: {settings.redact(f'{type(exc).__name__}: {exc}')}")

    if pictures:
        source = ", ".join(p["filename"] for p in pictures)
        try:
            read, usage = read_pictures(pictures, client=client, today=today)
            out["usage"] = usage
            problems = [f"not applied — {why}" for why in read["set_aside"]]
            if read["unreadable"]:
                problems.append(f"could not be read with certainty: {read['unreadable']}")
            if read["trades"] or (read["view"] == "transactions" and problems):
                out["applied"] += _apply_trades(day, source, read["trades"], problems, read["rows_stated"])
            elif read["positions"]:
                changes = diff(load_holdings(), read["positions"])
                save_pending(read["positions"], changes, source, day)
                out["pending"] = changes
                log_event(day, "waiting", source, changes or ["no change against what's on file"], problems)
            elif read["view"] != "other" or any(p.get("asked") for p in pictures):
                # A logo or a chart in an ordinary reply is not a failed update; only say so when he sent one.
                why = problems or ["no trades or positions could be read from it"]
                if read["view"] == "other":
                    why = ["it doesn't show a transactions or positions list", *problems]
                log_event(day, "unreadable", source, [], why)
        except Exception as exc:  # noqa: BLE001 -- reported, never lost
            log_event(day, "error", source, [], ["the screenshot is kept, and Alex has been told"])
            out["notes"].append(f"{source} could not be read: {settings.redact(f'{type(exc).__name__}: {exc}')}")
    return out
