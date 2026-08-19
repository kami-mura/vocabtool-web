import datetime as dt
import json

import pytest

from app.models import Card
from app.srs import (
    apply_rating,
    apply_rating_with_log,
    forecast_due_counts,
    memory_curve,
    rating_previews,
)


def _card(reps=0, interval=0.0, due=None, state=None):
    c = Card(word="test", card_type="general", front="t", back="t")
    c.id = 1
    c.reps = reps
    c.interval_days = interval
    c.due_at = due
    c.state = state or ("new" if reps == 0 else "review")
    c.lapses = 0
    c.learning_step = 0
    c.session_reduce_day = ""
    c.session_reduce_used = 0
    c.fsrs_state = None
    return c


def test_again_on_new_card_keeps_learning_today():
    now = dt.datetime(2026, 8, 3, 12, 0)
    card = _card()
    apply_rating(card, "again", now=now)
    assert card.reps == 1
    assert card.interval_days == 0.0
    assert card.state == "learning"
    assert card.due_at == now
    assert card.lapses == 0
    assert card.fsrs_state


def test_hard_on_new_card_keeps_learning_today():
    now = dt.datetime(2026, 8, 3, 12, 0)
    card = _card()
    apply_rating(card, "hard", now=now)
    assert card.state == "learning"
    assert card.due_at == now
    assert card.interval_days == 0.0
    assert card.learning_step == 0


def test_good_on_new_card_graduates():
    now = dt.datetime(2026, 8, 3, 12, 0)
    card = _card()
    apply_rating(card, "good", now=now)
    assert card.state == "review"
    assert card.due_at > now
    assert card.interval_days >= 1


def test_easy_on_new_card_graduates():
    now = dt.datetime(2026, 8, 3, 12, 0)
    card = _card()
    apply_rating(card, "easy", now=now)
    assert card.state == "review"
    assert card.due_at > now
    assert card.interval_days >= 1
    assert card.reps == 1


def test_easy_intervals_progress_with_fuzzing():
    now = dt.datetime(2026, 8, 3, 12, 0)
    card = _card()
    intervals = []
    for index in range(6):
        apply_rating(card, "easy", now=now + dt.timedelta(days=index))
        intervals.append(card.interval_days)
    assert all(value >= 1 for value in intervals)
    # FSRS fuzzing can make one interval slightly shorter than the previous one.
    assert intervals[-1] > intervals[0]
    assert len(set(intervals)) >= 3


def test_hard_on_review_card_schedules_future_review():
    now = dt.datetime(2026, 8, 3, 12, 0)
    card = _card(
        reps=3,
        interval=30.0,
        due=now - dt.timedelta(days=1),
        state="review",
    )
    apply_rating(card, "hard", now=now)
    assert card.state == "review"
    assert card.due_at > now
    assert card.interval_days >= 1
    assert card.reps == 4


def test_again_on_review_card_counts_lapse_and_needs_known():
    now = dt.datetime(2026, 8, 3, 12, 0)
    card = _card(
        reps=3,
        interval=30.0,
        due=now - dt.timedelta(days=1),
        state="review",
    )
    apply_rating(card, "again", now=now)
    assert card.state == "learning"
    assert card.due_at == now
    assert card.interval_days == 0.0
    assert card.lapses == 1

    apply_rating(card, "easy", now=now)
    assert card.state == "review"
    assert card.due_at > now
    assert card.interval_days >= 1


def test_again_on_new_card_twice_counts_no_lapse():
    now = dt.datetime(2026, 8, 3, 12, 0)
    card = _card()
    apply_rating(card, "again", now=now)
    apply_rating(card, "again", now=now + dt.timedelta(minutes=1))
    assert card.state == "learning"
    assert card.lapses == 0


def test_again_on_graduated_card_counts_single_lapse():
    now = dt.datetime(2026, 8, 3, 12, 0)
    card = _card()
    apply_rating(card, "easy", now=now)
    assert card.state == "review"
    apply_rating(card, "again", now=now + dt.timedelta(days=1))
    assert card.lapses == 1
    apply_rating(card, "again", now=now + dt.timedelta(days=1, minutes=1))
    assert card.lapses == 1


def test_apply_rating_with_log_returns_fsrs_json():
    now = dt.datetime(2026, 8, 3, 12, 0)
    card, log_json = apply_rating_with_log(_card(), "easy", now=now)
    state = json.loads(card.fsrs_state)
    assert state["state"] == 2  # Review
    assert state["stability"] > 0
    log = json.loads(log_json)
    assert log["rating"] == 4  # Easy


def test_invalid_rating_rejected():
    with pytest.raises(ValueError):
        apply_rating(_card(), "unknown")


def test_forecast_covers_thirty_days_and_counts_due_today():
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    card = _card(reps=1, interval=3.0, due=now)
    counts = forecast_due_counts([card], days=30)
    assert len(counts) == 30
    assert counts[next(iter(sorted(counts)))] >= 1
    assert sum(counts.values()) >= 2


def test_rating_previews_show_four_ratings():
    previews = rating_previews(_card(), now=dt.datetime(2026, 8, 3, 12, 0))
    assert set(previews) == {"again", "hard", "good", "easy"}
    assert previews["again"]["label"] == ""
    assert previews["hard"]["label"] == ""
    assert previews["good"]["label"].endswith("d")
    assert previews["easy"]["label"].endswith("d")
    assert previews["good"]["interval_days"] >= 1
    assert previews["easy"]["interval_days"] >= 1


def test_memory_curve_declines_without_review():
    now = dt.datetime(2026, 8, 3, 12, 0)
    card = _card(
        reps=2,
        interval=6.0,
        due=now + dt.timedelta(days=6),
        state="review",
    )
    curve = memory_curve([card], days=3, now=now)
    assert len(curve) == 3
    assert curve[0]["recall"] > curve[-1]["recall"]
