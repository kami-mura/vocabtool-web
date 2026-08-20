"""学习热力图测试：起始日从注册日算起、老用户封顶 365 天、新学/复习按卡片去重。"""

import datetime as dt

from app.db import SessionLocal
from app.models import Card, ReviewLog, User
from tests.conftest import register


def _user_row(email):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email).one()
    finally:
        db.close()


def _user_id(email):
    return _user_row(email).id


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


def _review_new_card(client, word, action_id="heatmap-action"):
    made = client.post(
        "/api/card-studio/cards",
        json={"words": [word], "card_type": "general"},
    )
    assert made.status_code == 200
    client.put("/api/cards/settings", json={"new_cards_per_day": 10})
    card = client.get("/api/cards").json()["new"][0]
    reviewed = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "good",
            "action_id": action_id,
            "expected_revision": card["revision"],
        },
    )
    assert reviewed.status_code == 200
    return card["id"]


def _heatmap(client):
    return client.get("/api/dashboard").json()["heatmap_days"]


def test_heatmap_new_user_has_single_zero_day(client):
    register(client, "heatmap-new@example.com")
    days = _heatmap(client)
    assert len(days) == 1
    assert days[0]["total"] == 0
    assert days[0]["new_count"] == 0
    assert days[0]["review_count"] == 0


def test_heatmap_days_ascending_with_zeros(client):
    register(client, "heatmap-asc@example.com")
    _review_new_card(client, "run", action_id="heatmap-asc-run")
    _review_new_card(client, "set", action_id="heatmap-asc-set")
    # 注册日提前到 10 天前，热力图窗口从注册日到今天共 11 天。
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "heatmap-asc@example.com").one()
        user.created_at = dt.datetime.now(dt.timezone.utc).replace(
            tzinfo=None
        ) - dt.timedelta(days=10)
        db.commit()
    finally:
        db.close()
    user_id = _user_id("heatmap-asc@example.com")
    _set_review_dates(user_id, 3, 1)
    days = _heatmap(client)
    assert len(days) == 11
    assert [d["date"] for d in days] == sorted(d["date"] for d in days)
    counts = {d["date"]: d["total"] for d in days}
    assert sum(1 for v in counts.values() if v == 0) == 9
    assert counts[days[-2]["date"]] == 1
    assert counts[days[-4]["date"]] == 1


def test_heatmap_new_card_repeat_review_counts_once_as_new(client):
    register(client, "heatmap-dedup@example.com")
    card_id = _review_new_card(client, "run")
    # 同一天对同一张卡再复习一次（is_new=False），新学只算一次。
    db = SessionLocal()
    try:
        user_id = _user_id("heatmap-dedup@example.com")
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        db.add(
            ReviewLog(
                user_id=user_id,
                card_id=card_id,
                rating="good",
                is_new=False,
                reviewed_at=now,
            )
        )
        db.commit()
    finally:
        db.close()
    days = _heatmap(client)
    assert len(days) == 1
    day = days[0]
    assert day["new_count"] == 1
    assert day["review_count"] == 0
    assert day["total"] == 1
    assert day["total"] == day["new_count"] + day["review_count"]


def test_heatmap_review_only_day_counts_review(client):
    register(client, "heatmap-review@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    db = SessionLocal()
    try:
        user_id = _user_id("heatmap-review@example.com")
        card_id = db.query(Card.id).filter(Card.user_id == user_id).scalar()
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        db.add(
            ReviewLog(
                user_id=user_id,
                card_id=card_id,
                rating="good",
                is_new=False,
                reviewed_at=now,
            )
        )
        db.commit()
    finally:
        db.close()
    day = _heatmap(client)[0]
    assert day["new_count"] == 0
    assert day["review_count"] == 1
    assert day["total"] == 1


def test_heatmap_caps_365_days_for_old_user(client):
    register(client, "heatmap-old@example.com")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "heatmap-old@example.com").one()
        user.created_at = dt.datetime.now(dt.timezone.utc).replace(
            tzinfo=None
        ) - dt.timedelta(days=500)
        db.commit()
    finally:
        db.close()
    days = _heatmap(client)
    assert len(days) == 365
    today = dt.date.today().isoformat()
    assert days[-1]["date"] == today
    assert days[0]["date"] == (dt.date.today() - dt.timedelta(days=364)).isoformat()


def test_heatmap_respects_site_timezone(client, monkeypatch):
    from zoneinfo import ZoneInfo

    from app import config as app_config

    register(client, "heatmap-tz@example.com")
    monkeypatch.setattr(app_config, "APP_TIMEZONE", "America/New_York")
    _review_new_card(client, "run", action_id="heatmap-tz-action")
    days = _heatmap(client)
    tz = ZoneInfo("America/New_York")
    expected = dt.datetime.now(tz).date().isoformat()
    assert days[-1]["date"] == expected
    assert days[-1]["total"] == 1
