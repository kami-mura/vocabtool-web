import datetime as dt
import time

from app.db import SessionLocal
from app.models import Card, ReviewLog, SentenceRefreshPreference, User
from app.sentence_refresh import (
    find_due_cards,
    get_preference,
    refresh_cards,
    set_preference,
)
from tests.conftest import register


def _user_id(email: str) -> int:
    db = SessionLocal()
    try:
        return db.query(User).filter(User.email == email).one().id
    finally:
        db.close()


def _make_reading_card(db, user_id: int, word: str) -> Card:
    card = Card(
        user_id=user_id,
        word=word,
        card_type="reading",
        front=f"The {word} appears in the original test sentence.",
        back=f"释义：{word}",
        context=f"The {word} appears in the original test sentence.",
        state="review",
        due_at=None,
        reps=3,
    )
    db.add(card)
    db.commit()
    return card


def _add_reviews(db, user_id: int, card_id: int, timestamps: list[dt.datetime]) -> None:
    for reviewed_at in timestamps:
        db.add(
            ReviewLog(
                user_id=user_id,
                card_id=card_id,
                rating="good",
                reviewed_at=reviewed_at,
            )
        )
    db.commit()


def _create_pref(db, user_id: int, interval: int, enabled_at: dt.datetime) -> None:
    db.add(
        SentenceRefreshPreference(
            user_id=user_id,
            interval=interval,
            enabled_at=enabled_at,
        )
    )
    db.commit()


def test_preference_default_off(client):
    register(client, "pref-default@example.com")
    user_id = _user_id("pref-default@example.com")
    db = SessionLocal()
    try:
        assert get_preference(db, user_id) is None
    finally:
        db.close()
    res = client.get("/api/cards/sentence-refresh-preference")
    assert res.status_code == 200
    assert res.json()["interval"] == 0


def test_set_preference_enable_resets_enabled_at(client):
    register(client, "pref-enable@example.com")
    user_id = _user_id("pref-enable@example.com")
    db = SessionLocal()
    try:
        pref = set_preference(db, user_id, 0)
        assert pref.interval == 0
        assert pref.enabled_at is None

        pref = set_preference(db, user_id, 3)
        assert pref.interval == 3
        assert pref.enabled_at is not None
        first_enabled = pref.enabled_at

        pref = set_preference(db, user_id, 0)
        assert pref.enabled_at == first_enabled  # 关闭不清除记录

        time.sleep(0.01)
        pref = set_preference(db, user_id, 5)
        assert pref.enabled_at > first_enabled  # 重新开启时重置起点
    finally:
        db.close()


def test_find_due_cards_counts_distinct_days(client):
    register(client, "due-cards@example.com")
    user_id = _user_id("due-cards@example.com")
    db = SessionLocal()
    try:
        enabled_at = dt.datetime(2026, 8, 9, 0, 0, 0)
        _create_pref(db, user_id, 3, enabled_at)

        due_card = _make_reading_card(db, user_id, "alpha")
        not_due_card = _make_reading_card(db, user_id, "beta")
        _add_reviews(
            db,
            user_id,
            due_card.id,
            [
                dt.datetime(2026, 8, 10, 9, 0, 0),
                dt.datetime(2026, 8, 11, 9, 0, 0),
                dt.datetime(2026, 8, 12, 9, 0, 0),
            ],
        )
        # 同一天多次复习只算一天：只有 2 个不同日期，不满足 3 天
        _add_reviews(
            db,
            user_id,
            not_due_card.id,
            [
                dt.datetime(2026, 8, 10, 9, 0, 0),
                dt.datetime(2026, 8, 11, 9, 0, 0),
                dt.datetime(2026, 8, 11, 20, 0, 0),
            ],
        )

        now = dt.datetime(2026, 8, 13, 4, 0, 0)
        cards = find_due_cards(
            db,
            user_id,
            enabled_at=enabled_at,
            interval=3,
            limit=10,
            recent_hours=48,
            now=now,
        )
        assert [c.word for c in cards] == ["alpha"]
    finally:
        db.close()


def test_refresh_cards_updates_front_and_context(client):
    register(client, "refresh-cards@example.com")
    user_id = _user_id("refresh-cards@example.com")
    db = SessionLocal()
    try:
        card = _make_reading_card(db, user_id, "gamma")
        updated, errors = refresh_cards(db, user_id, [card])
        assert updated == 1
        assert errors == []
        db.refresh(card)
        assert "gamma" in card.front
        assert "original test sentence" not in card.front
        assert card.context != "The gamma appears in the original test sentence."
    finally:
        db.close()


def test_api_single_card_refresh(client):
    register(client, "single-refresh@example.com")
    user_id = _user_id("single-refresh@example.com")
    db = SessionLocal()
    try:
        card = _make_reading_card(db, user_id, "zeta")
    finally:
        db.close()

    res = client.post(f"/api/cards/{card.id}/refresh-sentence")
    assert res.status_code == 200
    assert res.json()["updated"] == 1

    db = SessionLocal()
    try:
        card_now = db.get(Card, card.id)
        assert "zeta" in card_now.front
        assert "original test sentence" not in card_now.front
    finally:
        db.close()


def test_api_save_preference_and_manual_refresh(client):
    register(client, "api-refresh@example.com")
    user_id = _user_id("api-refresh@example.com")

    res = client.put(
        "/api/cards/sentence-refresh-preference",
        json={"interval": 5},
    )
    assert res.status_code == 200
    assert res.json()["interval"] == 5

    got = client.get("/api/cards/sentence-refresh-preference")
    assert got.json()["interval"] == 5

    db = SessionLocal()
    try:
        pref = get_preference(db, user_id)
        card = _make_reading_card(db, user_id, "delta")
        _add_reviews(
            db,
            user_id,
            card.id,
            [
                pref.enabled_at + dt.timedelta(days=1),
                pref.enabled_at + dt.timedelta(days=2),
            ],
        )
    finally:
        db.close()

    refreshed = client.post("/api/cards/refresh-sentences")
    assert refreshed.status_code == 200
    assert refreshed.json()["updated"] >= 1


def test_cli_refresh_command(client):
    from app.cli import _cmd_refresh_reading_sentences

    register(client, "cli-refresh@example.com")
    user_id = _user_id("cli-refresh@example.com")
    db = SessionLocal()
    try:
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        enabled_at = now - dt.timedelta(days=4)
        _create_pref(db, user_id, 3, enabled_at)
        card = _make_reading_card(db, user_id, "epsilon")
        _add_reviews(
            db,
            user_id,
            card.id,
            [
                now - dt.timedelta(days=3),
                now - dt.timedelta(days=2),
                now - dt.timedelta(days=1),
            ],
        )
    finally:
        db.close()

    code = _cmd_refresh_reading_sentences(limit=10, recent_hours=48)
    assert code == 0

    db = SessionLocal()
    try:
        card_now = db.query(Card).filter(Card.word == "epsilon").one()
        assert "epsilon" in card_now.front
        assert "original test sentence" not in card_now.front
    finally:
        db.close()
