"""Single source of the client-facing note and action labels.

Suggestions use plain Buy / Add / Hold / Trim / Sell labels (Top up / Hold
for the ETFs), and every email, report and dashboard view ends with NOTE.
While this is a private tool for one client, the short note is enough; before
sharing it more widely, restore the fuller framing (CLAUDE.md, roadmap 1).
Suggestions are stored as lower-case codes (state/suggestions.json,
state/suggestion_log.csv); `label()` turns a code into its display label.
"""

NOTE = "Not financial advice. For information only; decisions are yours."

ACTION_LABELS = {"buy": "Buy", "add": "Add", "top_up": "Top up", "hold": "Hold", "trim": "Trim", "sell": "Sell"}


def label(code):
    return ACTION_LABELS.get((code or "").strip().lower(), "n/a")
