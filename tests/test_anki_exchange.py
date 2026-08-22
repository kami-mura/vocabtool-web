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
            # created_at 固定在过去：导出包的 mod 取 max(复习时间, created_at)，
            # 若用真实当前时间，时钟越过下方本地进度的固定时间后
            # 会误判为“包里进度更新”，测试随时间漂移失败。
            created_at=dt.datetime(2026, 8, 1, 0, 0),
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
    assert 'font-family: -apple-system' in model["css"]
    assert '"PingFang SC"' in model["css"]
    assert 'class="card-box"' in model["tmpls"][0]["qfmt"]
    assert 'class="card-box"' in model["tmpls"][0]["afmt"]
    assert 'class="card-front"' in model["tmpls"][0]["qfmt"]
    assert 'class="card-back"' in model["tmpls"][0]["afmt"]
    assert 'hr id="answer"' in model["tmpls"][0]["afmt"]
    assert "font-size: 30px; line-height: 1.7;" in model["css"]
    assert "font-size: 24px; line-height: 1.65;" in model["css"]
    assert "max-width: 620px" in model["css"]
    assert "border-radius: 16px" in model["css"]
    assert "@media (max-width: 600px)" in model["css"]
    assert ".nightMode .card-box { background: #1f2937;" in model["css"]
    assert ".target-word { color: #2f6fed; font-weight: 700; }" in model["css"]
    assert ".nightMode .target-word { color: #8fb0f8; }" in model["css"]
    assert "font-size: 28px;" not in model["css"]
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


def test_apkg_export_preserves_fsrs_memory_state(client, tmp_path):
    register(client)
    source = _scheduled_card("alice@example.com")
    db = SessionLocal()
    try:
        card = db.get(Card, source.id)
        card.fsrs_state = json.dumps(
            {
                "card_id": card.id,
                "state": 2,
                "step": None,
                "stability": 12.345,
                "difficulty": 6.789,
                "due": "2026-08-20T00:00:00+00:00",
                "last_review": "2026-08-12T01:02:03+00:00",
            }
        )
        db.commit()
    finally:
        db.close()

    package = _export_package(client)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        collection_path = tmp_path / "collection.anki2"
        collection_path.write_bytes(archive.read("collection.anki2"))
    connection = sqlite3.connect(str(collection_path))
    try:
        card_data = connection.execute("SELECT data FROM cards").fetchone()[0]
    finally:
        connection.close()

    assert json.loads(card_data) == {
        "s": 12.345,
        "d": 6.789,
        "lrt": 1_786_496_523,
    }


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


def test_import_does_not_overwrite_newer_local_progress(client, monkeypatch):
    del monkeypatch
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


def test_import_schedule_decodes_anki_queue_matrix():
    """导入排程映射矩阵：queue=3 是天数（不是 Unix 秒），暂停新卡保持新卡。"""
    import datetime as dt

    from app.anki_exchange import _import_schedule

    creation = int(dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).timestamp())
    crt_date = dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)

    # 新卡（queue=0）与暂停/掩埋的新卡（queue=-1/-2）都保持新卡语义。
    assert _import_schedule(
        creation_time=creation, card_type=0, queue=0, due=7, interval=0
    ) == ("new", None, 0)
    for negative in (-1, -2):
        assert _import_schedule(
            creation_time=creation, card_type=0, queue=negative, due=3, interval=0
        ) == ("new", None, 0)

    # queue=1 学习卡：due 是 Unix 秒时间戳。
    future_ts = int(
        (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=10)).timestamp()
    )
    state, due_at, _ = _import_schedule(
        creation_time=creation, card_type=1, queue=1, due=future_ts, interval=0
    )
    assert state == "learning"
    assert abs((due_at.replace(tzinfo=dt.timezone.utc) - (
        dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=10)
    )).total_seconds()) < 60

    # queue=3 跨日学习卡：due 是「集合创建起算的天数」，
    # 未来第 5 天到期必须换算到 crt+5 天，而不是导入时刻。
    state, due_at, _ = _import_schedule(
        creation_time=creation, card_type=1, queue=3, due=5, interval=0
    )
    assert state == "learning"
    assert due_at is not None
    expected = crt_date + dt.timedelta(days=5)
    assert abs((due_at.replace(tzinfo=dt.timezone.utc) - expected).total_seconds()) < 1

    # queue=2 复习卡：due 是天数，基于集合创建日。
    state, due_at, _ = _import_schedule(
        creation_time=creation, card_type=2, queue=2, due=30, interval=30
    )
    assert state == "review"
    assert due_at is not None
    expected = crt_date + dt.timedelta(days=30)
    assert abs((due_at.replace(tzinfo=dt.timezone.utc) - expected).total_seconds()) < 1


