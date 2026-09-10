import datetime as dt

from app import config
from app.api_support import _learning_day
from app.db import SessionLocal
from app.models import Card
from tests.conftest import register


def test_word_library_starts_empty_and_old_classification_api_is_removed(client):
    register(client, "rank@example.com")
    library = client.get("/api/words").json()
    assert library == {"words": [], "count": 0, "query": ""}
    assert client.post(
        "/api/words/the/status", json={"status": "unknown"}
    ).status_code == 404
    assert client.post(
        "/api/words/import-known", json={"text": "quasar"}
    ).status_code == 405
    # 三态标记接口已随状态概念一并移除（路径被 /words/{word} 占用，方法不允许）。
    assert client.post(
        "/api/words/batch-status", json={"words": ["quasar"], "status": "easy"}
    ).status_code == 405


def test_per_user_ngsl_profile_is_only_a_reading_baseline(client):
    register(client, "ngsl-profile@example.com")
    profile = client.get("/api/words/ngsl-profile")
    assert profile.status_code == 200
    assert profile.json() == {"known_rank": 3000}

    updated = client.put(
        "/api/words/ngsl-profile",
        json={"known_rank": 8000},
    )
    assert updated.status_code == 200
    assert updated.json() == {"ok": True, "known_rank": 8000}
    assert client.get("/api/words").json()["words"] == []
def test_reading_display_preference_is_user_scoped_and_validated(client):
    register(client, "reader-layout@example.com")
    default = client.get("/api/reading/display-preference")
    assert default.status_code == 200
    assert default.json() == {
        "font_family": "book",
        "font_size": 17,
        "page_margin": 36,
    }
    updated = client.put(
        "/api/reading/display-preference",
        json={"font_family": "palatino", "font_size": 20, "page_margin": 44},
    )
    assert updated.status_code == 200
    assert updated.json()["font_family"] == "palatino"
    assert client.get("/api/reading/display-preference").json()["font_size"] == 20
    assert client.put(
        "/api/reading/display-preference",
        json={"font_family": "comic", "font_size": 17, "page_margin": 36},
    ).status_code == 400


def test_per_user_storage_quota_and_single_file_limit(client, monkeypatch):
    register(client, "storage-limit@example.com")
    usage = client.get("/api/storage")
    assert usage.status_code == 200
    assert usage.json()["limit_mb"] == 50
    assert usage.json()["single_file_limit_mb"] == 10
    monkeypatch.setattr(
        config,
        "USER_STORAGE_QUOTA_BYTES",
        usage.json()["used_bytes"] + 20,
    )
    over_quota = client.post(
        "/api/card-studio/cards",
        json={"words": ["quasar"], "card_type": "general"},
    )
    assert over_quota.status_code == 413
    assert "个人存储空间不足" in over_quota.json()["detail"]

    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 10)
    too_large_file = client.post(
        "/api/card-studio/targets-file",
        params={"filename": "book.txt"},
        content=b"12345678901",
        headers={"Content-Type": "text/plain"},
    )
    assert too_large_file.status_code == 413


def test_enrich_rejects_overlong_word(client):
    register(client, "enrich-long@example.com")
    response = client.post("/api/words/" + "a" * 101 + "/enrich")
    assert response.status_code == 400
    assert "单词格式不正确" in response.json()["detail"]


def test_temporary_target_file_is_discarded_and_does_not_use_storage(client):
    register(client, "temporary-target-file@example.com")
    before = client.get("/api/storage").json()
    db = SessionLocal()
    try:
        card_count_before = db.query(Card).count()
    finally:
        db.close()
    extracted = client.post(
        "/api/card-studio/targets-file",
        params={
            "filename": "temporary.csv",
            "from_rank": 1,
            "to_rank": 31_000,
            "count": 100,
            "include_unknown": True,
        },
        content=b"sentence\nPeople run every morning.\nAdaptive systems improve.",
        headers={"Content-Type": "text/csv"},
    )
    assert extracted.status_code == 200, extracted.text
    payload = extracted.json()
    assert payload["temporary"] is True
    assert payload["source_type"] == "csv"
    assert "run" in {item["word"] for item in payload["words"]}

    after = client.get("/api/storage").json()
    assert after["used_bytes"] == before["used_bytes"]
    db = SessionLocal()
    try:
        assert db.query(Card).count() == card_count_before
    finally:
        db.close()


def test_learning_day_resets_at_shanghai_midnight():
    before_midnight = _learning_day(dt.datetime(2026, 8, 3, 15, 59, 59))
    after_midnight = _learning_day(dt.datetime(2026, 8, 3, 16, 0, 0))
    assert before_midnight[0] == "2026-08-03"
    assert after_midnight[0] == "2026-08-04"
    assert before_midnight[2] == after_midnight[1]


def test_saved_words_can_be_searched_and_batch_removed(client):
    register(client, "batch-words@example.com")
    for word in ("run", "set"):
        lookup = client.post("/api/lookups", json={"text": word}).json()["lookup"]
        saved = client.post(f"/api/lookups/{lookup['id']}/save")
        assert saved.status_code == 200
    assert client.get("/api/words", params={"q": "ru"}).json()["words"][0]["word"] == "run"

    deleted = client.post(
        "/api/words/delete-batch",
        json={"words": ["run", "set"]},
    )
    assert deleted.status_code == 200
    payload = deleted.json()
    assert payload == {"ok": True, "deleted": 2}


def test_saved_words_have_no_status_field(client):
    """生词库只记录词本身：响应不再暴露 easy/mid/hard 状态。"""
    register(client, "no-status@example.com")
    for word in ("quasar", "nebula"):
        lookup = client.post("/api/lookups", json={"text": word}).json()["lookup"]
        assert client.post(f"/api/lookups/{lookup['id']}/save").status_code == 200
    payload = client.get("/api/words").json()
    assert payload["count"] == 2
    assert {item["word"] for item in payload["words"]} == {"quasar", "nebula"}
    for item in payload["words"]:
        assert "status" not in item
        assert "mid" not in item


def test_library_view_is_derived_from_cards(client):
    """「已制卡」由 cards 表派生：制卡后词离开生词库视图，删卡后自动回来。"""
    register(client, "library-derived@example.com")
    for word in ("quasar", "nebula"):
        lookup = client.post("/api/lookups", json={"text": word}).json()["lookup"]
        assert client.post(f"/api/lookups/{lookup['id']}/save").status_code == 200

    from app.models import User

    db = SessionLocal()
    try:
        user_id = (
            db.query(User.id)
            .filter(User.email == "library-derived@example.com")
            .one()[0]
        )
        card = Card(
            user_id=user_id,
            word="quasar",
            card_type="general",
            front="quasar",
            back="quasar",
        )
        db.add(card)
        db.commit()
        card_id = card.id
    finally:
        db.close()

    library = client.get("/api/words").json()
    assert library["count"] == 1
    assert {item["word"] for item in library["words"]} == {"nebula"}

    # 删卡后该词重新出现；不再需要把状态从 mid 回写成 hard。
    assert client.delete(f"/api/cards/{card_id}").status_code == 200
    library = client.get("/api/words").json()
    assert library["count"] == 2
    assert {item["word"] for item in library["words"]} == {"quasar", "nebula"}
