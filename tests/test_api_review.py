import datetime as dt

from app.db import SessionLocal
from app.models import Card, ReviewLog, User
from tests.conftest import register


def test_app_defaults_to_study_and_exposes_progress_mobile_controls(client):
    register(client, "app-shell@example.com")
    page = client.get("/")
    assert page.status_code == 200
    html = page.text
    assert 'data-view="cards"' not in html
    assert 'data-view="corpora"' not in html
    assert 'href="/"' in html
    # 学习页卡片置顶；卡片管理菜单含撤回/删除/生词库/制作新卡/我的卡片/每天新学习。
    assert 'id="real-review-cards"' in html
    assert html.index('id="real-review-cards"') < html.index(
        'id="real-review-manage-toggle"'
    )
    assert 'id="real-review-undo"' in html
    assert 'id="real-review-delete-first"' in html
    assert 'id="real-manage-library"' in html
    # 底部导航：查词/学习/文章/卡片，不再有个人与独立管理 tab。
    assert 'data-mobile-view="manage"' not in html
    assert 'id="mobile-profile-btn"' not in html
    assert html.count('data-mobile-view="search"') == 1
    assert html.count('data-mobile-view="study"') == 1
    assert html.count('data-mobile-view="article"') == 1
    assert html.count('data-mobile-view="cards"') == 1
    # 账户菜单入口恢复在顶部横幅右侧。
    assert 'id="account-menu-toggle"' in html
    # 首页直接包含制卡入口，不再跳转到单独的制卡页。
    assert 'id="corpus-import-progress"' not in html
    assert 'id="real-card-words"' in html
    assert 'id="real-card-generate"' in html
    assert 'id="real-review-remaining-total"' in html
    assert 'id="real-extra-count"' in html
    # 数据卡保留四个统计，其中学习天数只在手机端显示。
    assert 'id="oil-today-days"' in html
    assert '<form id="landing-search-form"' in html
    assert html.count('id="daily-new-limit"') == 0
    assert html.count('id="real-review-daily-new-limit"') == 1
    assert html.index('id="real-review-daily-new-limit"') < html.index(
        'id="real-library"'
    )
    assert 'id="real-review-daily-new-limit" type="number" min="0" max="200" value="10"' in html


def test_new_user_defaults_to_ten_new_cards_per_day(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "NEW_CARDS_PER_DAY", 10)
    register(client, "review-default-ten@example.com")
    settings = client.get("/api/cards/settings")
    assert settings.status_code == 200
    assert settings.json()["new_cards_per_day"] == 10


def test_review_without_expected_revision_is_rejected(client):
    register(client, "review-missing-revision@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    assert made.status_code == 200
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]
    blocked = client.post(f"/api/cards/{card['id']}/review", json={"rating": "good"})
    assert blocked.status_code == 400
    assert "缺少卡片版本" in blocked.json()["detail"]
    again_blocked = client.post(
        f"/api/cards/{card['id']}/review",
        json={"rating": "good", "action_id": "no-revision-action"},
    )
    assert again_blocked.status_code == 400


def test_review_action_id_is_idempotent(client):
    register(client, "review-idempotent@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"card_type": "reading", "words": ["cat"]},
    )
    assert made.status_code == 200
    assert made.json()["created"] == 1
    card = client.get("/api/cards?card_type=reading").json()["new"][0]
    payload = {
        "rating": "good",
        "action_id": "same-browser-action-1",
        "expected_revision": card["revision"],
    }
    first = client.post(f"/api/cards/{card['id']}/review", json=payload)
    second = client.post(f"/api/cards/{card['id']}/review", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["idempotent"] is True
    db = SessionLocal()
    try:
        assert db.query(ReviewLog).filter(ReviewLog.card_id == card["id"]).count() == 1
        assert db.get(Card, card["id"]).reps == first.json()["card"]["reps"]
    finally:
        db.close()


def test_review_does_not_count_against_storage_quota(client, monkeypatch):
    """复习日志是学习核心路径，不占用用户内容配额；配额满时评分仍应成功。"""
    from app import config

    register(client, "review-quota@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    assert made.status_code == 200
    assert made.json()["created"] == 1
    card = client.get("/api/cards").json()["new"][0]
    usage = client.get("/api/storage").json()
    monkeypatch.setattr(
        config, "USER_STORAGE_QUOTA_BYTES", usage["used_bytes"] + 50
    )
    reviewed = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "good",
            "action_id": "quota-allowed-action",
            "expected_revision": card["revision"],
        },
    )
    assert reviewed.status_code == 200
    db = SessionLocal()
    try:
        assert db.query(ReviewLog).filter(ReviewLog.card_id == card["id"]).count() == 1
    finally:
        db.close()