def test_multi_template_note_imports_every_ordinal_with_history(client):
    """一个 note 的多模板（guid 含 ordinal）必须导入每一张卡和它的历史，
    不再把第二张起判成冲突丢掉（见 docs/审查整改清单.md P1-3）。"""
    register(client, email="multi-template@example.com")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "multi-template@example.com").one()

        def anki_card(guid: str, front: str) -> dict:
            card = _parsed_card(guid, front)
            card["card_type"] = "anki"
            card["reviews"] = [
                {
                    "id": 1770000000000 + len(guid),
                    "ease": 3,
                    "interval_days": 4,
                    "last_interval_days": 1,
                    "factor": 2500,
                    "type": 1,
                }
            ]
            return card

        parsed = {
            "cards": [
                anki_card("note-abc:0", "durable"),
                anki_card("note-abc:1", "able to last a long time"),
            ]
        }
        result = import_parsed(db, user.id, parsed)
        db.commit()
        assert result == {
            "created": 2,
            "updated": 0,
            "progress_kept": 0,
            "conflicts": 0,
            "histories": 2,
        }
        cards = (
            db.query(Card)
            .filter(Card.user_id == user.id)
            .order_by(Card.id)
            .all()
        )
        assert [card.anki_guid for card in cards] == ["note-abc:0", "note-abc:1"]
        assert [card.front for card in cards] == [
            "durable",
            "able to last a long time",
        ]

        # 重复导入幂等：两张卡都按 guid 命中更新，历史不重复。
        repeat = import_parsed(db, user.id, parsed)
        db.commit()
        assert repeat["created"] == 0
        assert repeat["conflicts"] == 0
        assert repeat["histories"] == 0
        assert db.query(Card).filter(Card.user_id == user.id).count() == 2
        logs = db.query(AnkiReviewLog).filter(AnkiReviewLog.user_id == user.id).count()
        assert logs == 2
    finally:
        db.close()


def test_site_card_word_type_dedup_still_enforced(client):
    """放开约束只针对 anki 类型：站内生成卡的 (user, word, type) 去重不变。"""
    register(client, email="site-dedup@example.com")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "site-dedup@example.com").one()
        parsed = {
            "cards": [
                _parsed_card("site-guid-a:0", "first front"),
                _parsed_card("site-guid-b:0", "second front"),
            ]
        }
        result = import_parsed(db, user.id, parsed)
        db.commit()
        assert result["created"] == 1
        assert result["conflicts"] == 1
        assert db.query(Card).filter(Card.user_id == user.id).count() == 1
    finally:
        db.close()


def test_migration_replaces_word_type_constraint_with_partial_index(tmp_path, monkeypatch):
    """旧库的 uq_user_word_type 约束被替换为部分唯一索引：行数不变、
    非 anki 卡去重仍在、同词多张 anki 卡可以共存。"""
    from sqlalchemy import create_engine as sa_create_engine

    from app import db as db_mod
    from app.db import _migrate_anki_multi_template_cards

    path = str(tmp_path / "old-cards.db")
    engine = sa_create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(db_mod.text(
            "CREATE TABLE cards ("
            "id INTEGER PRIMARY KEY, "
            "user_id INTEGER NOT NULL, "
            "word VARCHAR(100) NOT NULL, "
            "card_type VARCHAR(20) NOT NULL, "
            "front TEXT NOT NULL, "
            "back TEXT NOT NULL, "
            "anki_guid VARCHAR(64), "
            "due_at DATETIME, "
            "CONSTRAINT uq_user_word_type UNIQUE (user_id, word, card_type), "
            "CONSTRAINT uq_cards_user_anki_guid UNIQUE (user_id, anki_guid))"
        ))
        connection.execute(db_mod.text(
            "INSERT INTO cards (id, user_id, word, card_type, front, back, anki_guid, due_at) "
            "VALUES (1, 1, 'durable', 'reading', 'f1', 'b1', NULL, '2026-01-01'), "
            "(2, 1, 'run', 'general', 'f2', 'b2', NULL, NULL), "
            "(3, 1, 'durable', 'anki', 'f3', 'b3', 'g:0', '2026-01-02')"
        ))
    monkeypatch.setattr(db_mod, "engine", engine)
    _migrate_anki_multi_template_cards()

    import sqlalchemy as sa

    inspector = sa.inspect(engine)
    uniques = {u.get("name") for u in inspector.get_unique_constraints("cards")}
    indexes = {i["name"] for i in inspector.get_indexes("cards")}
    assert "uq_user_word_type" not in uniques
    assert "uq_cards_user_anki_guid" in uniques
    assert "uq_cards_word_type_non_anki" in indexes

    with engine.begin() as connection:
        count = connection.execute(db_mod.text("SELECT COUNT(*) FROM cards")).scalar()
        assert count == 3
        # 同词第二张 anki 卡（不同 guid）现在可以插入。
        connection.execute(db_mod.text(
            "INSERT INTO cards (user_id, word, card_type, front, back, anki_guid, due_at) "
            "VALUES (1, 'durable', 'anki', 'f4', 'b4', 'g:1', NULL)"
        ))
        # 非 anki 卡的同 (user, word, type) 仍被拒绝。
        try:
            connection.execute(db_mod.text(
                "INSERT INTO cards (user_id, word, card_type, front, back, anki_guid, due_at) "
                "VALUES (1, 'run', 'general', 'f5', 'b5', NULL, NULL)"
            ))
            raised = False
        except Exception:
            raised = True
        assert raised, "非 anki 卡的 (user, word, type) 唯一性仍必须生效"
    engine.dispose()
