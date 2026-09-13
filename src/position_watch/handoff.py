"""Deterministic read/write for the day-to-day handoff file.

Each daily review run writes the workspace's state/handoff.json summarizing that day's
suggestions and any notes for the next run. The following day's routine
reads it back (see the routine prompt) so the agent has continuity across
days -- e.g. "yesterday flagged a payout ratio over 100%, did it change?" --
without re-deriving everything from scratch or fabricating memory of past
runs. This module only does file I/O; it never generates or interprets
the content itself.
"""

import json

from position_watch import settings


def handoff_path():
    return settings.state_dir() / "handoff.json"


def load_handoff():
    """Returns the previous handoff dict, or None if this is the first run."""
    if not handoff_path().exists():
        return None
    with open(handoff_path()) as f:
        return json.load(f)


def save_handoff(data: dict):
    handoff_path().parent.mkdir(parents=True, exist_ok=True)
    with open(handoff_path(), "w") as f:
        json.dump(data, f, indent=2, default=str)
        f.write("\n")
