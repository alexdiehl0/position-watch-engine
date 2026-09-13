import json

from position_watch import people


def test_recipients_and_inbox():
    assert people.recipients() == ["operator@example.com", "Client@Example.com"]
    assert people.feedback_inbox() == "operator@example.com"


def test_who_sent_is_case_insensitive_and_rejects_strangers():
    assert people.who_sent("client@example.COM") == {"name": "Client", "role": "client"}
    assert people.who_sent("stranger@example.com") is None


def test_update_preferences_records_history(workspace):
    line = people.update_preferences(
        {"avoid": {"add": ["tobacco"]}, "risk_tolerance": "moderate"},
        source="feedback m1 from Client",
        when="2026-01-03",
    )

    saved = json.loads((workspace / "config" / "people.json").read_text())
    prefs = next(p for p in saved["people"] if p["role"] == "client")["preferences"]
    assert prefs["avoid"] == ["tobacco"]
    assert prefs["risk_tolerance"] == "moderate"
    assert prefs["history"][-1] == {"date": "2026-01-03", "change": line, "source": "feedback m1 from Client"}