def test_review_endpoints_honor_rate_limit(client, monkeypatch):
    from app.routes import card_routes

    register(client, "rate-limit-review@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"card_type": "reading", "words": ["cat"]},
    )
    assert made.status_code == 200
    assert made.json()["created"] == 1
    card = client.get("/api/cards?card_type=reading").json()["new"][0]

    monkeypatch.setattr(
        card_routes, "check_request_rate", lambda *_args, **_kwargs: False
    )
    single = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "good",
            "action_id": "rate-limit-single-action",
            "expected_revision": card["revision"],
        },
    )
    batch = client.post(
        "/api/cards/reviews/batch",
        json={"ratings": [{"card_id": card["id"], "rating": "good"}]},
    )
    assert single.status_code == 429
    assert batch.status_code == 429


def test_daily_new_limit_blocks_extra_study_and_early_review(client):
    register(client, "daily-limit@example.com")
    for word, card_type in (("run", "general"), ("set", "reading"), ("point", "cloze")):
        made = client.post(
            "/api/card-studio/cards",
            json={"words": [word], "card_type": card_type},
        )
        assert made.status_code == 200
        assert made.json()["created"] == 1

    saved = client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    assert saved.status_code == 200
    queue = client.get("/api/cards").json()
    assert queue["new_cards_per_day"] == 1
    assert len(queue["new"]) == 1
    blocked = client.get("/api/cards", params={"extra_new": 10})
    assert blocked.status_code == 409

    new_card = queue["new"][0]
    card_id = new_card["id"]
    reviewed = client.post(
        f"/api/cards/{card_id}/review",
        json={
            "rating": "easy",
            "action_id": "daily-limit-1",
            "expected_revision": new_card["revision"],
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["card"]["session_repeat"] is False
    # 认识后 2 天才到期，未到期不能提前评分。
    assert client.post(
        f"/api/cards/{card_id}/review",
        json={
            "rating": "good",
            "action_id": "daily-limit-2",
            "expected_revision": reviewed.json()["card"]["revision"],
        },
    ).status_code == 409
    assert client.get("/api/cards").json()["new"] == []
    extra = client.get("/api/cards", params={"extra_new": 10})
    assert extra.status_code == 200
    assert len(extra.json()["new"]) == 2
    # 模拟到期后继续复习。
    db = SessionLocal()
    try:
        row = db.get(Card, card_id)
        row.due_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    reviewed = client.post(
        f"/api/cards/{card_id}/review",
        json={
            "rating": "easy",
            "action_id": "daily-limit-3",
            "expected_revision": reviewed.json()["card"]["revision"],
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["card"]["session_repeat"] is False
    assert client.get("/api/cards", params={"practice_limit": 20}).status_code == 410
    assert client.post(
        f"/api/cards/{card_id}/review",
        json={
            "rating": "easy",
            "action_id": "daily-limit-4",
            "expected_revision": reviewed.json()["card"]["revision"],
        },
    ).status_code == 409

    browsed = client.get("/api/cards/browse", params={"q": "run"}).json()
    assert browsed["total"] >= 1
    assert "run" in {card["word"] for card in browsed["cards"]}


def test_daily_new_cards_are_random_but_stable_for_the_day(client, monkeypatch):
    register(client, "new-gather-order@example.com")
    for word in ("run", "set", "point"):
        made = client.post(
            "/api/card-studio/cards",
            json={"words": [word], "card_type": "reading"},
        )
        assert made.status_code == 200
        assert made.json()["created"] == 1
    client.put("/api/cards/settings", json={"new_cards_per_day": 2})
    monkeypatch.setattr(
        "app.routes.card_routes.random.shuffle", lambda items: items.reverse()
    )

    first = client.get("/api/cards").json()
    second = client.get("/api/cards").json()
    first_words = {card["word"] for card in first["new"]}
    second_words = {card["word"] for card in second["new"]}
    assert len(first_words) == 2
    assert first_words <= {"run", "set", "point"}
    assert second_words == first_words


def test_review_due_date_then_random_within_each_day(client, monkeypatch):
    register(client, "review-sort-order@example.com")
    for word in ("run", "set", "point"):
        made = client.post(
            "/api/card-studio/cards",
            json={"words": [word], "card_type": "reading"},
        )
        assert made.status_code == 200
        assert made.json()["created"] == 1
    db = SessionLocal()
    try:
        user_id = db.query(User.id).filter(User.email == "review-sort-order@example.com").scalar()
        rows = {
            row.word: row
            for row in db.query(Card).filter(
                Card.user_id == user_id, Card.card_type == "reading"
            )
        }
        base = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=3)
        rows["run"].state = "review"
        rows["run"].due_at = base.replace(hour=8)
        rows["set"].state = "review"
        rows["set"].due_at = base.replace(hour=12)
        rows["point"].state = "review"
        rows["point"].due_at = (base + dt.timedelta(days=1)).replace(hour=8)
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(
        "app.routes.card_routes.random.shuffle", lambda items: items.reverse()
    )

    queue = client.get("/api/cards").json()
    assert [card["word"] for card in queue["due"]] == ["run", "set", "point"]


def test_today_queue_mixes_reviews_and_new_cards(client, monkeypatch):
    """复习与今日新学混在同一队列：新学不被堆积的复习卡挡在门外。"""
    register(client, "mixed-queue@example.com")
    for word in ("run", "set"):
        made = client.post(
            "/api/card-studio/cards",
            json={"words": [word], "card_type": "reading"},
        )
        assert made.json()["created"] == 1
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    db = SessionLocal()
    try:
        user_id = db.query(User.id).filter(
            User.email == "mixed-queue@example.com"
        ).scalar()
        due_card = db.query(Card).filter(
            Card.user_id == user_id,
            Card.word == "run",
        ).one()
        due_card.state = "review"
        due_card.due_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=1)
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr("app.routes.card_routes.random.shuffle", lambda _items: None)

    queue = client.get("/api/cards").json()
    assert queue["remaining_counts"] == {"due": 1, "new": 1, "again": 0}
    # 到期复习与今日新学一起出现。
    assert {item["queue_kind"] for item in queue["queue"]} == {"due", "new"}
    assert {item["word"] for item in queue["queue"]} == {"run", "set"}

    due_card = queue["queue"][0]
    assert client.post(
        f"/api/cards/{due_card['id']}/review",
        json={
            "rating": "easy",
            "action_id": "mixed-queue-action",
            "expected_revision": due_card["revision"],
        },
    ).status_code == 200
    after = client.get("/api/cards").json()
    assert after["remaining_counts"] == {"due": 0, "new": 1, "again": 0}
    assert {item["queue_kind"] for item in after["queue"]} == {"new"}
    assert {item["word"] for item in after["queue"]} == {"set"}


def test_queue_orders_due_new_then_learning_last(client):
    """队列固定顺序：到期复习 → 今日新学 → 学习中的卡（重来后排队尾）。

    前端会话内把「重来/困难」卡追加到队尾，服务端也必须把学习中的卡排在
    最后，否则刷新后队列会与页面显示不一致。
    """
    register(client, "queue-order@example.com")
    for word in ("run", "set", "point"):
        made = client.post(
            "/api/card-studio/cards",
            json={"words": [word], "card_type": "reading"},
        )
        assert made.json()["created"] == 1
    client.put("/api/cards/settings", json={"new_cards_per_day": 2})
    first = client.get("/api/cards").json()
    new_cards = first["new"]
    assert len(new_cards) == 2
    again_card = new_cards[0]
    remaining_new = new_cards[1]
    db = SessionLocal()
    try:
        user_id = db.query(User.id).filter(
            User.email == "queue-order@example.com"
        ).scalar()
        due_card = (
            db.query(Card)
            .filter(
                Card.user_id == user_id,
                Card.id.notin_([again_card["id"], remaining_new["id"]]),
            )
            .first()
        )
        due_card.state = "review"
        due_card.due_at = (
            dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=1)
        )
        db.commit()
    finally:
        db.close()
    # 给其中一张今日新卡点“重来”：进入学习区，另一张仍留在今日新学。
    again = client.post(
        f"/api/cards/{again_card['id']}/review",
        json={
            "rating": "again",
            "action_id": "queue-order-again",
            "expected_revision": again_card["revision"],
        },
    )
    assert again.status_code == 200
    assert again.json()["card"]["session_repeat"] is True
    assert again.json()["card"]["repeat_now"] is True

    queue = client.get("/api/cards").json()
    kinds = [item["queue_kind"] for item in queue["queue"]]
    # 学习中的卡必须排最后：到期复习 → 今日新学 → 重来卡。
    assert kinds == ["due", "new", "again"]
    assert [item["id"] for item in queue["queue"]][-1] == again_card["id"]
    # 再次评分（认识）后毕业，队列不再包含它。
    known = client.post(
        f"/api/cards/{again_card['id']}/review",
        json={
            "rating": "easy",
            "action_id": "queue-order-easy",
            "expected_revision": again.json()["card"]["revision"],
        },
    )
    assert known.status_code == 200
    assert known.json()["card"]["repeat_now"] is False
    after = client.get("/api/cards").json()
    assert [item["queue_kind"] for item in after["queue"]] == ["due", "new"]
    assert again_card["id"] not in {item["id"] for item in after["queue"]}


def test_future_review_card_does_not_enter_today_queue(client):
    register(client, "future-review@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]
    reviewed = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "easy",
            "action_id": "future-review-1",
            "expected_revision": card["revision"],
        },
    )
    assert reviewed.status_code == 200
    # 第一次认识后 2 天才到期，不会立刻回队列。
    waiting = client.get("/api/cards").json()
    assert waiting["due"] == []
    assert waiting["remaining_counts"]["due"] == 0
    assert card["id"] not in {item["id"] for item in waiting["queue"]}
    # 未到期不能提前评分。
    assert client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "good",
            "action_id": "future-review-2",
            "expected_revision": reviewed.json()["card"]["revision"],
        },
    ).status_code == 409
    # 模拟到期后完成下一次复习。
    db = SessionLocal()
    try:
        row = db.get(Card, card["id"])
        row.due_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    reviewed = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "easy",
            "action_id": "future-review-3",
            "expected_revision": reviewed.json()["card"]["revision"],
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["card"]["next_review_date"] is not None

    waiting = client.get("/api/cards").json()
    assert waiting["due"] == []
    assert waiting["remaining_counts"]["due"] == 0
    assert card["id"] not in {item["id"] for item in waiting["queue"]}


