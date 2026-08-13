import datetime as dt
import io
import json
import sqlite3
import zipfile

from app.anki_exchange import import_parsed, parse_apkg
from app.db import SessionLocal
from app.models import AnkiReviewLog, Card, ReviewLog, User
from tests.conftest import register


def _parsed_card(guid: str, front: str) -> dict:
    return {
        "anki_guid": guid,
        "word": "durable",
        "card_type": "reading",
        "front": front,
        "back": "adj. able to last a long time",
        "context": "",
        "state": "review",
        "due_at": dt.datetime(2026, 8, 20, 0, 0),
        "interval_days": 8,
        "ease": 2.5,
        "learning_step": 0,
        "reps": 3,
        "lapses": 0,
        "buried": False,
        "modified_at": dt.datetime(2026, 8, 12, 0, 0),
        "reviews": [],
    }


def test_same_package_duplicate_anki_guid_is_not_silently_merged(client):
    register(client, email="dup-guid@example.com")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "dup-guid@example.com").one()
        parsed = {
            "cards": [
                _parsed_card("shared-guid:0", "first front"),
                _parsed_card("shared-guid:0", "second front"),
            ]
        }
        result = import_parsed(db, user.id, parsed)
        db.commit()
        assert result == {
            "created": 1,
            "updated": 0,
            "progress_kept": 0,
            "conflicts": 1,
            "histories": 0,
        }
        cards = db.query(Card).filter(Card.user_id == user.id).all()
        assert len(cards) == 1
        assert cards[0].front == "first front"
        assert cards[0].anki_guid == "shared-guid:0"
    finally:
        db.close()


def _scheduled_card(email: str, *, word: str = "durable", front=None) -> Card:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        card = Card(
            user_id=user.id,
            word=word,
            card_type="reading",
            front=front or f"A {word} design lasts for years.",
            back="adj. able to last a long time | 耐用的",
            context=f"A {word} design lasts for years.",
            state="review",
            due_at=dt.datetime(2026, 8, 20, 0, 0),
            interval_days=8,
            ease=2.4,
            reps=3,
            lapses=1,
        )
        db.add(card)
        db.flush()
        db.add(
            ReviewLog(
                user_id=user.id,
                card_id=card.id,
                rating="good",
                is_new=False,
                interval_days=4,
                ease=2.4,
                previous_state="review",
                reviewed_at=dt.datetime(2026, 8, 12, 1, 2, 3),
            )
        )
        db.commit()
        db.refresh(card)
        return card
    finally:
        db.close()


def _export_package(client) -> bytes:
    response = client.get("/api/cards/anki/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-disposition"] == 'attachment; filename="vocabtool.apkg"'
    return response.content


def test_export_renders_target_word_and_uses_vocabtool_name(client, tmp_path):
    register(client)
    _scheduled_card(
        "alice@example.com",
        word="dole",
        front="After losing his job, he had to rely on the **dole** for several months.",
    )

    package = _export_package(client)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        collection_path = tmp_path / "collection.anki2"
        collection_path.write_bytes(archive.read("collection.anki2"))
    connection = sqlite3.connect(str(collection_path))
    try:
        note_mod, raw_fields = connection.execute("SELECT mod, flds FROM notes").fetchone()
        fields = raw_fields.split("\x1f")
        models_raw, decks_raw = connection.execute("SELECT models, decks FROM col").fetchone()
    finally:
        connection.close()

    assert fields[1] == (
        'After losing his job, he had to rely on the '
        '<span class="target-word">dole</span> for several months.'
    )
    assert "**" not in fields[1]
    assert note_mod == 1_786_665_600
    model = next(iter(json.loads(models_raw).values()))
    deck = next(iter(json.loads(decks_raw).values()))
    assert model["name"] == "vocabtool"
    assert ".target-word { color: #2f6fed; font-weight: 700; }" in model["css"]
    assert ".nightMode .target-word { color: #8fb0f8; }" in model["css"]
    assert deck["name"] == "vocabtool"


def test_apkg_export_contains_schedule_and_review_history(client):
    register(client)
    source = _scheduled_card("alice@example.com")

    package = _export_package(client)
    parsed = parse_apkg(package, 100)

    assert parsed["review_count"] == 1
    assert len(parsed["cards"]) == 1
    card = parsed["cards"][0]
    assert card["word"] == source.word
    assert card["card_type"] == "reading"
    assert card["state"] == "review"
    assert card["interval_days"] == 8
    assert card["reps"] == 3
    assert card["lapses"] == 1
    assert card["reviews"][0]["ease"] == 3
    assert card["reviews"][0]["last_interval_days"] == 4


