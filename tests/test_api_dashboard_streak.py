"""连续学习天数（streak）测试：今天有记录从今天往前数，今天没学但昨天有则从昨天数。"""

import datetime as dt

from app.db import SessionLocal
from app.models import ReviewLog, User
from tests.conftest import register


def _user_id(email):
    db = SessionLocal()
    try:
        return db.query(User.id).filter(User.email == email).scalar()
    finally:
        db.close()


def _set_review_dates(user_id, *days):
    """把该用户第 i 条复习记录设为 days[i] 天前（本地日期 = 今天 - days[i]）。"""
    db = SessionLocal()
    try:
        logs = (
            db.query(ReviewLog)
            .filter(ReviewLog.user_id == user_id)
            .order_by(ReviewLog.id)
            .all()
        )
        assert len(logs) == len(days), (len(logs), days)
        for log, day_offset in zip(logs, days, strict=True):
            log.reviewed_at = dt.datetime.now(dt.timezone.utc).replace(
                tzinfo=None
            ) - dt.timedelta(days=day_offset)
        db.commit()
    finally:
        db.close()


def _review_new_card(client, word, card_type="general"):
    made = client.post(
        "/api/card-studio/cards",
        json={"words": [word], "card_type": card_type},
    )
    assert made.status_code == 200
    client.put("/api/cards/settings", json={"new_cards_per_day": 10})
    card = client.get("/api/cards").json()["new"][0]
    reviewed = client.post(
        f"/api/cards/{card['id']}/review", json={"rating": "good"}
    )
    assert reviewed.status_code == 200


def _streak(client):
    return client.get("/api/dashboard").json()["consecutive_study_days"]


def test_streak_zero_for_new_user(client):
    register(client, "streak-new@example.com")
    assert _streak(client) == 0


def test_streak_one_after_single_day(client):
    register(client, "streak-one@example.com")
    _review_new_card(client, "run")
    assert _streak(client) == 1


def test_streak_counts_consecutive_days(client):
    register(client, "streak-three@example.com")
    _review_new_card(client, "run")
    _review_new_card(client, "set")
    _review_new_card(client, "point")
    user_id = _user_id("streak-three@example.com")
    _set_review_dates(user_id, 2, 1, 0)
    assert _streak(client) == 3


def test_streak_breaks_on_gap_day(client):
    register(client, "streak-gap@example.com")
    _review_new_card(client, "run")
    _review_new_card(client, "set")
    _review_new_card(client, "point")
    user_id = _user_id("streak-gap@example.com")
    _set_review_dates(user_id, 3, 2, 0)
    assert _streak(client) == 1


def test_streak_alive_when_today_not_studied_yet(client):
    """今天还没学，但昨天、前天都有记录：连续天数从昨天往前数，不断。"""
    register(client, "streak-alive@example.com")
    _review_new_card(client, "run")
    _review_new_card(client, "set")
    user_id = _user_id("streak-alive@example.com")
    _set_review_dates(user_id, 2, 1)
    assert _streak(client) == 2