def test_known_ends_today_and_schedules(client):
    """新卡点认识：今天学完，未到期不回队列。"""
    register(client, "known-ends-today@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]

    easy = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "easy",
            "action_id": "known-ends-1",
            "expected_revision": card["revision"],
        },
    )
    assert easy.status_code == 200
    assert easy.json()["card"]["session_repeat"] is False
    assert easy.json()["card"]["interval_days"] >= 1
    queue = client.get("/api/cards").json()
    assert queue["again"] == []
    assert queue["remaining_counts"]["again"] == 0
    # 未到期不能提前评分。
    assert client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "good",
            "action_id": "known-ends-2",
            "expected_revision": easy.json()["card"]["revision"],
        },
    ).status_code == 409
    # 模拟到期后回到队列。
    db = SessionLocal()
    try:
        row = db.get(Card, card["id"])
        row.due_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    queue = client.get("/api/cards").json()
    assert len(queue["due"]) == 1
    assert queue["remaining_counts"]["due"] == 1
    assert queue["can_extra_new"] is False
    assert client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "easy",
            "action_id": "known-ends-3",
            "expected_revision": easy.json()["card"]["revision"],
        },
    ).status_code == 200


def test_again_card_keeps_learning_until_known(client):
    """不认识：卡片今天继续学，直到点认识。"""
    register(client, "again-keeps-learning@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]
    again = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "again",
            "action_id": "again-keeps-1",
            "expected_revision": card["revision"],
        },
    )
    assert again.status_code == 200
    assert again.json()["card"]["session_repeat"] is True
    assert again.json()["card"]["interval_days"] == 0.0
    assert again.json()["card"]["rating_previews"]["again"]["label"] == ""

    waiting = client.get("/api/cards").json()
    assert waiting["again_pending_total"] == 1
    assert [item["id"] for item in waiting["queue"]] == [card["id"]]
    assert waiting["queue"][0]["queue_kind"] == "again"

    first_easy = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "easy",
            "action_id": "again-keeps-2",
            "expected_revision": again.json()["card"]["revision"],
        },
    )
    assert first_easy.status_code == 200
    assert first_easy.json()["card"]["interval_days"] >= 1
    assert client.get("/api/cards").json()["again_pending_total"] == 0

    db = SessionLocal()
    try:
        row = db.get(Card, card["id"])
        row.due_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    second_easy = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "easy",
            "action_id": "again-keeps-3",
            "expected_revision": first_easy.json()["card"]["revision"],
        },
    )
    assert second_easy.status_code == 200
    assert second_easy.json()["card"]["interval_days"] >= 1
    finished = client.get("/api/cards").json()
    assert finished["again_pending_total"] == 0
    assert finished["queue"] == []


