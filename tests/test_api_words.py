import datetime as dt

from app import config
from app.api_support import _learning_day
from app.db import SessionLocal
from app.models import Card
from tests.conftest import register


def test_word_library_starts_empty_and_old_classification_api_is_removed(client):
    register(client, "rank@example.com")
    library = client.get("/api/words").json()
    assert library == {"words": [], "count": 0, "query": "", "status": "all"}
    assert client.post(
        "/api/words/the/status", json={"status": "unknown"}
    ).status_code == 404
    assert client.post(
        "/api/words/import-known", json={"text": "quasar"}
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


def test_word_three_state_default_hard_and_batch_mark_easy(client):
    register(client, "three-state@example.com")
    for word in ("quasar", "nebula", "alpha"):
        lookup = client.post("/api/lookups", json={"text": word}).json()["lookup"]
        assert client.post(f"/api/lookups/{lookup['id']}/save").status_code == 200
    words = client.get("/api/words").json()["words"]
    statuses = {item["word"]: item["status"] for item in words}
    assert statuses == {"quasar": "hard", "nebula": "hard", "alpha": "hard"}

    # 批量标记 Easy：已是 hard 的词不降级（优先级 mid > hard > easy）
    marked = client.post(
        "/api/words/batch-status",
        json={"words": ["quasar", "nebula"], "status": "easy"},
    )
    assert marked.status_code == 200
    assert marked.json()["updated"] == 0
    words = client.get("/api/words").json()["words"]
    statuses = {item["word"]: item["status"] for item in words}
    assert statuses["quasar"] == "hard"
    assert statuses["nebula"] == "hard"
    assert statuses["alpha"] == "hard"

    # 未入库的新词可以被批量标记为 easy
    marked = client.post(
        "/api/words/batch-status",
        json={"words": ["beta", "gamma"], "status": "easy"},
    )
    assert marked.status_code == 200
    assert marked.json()["updated"] == 2
    words = client.get("/api/words").json()["words"]
    statuses = {item["word"]: item["status"] for item in words}
    assert statuses["beta"] == "easy"
    assert statuses["gamma"] == "easy"
    assert statuses["quasar"] == "hard"

    easy = client.get("/api/words", params={"status": "easy"}).json()
    assert easy["count"] == 2
    assert {item["word"] for item in easy["words"]} == {"beta", "gamma"}
    hard = client.get("/api/words", params={"status": "hard"}).json()
    assert hard["count"] == 3
    assert {item["word"] for item in hard["words"]} == {"quasar", "nebula", "alpha"}
    assert client.get("/api/words", params={"status": "nope"}).status_code == 400


def test_word_mid_is_derived_from_cards_and_not_manually_settable(client):
    register(client, "mid-derived@example.com")
    for word in ("quasar", "nebula"):
        lookup = client.post("/api/lookups", json={"text": word}).json()["lookup"]
        assert client.post(f"/api/lookups/{lookup['id']}/save").status_code == 200
    assert client.post(
        "/api/words/batch-status",
        json={"words": ["quasar"], "status": "mid"},
    ).status_code == 400

    from app.models import Card, User
    db = SessionLocal()
    try:
        user_id = db.query(User.id).filter(User.email == "mid-derived@example.com").one()[0]
        db.add(
            Card(
                user_id=user_id,
                word="quasar",
                card_type="general",
                front="quasar",
                back="quasar",
            )
        )
        db.commit()
    finally:
        db.close()

    words = client.get("/api/words").json()["words"]
    statuses = {item["word"]: item["status"] for item in words}
    assert statuses["quasar"] == "mid"
    assert statuses["nebula"] == "hard"
    mid = client.get("/api/words", params={"status": "mid"}).json()
    assert mid["count"] == 1
    assert mid["words"][0]["word"] == "quasar"

    # 已制卡（mid）词在批量标记 Easy 时保持 mid，不会被降级
    marked = client.post(
        "/api/words/batch-status",
        json={"words": ["quasar"], "status": "easy"},
    )
    assert marked.status_code == 200
    assert marked.json()["updated"] == 0
    words = client.get("/api/words").json()["words"]
    statuses = {item["word"]: item["status"] for item in words}
    assert statuses["quasar"] == "mid"

    # 一个单词只属于一个状态：quasar 是 mid 时不出现在 easy/hard 筛选中
    easy = client.get("/api/words", params={"status": "easy"}).json()
    assert "quasar" not in {item["word"] for item in easy["words"]}
    hard = client.get("/api/words", params={"status": "hard"}).json()
    assert "quasar" not in {item["word"] for item in hard["words"]}


def test_batch_mark_easy_count_excludes_already_easy(client):
    """批量标记 Easy 的个数不包含已 Easy 的词；preview 模式只算不写。"""
    register(client, "batch-easy-count@example.com")
    for word in ("alpha", "beta"):
        lookup = client.post("/api/lookups", json={"text": word}).json()["lookup"]
        assert client.post(f"/api/lookups/{lookup['id']}/save").status_code == 200

    # 已入库的新词标记为 easy
    first = client.post(
        "/api/words/batch-status",
        json={"words": ["gamma", "delta"], "status": "easy"},
    )
    assert first.json()["updated"] == 2

    # 混合：gamma/delta 已 easy 跳过、alpha/beta 是 hard 不降级、epsilon 新词标记
    again = client.post(
        "/api/words/batch-status",
        json={"words": ["alpha", "gamma", "delta", "epsilon"], "status": "easy"},
    )
    body = again.json()
    assert body["updated"] == 1
    assert body["skipped_existing"] == 2
    assert body["skipped_higher"] == 1

    # preview 不写库：zeta 会被算进 updated，但不会真的入库
    preview = client.post(
        "/api/words/batch-status",
        json={
            "words": ["gamma", "zeta"],
            "status": "easy",
            "preview": True,
        },
    )
    pbody = preview.json()
    assert pbody["updated"] == 1
    assert pbody["skipped_existing"] == 1
    assert pbody["preview"] is True
    easy = client.get("/api/words", params={"status": "easy"}).json()
    assert easy["count"] == 3
    assert {item["word"] for item in easy["words"]} == {
        "gamma",
        "delta",
        "epsilon",
    }
