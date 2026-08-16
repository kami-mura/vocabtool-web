"""连续学习天数（streak）测试：今天有记录从今天往前数，今天没学但昨天有则从昨天数。"""

import datetime as dt

from app.db import SessionLocal
from app.models import Card, ReviewLog, User
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
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "good",
            "action_id": f"streak-{word}-{card_type}",
            "expected_revision": card["revision"],
        },
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


def test_dashboard_due_today_excludes_buried_cards(client):
    register(client, "buried-dashboard@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    db = SessionLocal()
    try:
        user_id = _user_id("buried-dashboard@example.com")
        row = db.query(Card).filter(Card.user_id == user_id).one()
        row.state = "review"
        row.due_at = dt.datetime.now(dt.timezone.utc).replace(
            tzinfo=None
        ) - dt.timedelta(days=1)
        row.buried = True
        db.commit()
    finally:
        db.close()
    data = client.get("/api/dashboard").json()
    assert data["due_today"] == 0


def test_dashboard_again_pending_counts_learning_cards(client):
    register(client, "again-dashboard@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]
    again = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "again",
            "action_id": "again-dashboard-action",
            "expected_revision": card["revision"],
        },
    )
    assert again.status_code == 200
    assert again.json()["card"]["is_learning"] is True
    data = client.get("/api/dashboard").json()
    assert data["again_pending"] >= 1


def test_study_date_expr_compiles_sqlite_dialect(monkeypatch):
    from zoneinfo import ZoneInfo

    from sqlalchemy import column
    from sqlalchemy.dialects import sqlite

    from app import config as app_config
    from app.routes.dashboard_routes import _study_date_expr

    reviewed_at = column("reviewed_at")
    tz = ZoneInfo("America/New_York")
    offset_hours = tz.utcoffset(dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600
    monkeypatch.setattr(app_config, "APP_TIMEZONE", "America/New_York")
    monkeypatch.setattr(app_config, "DATABASE_URL", "sqlite:///test.db")
    sqlite_sql = str(
        _study_date_expr(reviewed_at).compile(
            dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert f"{offset_hours:+g} hours" in sqlite_sql
    assert "+-" not in sqlite_sql


def test_config_rejects_non_sqlite_database_url():
    import os
    import subprocess
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql://user:pass@localhost/db",
        "VOCABFLOW_SKIP_DOTENV": "true",
    }
    result = subprocess.run(
        [sys.executable, "-c", "from app import config"],
        env=env,
        cwd=project_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "仅支持 SQLite" in result.stderr


def test_dashboard_streak_works_in_negative_offset_timezone(client, monkeypatch):
    from app import config as app_config

    register(client, "streak-negative-offset@example.com")
    monkeypatch.setattr(app_config, "APP_TIMEZONE", "America/New_York")
    _review_new_card(client, "run")
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    assert response.json()["consecutive_study_days"] == 1
