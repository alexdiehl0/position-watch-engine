from datetime import date

from position_watch.analysis import pool


def test_score_rewards_discount_and_yield():
    score, why = pool._score(pe=10, median_pe=20, yld=4, payout=50)
    assert score == 1.5  # 50% discount -> +1, 4% yield -> +0.5
    assert "50% below" in why


def test_no_value_credit_above_pe_40():
    score, why = pool._score(pe=60, median_pe=200, yld=None, payout=None)
    assert score == 0
    assert "but over 40" in why


def test_stretched_payout_is_penalised():
    covered, _ = pool._score(pe=15, median_pe=15, yld=6, payout=60)
    stretched, why = pool._score(pe=15, median_pe=15, yld=6, payout=120)
    assert stretched == covered - 0.5
    assert "over 100%" in why


def test_loss_makers_score_negative():
    score, why = pool._score(pe=-5, median_pe=12, yld=None, payout=None)
    assert score < 0
    assert "loss-making" in why


def test_pick_takes_top_scores_then_rotation():
    universe = pool.load_universe()
    stocks = {
        s: {"screen": {"passed": True, "score": sc}}
        for s, sc in {"AAA": 1.5, "BBB": 1.0, "CCC": 0.5, "DDD": 0.1}.items()
    }
    stocks["CCC"]["shortlisted"] = {"times": 3, "first": "2026-01-01", "last": "2026-01-05"}
    today = date(2026, 1, 10)

    picks = pool.pick({"stocks": stocks}, universe, exclude={"AAA"}, today=today)

    assert picks[:2] == [("BBB", "top"), ("CCC", "top")]
    assert picks[2] == ("DDD", "rotation")  # never shortlisted, so it comes first in rotation
    assert stocks["BBB"]["shortlisted"] == {"times": 1, "first": "2026-01-10", "last": "2026-01-10"}


def test_refresh_is_due_after_refresh_days():
    universe = pool.load_universe()
    assert pool.needs_refresh({"refreshed": None}, universe, date(2026, 1, 10))
    assert not pool.needs_refresh({"refreshed": "2026-01-05"}, universe, date(2026, 1, 10))
    assert pool.needs_refresh({"refreshed": "2026-01-03"}, universe, date(2026, 1, 10))