def test_again_then_easy_graduates_to_review(client):
    """点错误后卡在今天队列，再点认识即毕业进入复习。"""
    register(client, "again-again-reset@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]
    again = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "again",
            "action_id": "again-reset-1",
            "expected_revision": card["revision"],
        },
    )
    assert again.status_code == 200
    assert again.json()["card"]["interval_days"] == 0.0

    known = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "easy",
            "action_id": "again-reset-2",
            "expected_revision": again.json()["card"]["revision"],
        },
    )
    assert known.status_code == 200
    assert known.json()["card"]["interval_days"] >= 1
    assert known.json()["card"]["state"] == "scheduled"


def test_batch_review_keeps_learning_until_known(client):
    register(client, "batch-graduate@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]
    response = client.post(
        "/api/cards/reviews/batch",
        json={
            "ratings": [
                {
                    "card_id": card["id"],
                    "rating": "again",
                    "action_id": "batch-a1",
                    "expected_revision": card["revision"],
                },
                {
                    "card_id": card["id"],
                    "rating": "good",
                    "action_id": "batch-a2",
                    "expected_revision": card["revision"] + 1,
                },
            ]
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["cards"]) == 2
    assert data["errors"] == []
    assert data["cards"][0]["card"]["interval_days"] == 0.0
    assert data["cards"][1]["card"]["interval_days"] >= 1

    db = SessionLocal()
    try:
        row = db.get(Card, card["id"])
        row.due_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    first = client.post(
        "/api/cards/reviews/batch",
        json={
            "ratings": [
                {
                    "card_id": card["id"],
                    "rating": "easy",
                    "action_id": "batch-b1",
                    "expected_revision": data["cards"][-1]["card"]["revision"],
                }
            ]
        },
    ).json()
    assert first["cards"][0]["card"]["interval_days"] >= 1

    db = SessionLocal()
    try:
        row = db.get(Card, card["id"])
        row.due_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    second = client.post(
        "/api/cards/reviews/batch",
        json={
            "ratings": [
                {
                    "card_id": card["id"],
                    "rating": "easy",
                    "action_id": "batch-c1",
                    "expected_revision": first["cards"][0]["card"]["revision"],
                }
            ]
        },
    ).json()
    assert second["cards"][0]["card"]["interval_days"] >= 1
    finished = client.get("/api/cards").json()
    assert finished["again_pending_total"] == 0
    assert finished["queue"] == []


def test_batch_review_partial_error_keeps_valid_ratings(client):
    register(client, "batch-partial@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]
    response = client.post(
        "/api/cards/reviews/batch",
        json={
            "ratings": [
                {
                    "card_id": card["id"],
                    "rating": "again",
                    "action_id": "batch-p1",
                    "expected_revision": card["revision"],
                },
                {
                    "card_id": 999999,
                    "rating": "good",
                    "action_id": "batch-p2",
                    "expected_revision": 0,
                },
            ]
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["cards"]) == 1
    assert len(data["errors"]) == 1
    assert data["errors"][0]["card_id"] == 999999
    waiting = client.get("/api/cards").json()
    # Again 后卡片今天继续学，立刻回队列。
    assert waiting["again_pending_total"] == 1
    assert len(waiting["queue"]) == 1


def test_batch_review_action_id_is_idempotent(client):
    register(client, "batch-idempotent@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]
    payload = {
        "ratings": [
            {
                "card_id": card["id"],
                "rating": "again",
                "action_id": "batch-idem-1",
                "expected_revision": card["revision"],
            }
        ]
    }
    first = client.post("/api/cards/reviews/batch", json=payload)
    assert first.status_code == 200
    assert first.json()["cards"][0]["card"]["reps"] == 1
    second = client.post("/api/cards/reviews/batch", json=payload)
    assert second.status_code == 200
    assert second.json()["cards"][0]["idempotent"] is True
    assert second.json()["cards"][0]["card"]["reps"] == 1
    assert second.json()["cards"][0]["card"]["session_repeat"] is True


def test_batch_review_rejects_stale_card_revision(client):
    register(client, "review-stale-revision@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    card = client.get("/api/cards").json()["new"][0]
    first = client.post(
        "/api/cards/reviews/batch",
        json={
            "ratings": [
                {
                    "card_id": card["id"],
                    "rating": "again",
                    "action_id": "revision-first",
                    "expected_revision": card["revision"],
                }
            ]
        },
    )
    assert first.status_code == 200
    stale = client.post(
        "/api/cards/reviews/batch",
        json={
            "ratings": [
                {
                    "card_id": card["id"],
                    "rating": "easy",
                    "action_id": "revision-stale",
                    "expected_revision": card["revision"],
                }
            ]
        },
    )
    assert stale.status_code == 200
    assert stale.json()["cards"] == []
    assert "其他页面" in stale.json()["errors"][0]["detail"]


def test_undo_clears_review_request_so_retry_reapplies(client):
    register(client, "undo-retry@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    assert made.status_code == 200
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]
    payload = {
        "rating": "easy",
        "action_id": "undo-retry-action",
        "expected_revision": card["revision"],
    }
    first = client.post(f"/api/cards/{card['id']}/review", json=payload)
    assert first.status_code == 200
    assert "idempotent" not in first.json()

    undone = client.post("/api/cards/reviews/undo")
    assert undone.status_code == 200

    retry = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "easy",
            "action_id": "undo-retry-action",
            "expected_revision": undone.json()["card"]["revision"],
        },
    )
    assert retry.status_code == 200
    assert "idempotent" not in retry.json()
    assert retry.json()["card"]["state"] == "scheduled"
    assert retry.json()["card"]["reps"] == 1
    db = SessionLocal()
    try:
        assert db.query(ReviewLog).filter(ReviewLog.card_id == card["id"]).count() == 1
    finally:
        db.close()


def test_undo_rejects_review_older_than_fifteen_minutes(client):
    register(client, "undo-expired@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    assert made.status_code == 200
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]
    reviewed = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "easy",
            "action_id": "undo-expired-action",
            "expected_revision": card["revision"],
        },
    )
    assert reviewed.status_code == 200

    db = SessionLocal()
    try:
        row = (
            db.query(ReviewLog)
            .filter(ReviewLog.card_id == card["id"])
            .one()
        )
        row.reviewed_at = (
            dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
            - dt.timedelta(minutes=20)
        )
        db.commit()
    finally:
        db.close()
    blocked = client.post("/api/cards/reviews/undo")
    assert blocked.status_code == 409


def test_review_can_restore_exact_previous_schedule(client):
    register(client, "undo-review@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    assert made.status_code == 200
    before = client.get("/api/cards").json()["new"][0]

    reviewed = client.post(
        f"/api/cards/{before['id']}/review",
        json={
            "rating": "easy",
            "action_id": "undo-review-action",
            "expected_revision": before["revision"],
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["card"]["state"] == "scheduled"
    assert client.get("/api/cards").json()["can_undo"] is True

    undone = client.post("/api/cards/reviews/undo")
    assert undone.status_code == 200
    assert undone.json()["can_undo"] is False
    restored = undone.json()["card"]
    assert restored["id"] == before["id"]
    assert restored["state"] == before["state"] == "new"
    assert restored["interval_days"] == before["interval_days"] == 0.0
    assert restored["ease"] == before["ease"]
    assert restored["reps"] == before["reps"] == 0
    assert restored["due_at"] == before["due_at"]
    assert client.post("/api/cards/reviews/undo").status_code == 404


def test_batch_requires_action_id_and_expected_revision(client):
    """批量评分缺幂等字段时整体拒绝：不写日志、不改卡片。"""
    register(client, "batch-fields@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    assert made.status_code == 200
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]

    missing_both = client.post(
        "/api/cards/reviews/batch",
        json={"ratings": [{"card_id": card["id"], "rating": "again"}]},
    )
    assert missing_both.status_code == 400

    missing_revision = client.post(
        "/api/cards/reviews/batch",
        json={
            "ratings": [
                {
                    "card_id": card["id"],
                    "rating": "again",
                    "action_id": "batch-fields-action",
                }
            ]
        },
    )
    assert missing_revision.status_code == 400

    db = SessionLocal()
    try:
        assert db.query(ReviewLog).filter(ReviewLog.card_id == card["id"]).count() == 0
        untouched = db.query(Card).filter(Card.id == card["id"]).one()
        assert int(untouched.reps or 0) == 0
        assert int(untouched.revision or 0) == card["revision"]
    finally:
        db.close()


def test_undo_rejects_when_newer_anki_progress_exists(client):
    """站内评分后又导入更新的 Anki 进度时，撤回必须拒绝并保留导入进度。"""
    from app.models import AnkiReviewLog

    register(client, "undo-anki-newer@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    assert made.status_code == 200
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]
    reviewed = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "again",
            "action_id": "undo-anki-newer-action",
            "expected_revision": card["revision"],
        },
    )
    assert reviewed.status_code == 200

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "undo-anki-newer@example.com").one()
        log = (
            db.query(ReviewLog)
            .filter(ReviewLog.card_id == card["id"])
            .one()
        )
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        imported_at = max(now, (log.reviewed_at or now) + dt.timedelta(seconds=1))
        db.add(
            AnkiReviewLog(
                source_key="sha256:newer-anki-progress",
                user_id=user.id,
                card_id=card["id"],
                anki_review_id=1_700_000_000_000,
                rating=3,
                interval_days=10.0,
                last_interval_days=0.0,
                ease=2.5,
                review_type=1,
                reviewed_at=imported_at,
            )
        )
        # 模拟较新的 Anki 导入已更新排程
        card_row = db.query(Card).filter(Card.id == card["id"]).one()
        card_row.state = "review"
        card_row.due_at = dt.datetime(2030, 1, 1, 0, 0, 0)
        card_row.reps = 9
        card_row.revision = int(card_row.revision or 0) + 1
        db.commit()
    finally:
        db.close()

    blocked = client.post("/api/cards/reviews/undo")
    assert blocked.status_code == 409

    db = SessionLocal()
    try:
        kept = db.query(Card).filter(Card.id == card["id"]).one()
        assert int(kept.reps or 0) == 9
        assert kept.due_at == dt.datetime(2030, 1, 1, 0, 0, 0)
        assert db.query(ReviewLog).filter(ReviewLog.card_id == card["id"]).count() == 1
    finally:
        db.close()


def test_concurrent_undo_deletes_exactly_one_log(client):
    """并发双撤回只删除一条评分记录，另一个请求得到 404，不产生 500。"""
    import threading

    register(client, "undo-race@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    assert made.status_code == 200
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]
    reviewed = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "again",
            "action_id": "undo-race-action",
            "expected_revision": card["revision"],
        },
    )
    assert reviewed.status_code == 200

    statuses: list[int] = []
    barrier = threading.Barrier(2)

    def undo() -> None:
        barrier.wait(timeout=10)
        statuses.append(client.post("/api/cards/reviews/undo").status_code)

    threads = [threading.Thread(target=undo) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert sorted(statuses) == [200, 404]

    db = SessionLocal()
    try:
        assert db.query(ReviewLog).filter(ReviewLog.card_id == card["id"]).count() == 0
        restored = db.query(Card).filter(Card.id == card["id"]).one()
        assert int(restored.reps or 0) == 0
    finally:
        db.close()


def test_today_stats_count_cards_and_again_ratio(client):
    register(client, "today-stats@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    client.put("/api/cards/settings", json={"new_cards_per_day": 1})
    card = client.get("/api/cards").json()["new"][0]
    reviewed = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "again",
            "action_id": "today-stats-1",
            "expected_revision": card["revision"],
        },
    )
    assert reviewed.status_code == 200
    assert "today_stats" in reviewed.json()
    db = SessionLocal()
    try:
        row = db.get(Card, card["id"])
        row.due_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    second = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "easy",
            "action_id": "today-stats-2",
            "expected_revision": reviewed.json()["card"]["revision"],
        },
    )
    assert second.status_code == 200
    db = SessionLocal()
    try:
        row = db.get(Card, card["id"])
        row.due_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "easy",
            "action_id": "today-stats-3",
            "expected_revision": second.json()["card"]["revision"],
        },
    )
    stats = client.get("/api/cards").json()["today_stats"]
    assert stats == {
        "studied": 3,
        "unique_cards": 1,
        "reviews": 0,
        "new_learned": 1,
        "again": 1,
        "again_cards": 1,
        "again_rate": 100.0,
    }

    # 新学卡当天重复复习不算“复习”；到期卡才算复习。
    db = SessionLocal()
    try:
        due = Card(
            user_id=db.query(User).filter(User.email == "today-stats@example.com").one().id,
            word="settle",
            card_type="reading",
            front="settle",
            back="settle",
            state="review",
            due_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=1),
            interval_days=1.0,
            ease=2.5,
            reps=1,
            lapses=0,
        )
        db.add(due)
        db.commit()
        due_id = due.id
    finally:
        db.close()
    assert client.post(
        f"/api/cards/{due_id}/review",
        json={
            "rating": "easy",
            "action_id": "today-stats-due",
            "expected_revision": 0,
        },
    ).status_code == 200
    combined = client.get("/api/cards").json()["today_stats"]
    assert combined["unique_cards"] == 2
    assert combined["new_learned"] == 1
    assert combined["reviews"] == 1