def test_apkg_round_trip_is_idempotent_and_keeps_history(client):
    register(client)
    _scheduled_card("alice@example.com")
    package = _export_package(client)

    register(client, email="bob@example.com")
    first = client.post(
        "/api/cards/anki/import?filename=cards.apkg",
        content=package,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert first.status_code == 200, first.text
    assert first.json() == {
        "ok": True,
        "created": 1,
        "updated": 0,
        "progress_kept": 0,
        "conflicts": 0,
        "histories": 1,
    }

    second = client.post(
        "/api/cards/anki/import?filename=cards.apkg",
        content=package,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["created"] == 0
    assert second.json()["updated"] == 1
    assert second.json()["histories"] == 0

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "bob@example.com").one()
        card = db.query(Card).filter(Card.user_id == user.id).one()
        assert card.reps == 3
        assert card.interval_days == 8
        assert card.anki_guid
        assert db.query(AnkiReviewLog).filter(AnkiReviewLog.user_id == user.id).count() == 1
    finally:
        db.close()


def test_import_does_not_overwrite_newer_local_progress(client):
    register(client)
    _scheduled_card("alice@example.com")
    package = _export_package(client)
    register(client, email="bob@example.com")
    imported = client.post(
        "/api/cards/anki/import?filename=cards.apkg",
        content=package,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert imported.status_code == 200, imported.text

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "bob@example.com").one()
        card = db.query(Card).filter(Card.user_id == user.id).one()
        card.reps = 77
        card.lapses = 9
        card.interval_days = 30
        card.due_at = dt.datetime(2026, 9, 12, 0, 0)
        card.front = "用户保留的本地正面"
        card.back = "用户保留的本地背面"
        db.add(
            ReviewLog(
                user_id=user.id,
                card_id=card.id,
                rating="easy",
                interval_days=20,
                ease=2.5,
                previous_state="review",
                reviewed_at=dt.datetime(2026, 8, 13, 12, 0, 0),
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/cards/anki/import?filename=older.apkg",
        content=package,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["progress_kept"] == 1
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "bob@example.com").one()
        card = db.query(Card).filter(Card.user_id == user.id).one()
        assert card.reps == 77
        assert card.lapses == 9
        assert card.interval_days == 30
        assert card.due_at == dt.datetime(2026, 9, 12, 0, 0)
        assert card.front == "用户保留的本地正面"
        assert card.back == "用户保留的本地背面"
    finally:
        db.close()


def test_existing_scheduled_card_without_logs_is_not_reset(client):
    register(client)
    _scheduled_card("alice@example.com")
    package = _export_package(client)
    register(client, email="bob@example.com")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "bob@example.com").one()
        db.add(
            Card(
                user_id=user.id,
                word="durable",
                card_type="reading",
                front="本地正面",
                back="本地背面",
                state="review",
                due_at=dt.datetime(2026, 9, 1),
                interval_days=20,
                reps=3,
                lapses=2,
            )
        )
        db.commit()
    finally:
        db.close()

    response = client.post(
        "/api/cards/anki/import?filename=same-reps.apkg",
        content=package,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["progress_kept"] == 1
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "bob@example.com").one()
        card = db.query(Card).filter(Card.user_id == user.id).one()
        assert card.front == "本地正面"
        assert card.back == "本地背面"
        assert card.due_at == dt.datetime(2026, 9, 1)
        assert card.interval_days == 20
        assert card.reps == 3
        assert card.lapses == 2
    finally:
        db.close()


def test_unsupported_package_rolls_back_without_touching_cards(client):
    register(client)
    original = _scheduled_card("alice@example.com", word="untouched")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("collection.anki21b", b"unsupported")
        archive.writestr("media", "{}")

    response = client.post(
        "/api/cards/anki/import?filename=modern.apkg",
        content=output.getvalue(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 400
    assert "支持旧版 Anki" in response.json()["detail"]

    db = SessionLocal()
    try:
        card = db.get(Card, original.id)
        assert card is not None
        assert card.word == "untouched"
        assert card.reps == 3
        assert db.query(Card).count() == 1
    finally:
        db.close()


def test_import_error_after_insert_rolls_back_entire_batch(client, monkeypatch):
    register(client)

    def invalid_parsed_package(_data, _max_cards):
        return {
            "review_count": 1,
            "cards": [
                {
                    "anki_guid": "rollback-guid:0",
                    "word": "rollback",
                    "card_type": "anki",
                    "front": "rollback front",
                    "back": "rollback back",
                    "context": "{}",
                    "state": "review",
                    "due_at": dt.datetime(2026, 8, 20),
                    "interval_days": 5,
                    "ease": 2.5,
                    "learning_step": 0,
                    "reps": 1,
                    "lapses": 0,
                    "buried": False,
                    "modified_at": dt.datetime(2026, 8, 12),
                    "reviews": [
                        {
                            "id": 1,  # 非法的 Anki 毫秒时间戳；在 flush 后触发错误。
                            "ease": 3,
                            "interval_days": 5,
                            "last_interval_days": 1,
                            "factor": 2500,
                            "type": 1,
                        }
                    ],
                }
            ],
        }

    monkeypatch.setattr("app.routes.card_routes.anki_exchange.parse_apkg", invalid_parsed_package)
    response = client.post(
        "/api/cards/anki/import?filename=broken.apkg",
        content=b"parsed by test double",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 400

    db = SessionLocal()
    try:
        assert db.query(Card).count() == 0
        assert db.query(AnkiReviewLog).count() == 0
    finally:
        db.close()
