import json

from position_watch import init


def test_init_creates_a_complete_portfolio_repo(tmp_path):
    written = init.create(tmp_path / "mine")
    root = tmp_path / "mine"
    for expected in (".github/workflows/daily.yml", ".github/workflows/monthly.yml", ".gitignore", ".env.example",
                     "README.md", "config/people.json", "config/instruments.json", "config/stock_universe.json",
                     "data/processed/holdings.csv"):  # fmt: skip
        assert expected in written
    assert (root / "state").is_dir() and (root / "reports").is_dir()
    assert json.loads((root / "config/people.json").read_text())["people"][0]["email"] == "you@example.com"
    assert ".env" in (root / ".gitignore").read_text().split()


def test_init_never_overwrites(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "people.json").write_text("{}")
    assert "config/people.json" not in init.create(tmp_path)
    assert (tmp_path / "config" / "people.json").read_text() == "{}"
