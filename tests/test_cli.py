import json

import pytest

from position_watch import cli


def test_check_env_never_prints_values(monkeypatch, capsys):
    monkeypatch.setenv("FMP_API_KEY", "fmp-secret-value")
    monkeypatch.setenv("FINNHUB_API_KEY", "finnhub-secret-value")
    cli.main(["check-env"])
    out = capsys.readouterr().out
    assert "FMP_API_KEY: set" in out and "DASHBOARD_PASSCODE: MISSING" in out
    assert "secret-value" not in out


def test_check_env_fails_when_a_required_key_is_missing(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["check-env"])
    assert "FMP_API_KEY" in str(exc.value)


def test_log_suggestions_appends_every_scope(workspace):
    cli.main(["log-suggestions"])
    rows = (workspace / "state" / "suggestion_log.csv").read_text().splitlines()
    assert rows[-4:] == [
        "2026-01-02,holding,USDS,hold,Fairly valued.",
        "2026-01-02,holding,EURS,add,Cheap.",
        "2026-01-02,etf,ETFX,top_up,Below its average.",
        "2026-01-02,candidate,AAA,buy,Discount and yield.",
    ]


def test_handoff_save_and_show(monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"date": "2026-01-02", "notes_for_tomorrow": ["x"]})))
    cli.main(["handoff", "save"])
    capsys.readouterr()
    cli.main(["handoff"])
    assert json.loads(capsys.readouterr().out)["notes_for_tomorrow"] == ["x"]


def test_pnl_command_prints_summary_line(capsys):
    cli.main(["pnl"])
    assert json.loads(capsys.readouterr().out)["summary_line"].startswith("Portfolio value")