def test_concurrent_batch_reviews_from_two_devices_do_not_deadlock(client):
    """两个设备同时走真实评分接口时，SQLite 写事务应串行成功。"""
    import threading

    from fastapi.testclient import TestClient

    from app.main import app

    register(client, "concurrent-review@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"card_type": "general", "words": ["run", "jump"]},
    )
    assert made.status_code == 200
    assert made.json()["created"] == 2

    cards = client.get("/api/cards?card_type=general").json()["new"]
    assert len(cards) == 2
    card_a, card_b = cards[:2]

    devices = [TestClient(app), TestClient(app)]
    for device in devices:
        device.cookies.update(client.cookies)
    barrier = threading.Barrier(2)
    responses = [None, None]

    def rate(index: int, card: dict) -> None:
        barrier.wait(timeout=10)
        responses[index] = devices[index].post(
            "/api/cards/reviews/batch",
            json={
                "ratings": [
                    {
                        "card_id": card["id"],
                        "rating": "good",
                        "action_id": f"concurrent-device-{index}",
                        "expected_revision": card["revision"],
                    }
                ]
            },
        )

    threads = [
        threading.Thread(target=rate, args=(0, card_a)),
        threading.Thread(target=rate, args=(1, card_b)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    for device in devices:
        device.close()

    assert all(response is not None for response in responses)
    assert [response.status_code for response in responses] == [200, 200]
    assert all(response.json()["errors"] == [] for response in responses)


def test_review_database_busy_is_retryable(client, monkeypatch):
    import sqlite3

    from sqlalchemy.exc import OperationalError

    from app.routes import card_routes

    register(client, "review-busy@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"card_type": "general", "words": ["wait"]},
    )
    card = client.get("/api/cards?card_type=general").json()["new"][0]
    assert made.status_code == 200

    def busy(_db):
        raise OperationalError(
            "BEGIN IMMEDIATE",
            {},
            sqlite3.OperationalError("database is locked"),
        )

    monkeypatch.setattr(card_routes, "reserve_sqlite_write", busy)
    response = client.post(
        "/api/cards/reviews/batch",
        json={
            "ratings": [
                {
                    "card_id": card["id"],
                    "rating": "good",
                    "action_id": "busy-action",
                    "expected_revision": card["revision"],
                }
            ]
        },
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json() == {
        "detail": "数据库繁忙，请稍后重试",
        "code": "db_busy",
    }


def test_review_survives_concurrent_tts_prefetch(client, monkeypatch):
    """回归线上场景：切换下一张卡的 TTS 预取不能再撞掉评分写入。"""
    import threading

    from fastapi.testclient import TestClient

    from app.main import app
    from app.routes import tts_routes

    register(client, "review-with-tts@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={
            "card_type": "general",
            "words": ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"],
        },
    )
    assert made.status_code == 200
    cards = client.get("/api/cards?card_type=general").json()["new"]
    assert len(cards) == 6
    monkeypatch.setattr(tts_routes.tts, "schedule_prefetch", lambda texts: len(texts))

    scorer = TestClient(app)
    speaker = TestClient(app)
    scorer.cookies.update(client.cookies)
    speaker.cookies.update(client.cookies)
    try:
        for index, card in enumerate(cards):
            barrier = threading.Barrier(2)
            responses = {}

            def rate(
                current_card=card,
                current_index=index,
                current_barrier=barrier,
                current_responses=responses,
            ) -> None:
                current_barrier.wait(timeout=10)
                current_responses["review"] = scorer.post(
                    "/api/cards/reviews/batch",
                    json={
                        "ratings": [
                            {
                                "card_id": current_card["id"],
                                "rating": "good",
                                "action_id": f"tts-race-{current_index}",
                                "expected_revision": current_card["revision"],
                            }
                        ]
                    },
                )

            def prefetch(
                current_card=card,
                current_barrier=barrier,
                current_responses=responses,
            ) -> None:
                current_barrier.wait(timeout=10)
                current_responses["tts"] = speaker.post(
                    "/api/tts/prefetch", json={"texts": [current_card["word"]]}
                )

            threads = [threading.Thread(target=rate), threading.Thread(target=prefetch)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

            assert responses["review"].status_code == 200, responses["review"].text
            assert responses["review"].json()["errors"] == []
            assert responses["tts"].status_code == 200, responses["tts"].text
    finally:
        scorer.close()
        speaker.close()


def test_due_queue_payload_is_capped_but_remaining_count_stays_true(client):
    """到期队列载荷有上限（防 1 万卡导入后单响应数十 MB），
    但剩余数必须反映真实总数，不能被截断成上限值。"""
    register(client, "queue-cap@example.com")
    user_id = (
        SessionLocal()
        .query(User.id)
        .filter(User.email == "queue-cap@example.com")
        .scalar()
    )
    past = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=1)
    db = SessionLocal()
    try:
        db.add_all(
            Card(
                user_id=user_id,
                word=f"capword{index}",
                card_type="general",
                front=f"capword{index}",
                back="back",
                state="review",
                due_at=past,
            )
            for index in range(1003)
        )
        db.commit()
    finally:
        db.close()

    data = client.get("/api/cards").json()
    assert len(data["queue"]) == 1000
    assert data["remaining_counts"]["due"] == 1003
    assert data["due_total"] == 1003
    # 未触上限的常规用户行为不变：队列与剩余数一致。
    assert all(item["queue_kind"] == "due" for item in data["queue"])
