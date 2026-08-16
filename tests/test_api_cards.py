import re

from app import vocab
from app.db import SessionLocal
from app.models import Card, ReviewLog, User
from tests.conftest import register


def _make_corpus(user_email: str, text: str) -> int:
    """直接写库创建语料（语料阅读接口已移除，AI 短文/制卡语料源仍保留）。"""
    from app.api_support import _create_corpus

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == user_email).one()
        corpus = _create_corpus(db, user, "Studio source", text)
        return corpus.id
    finally:
        db.close()


def test_full_learning_flow(client):
    register(client, "dave@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"card_type": "reading", "words": ["cat", "mat"]},
    )
    assert made.status_code == 200
    assert made.json()["created"] >= 1

    queue = client.get("/api/cards?card_type=reading")
    assert queue.status_code == 200
    new_cards = queue.json()["new"]
    assert len(new_cards) >= 1

    review = client.post(
        f"/api/cards/{new_cards[0]['id']}/review",
        json={
            "rating": "easy",
            "action_id": "full-flow-action",
            "expected_revision": new_cards[0]["revision"],
        },
    )
    assert review.status_code == 200
    assert review.json()["card"]["state"] == "scheduled"
    assert review.json()["card"]["interval_days"] >= 1

    dash = client.get("/api/dashboard")
    assert dash.status_code == 200
    data = dash.json()
    assert len(data["review_days"]) == 30
    assert len(data["forecast"]) == 30
    assert data["delayed_recall_rate"] is None
    assert data["attempted_cards"] == 1
    assert data["consecutive_study_days"] == 1


def test_generated_card_types_keep_their_front_formats(client):
    register(client, "sentence-cards@example.com")
    for card_type in ("general", "reading", "cloze"):
        generated = client.post(
            "/api/card-studio/cards",
            json={
                "card_type": card_type,
                "words": ["quasar"],
            },
        )
        assert generated.status_code == 200
        assert generated.json()["created"] == 1
        queue = client.get("/api/cards", params={"card_type": card_type}).json()
        card = queue["new"][0]
        assert not card["back"].lower().startswith("quasar\n")
        if card_type == "general":
            # 通用卡固定为 正面单词 / 反面含义，正面不出现阅读材料的句子。
            assert card["front"] == "quasar"
        else:
            assert len(card["front"].split()) >= 6
            assert card["front"].rstrip().endswith(".")
            if card_type == "cloze":
                assert "______" in card["front"]
            else:
                assert "**quasar**" in card["front"].lower()


def test_studio_ai_cards_follow_old_streamlit_formats(client):
    register(client, "old-streamlit-format@example.com")

    general = client.post(
        "/api/card-studio/cards",
        json={"words": ["taxi (verb)"], "card_type": "general"},
    )
    assert general.status_code == 200
    assert general.json()["created"] == 1
    general_card = client.get(
        "/api/cards", params={"card_type": "general"}
    ).json()["new"][0]
    assert general_card["front"] == "taxi"
    assert general_card["back"] == "测试释义"

    reading = client.post(
        "/api/card-studio/cards",
        json={"words": ["adamant"], "card_type": "reading"},
    )
    assert reading.json()["created"] == 1
    reading_card = client.get(
        "/api/cards", params={"card_type": "reading"}
    ).json()["new"][0]
    assert reading_card["front"] == "The **adamant** appears clearly in this test sentence."
    assert reading_card["back"] == "n. | a test meaning | 测试释义"

    cloze = client.post(
        "/api/card-studio/cards",
        json={"words": ["quasar"], "card_type": "cloze"},
    )
    assert cloze.json()["created"] == 1
    cloze_card = client.get(
        "/api/cards", params={"card_type": "cloze"}
    ).json()["new"][0]
    assert "______" in cloze_card["front"]
    assert cloze_card["back"].startswith(
        "The **quasar** appears clearly in this test sentence, so you can answer.\n\n测试释义"
    )


def test_studio_cards_preserve_user_case(client):
    register(client, "case-cards@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["march", "March"], "card_type": "general"},
    )
    assert made.status_code == 200
    assert made.json()["created"] == 2

    queue = client.get("/api/cards").json()["queue"]
    card_words = sorted(item["word"] for item in queue)
    assert card_words == ["March", "march"]

    # 制卡后词保留在词库并显示 mid（已制卡）
    words = client.get("/api/words", params={"q": "march"}).json()["words"]
    assert {item["word"] for item in words} == {"March", "march"}
    assert all(item["status"] == "mid" for item in words)
    cards = client.get("/api/cards/browse", params={"q": "march"}).json()["cards"]
    assert {item["word"] for item in cards} == {"March", "march"}


def test_corpus_card_creation_preserves_user_case(client):
    register(client, "case-corpus@example.com")
    corpus_id = _make_corpus(
        "case-corpus@example.com",
        "March came quickly, and we march together every morning.",
    )
    made = client.post(
        "/api/card-studio/cards",
        json={
            "corpus_id": corpus_id,
            "words": ["March", "march"],
            "card_type": "general",
        },
    )
    assert made.status_code == 200
    assert made.json()["created"] == 2
    queue = client.get("/api/cards").json()["queue"]
    assert sorted(item["word"] for item in queue) == ["March", "march"]


def test_builtin_speaking_needs_are_categorized(client):
    register(client, "speaking-needs@example.com")
    data = client.get("/api/card-studio/needs").json()
    assert data["total"] >= 400
    assert len(data["categories"]) >= 20
    all_fronts = [
        need["front"] for group in data["categories"] for need in group["needs"]
    ]
    assert "对朋友说：婉拒邀请（不想去又不扫兴）" in all_fronts
    assert all(front.startswith("对") and "说：" in front for front in all_fronts)
    assert all(not need["has_card"] for group in data["categories"] for need in group["needs"])


def test_speaking_cards_generate_three_expressions_and_prefetch_audio(client):
    register(client, "speaking-cards@example.com")
    needs = [
        "对朋友说：婉拒邀请（不想去又不扫兴）",
        "对路人说：礼貌地问路",
        "对对方说：请对方说慢一点",
    ]
    res = client.post(
        "/api/card-studio/cards",
        json={"card_type": "speaking", "words": needs},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["created"] == 3
    assert data["failed"] == []
    assert data["created_words"] == needs
    assert len(data["audio_texts"]) >= 3
    assert all(re.search(r"[A-Za-z]", text) for text in data["audio_texts"])

    queue = client.get("/api/cards", params={"card_type": "speaking"}).json()
    new_cards = queue["new"]
    assert len(new_cards) == 3
    card = next(item for item in new_cards if item["front"] == needs[0])
    assert card["card_type"] == "speaking"
    # API 层 word 展示正面中文，而不是内部哈希。
    assert card["word"] == needs[0]
    assert "Alex" in card["defaults"]
    assert "City Library" in card["defaults"]
    assert card["back"].count(" || ") == 2
    assert "I'd love to" in card["back"]

    # 再次提交同一批：全部跳过，不重复消耗 AI。
    again = client.post(
        "/api/card-studio/cards",
        json={"card_type": "speaking", "words": needs},
    )
    assert again.json()["created"] == 0
    assert again.json()["existing"] == 3

    # 已制卡条目在需求集里标记出来。
    needs_data = client.get("/api/card-studio/needs").json()
    marked = [
        need
        for group in needs_data["categories"]
        for need in group["needs"]
        if need["front"] == needs[0]
    ]
    assert marked and marked[0]["has_card"] is True

    # 口语卡内部哈希不会污染生词库。
    assert client.get("/api/words").json()["words"] == []


def test_speaking_cards_accept_custom_needs_and_skip_short_backs(client):
    register(client, "speaking-custom@example.com")
    res = client.post(
        "/api/card-studio/cards",
        json={
            "card_type": "speaking",
            "words": ["自定义需求：和邻居打招呼", "hello how are you"],
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    # 纯英文行没有中文需求会被过滤；自定义中文需求正常制卡。
    assert data["created"] == 1
    assert data["created_words"] == ["自定义需求：和邻居打招呼"]
    browse = client.get("/api/cards/browse", params={"card_type": "speaking"}).json()
    assert len(browse["cards"]) == 1
    assert browse["cards"][0]["front"] == "自定义需求：和邻居打招呼"


def test_phrase_cards_appear_in_learning_cards_not_saved_words(client):
    register(client, "phrase-mid@example.com")
    generated = client.post(
        "/api/card-studio/cards",
        json={"words": ["mull it over"], "card_type": "reading"},
    )
    assert generated.status_code == 200
    assert generated.json()["created"] == 1
    # 制卡后词保留在词库并显示为 mid（已制卡）
    words = client.get("/api/words", params={"q": "mull it over"}).json()["words"]
    assert words and words[0]["status"] == "mid"
    cards = client.get("/api/cards/browse", params={"q": "mull it over"}).json()
    assert any(item["word"] == "mull it over" for item in cards["cards"])


def test_sync_mid_endpoint_is_removed(client):
    register(client, "sync-mid@example.com")
    generated = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    assert generated.json()["created"] == 1

    result = client.post("/api/cards/sync-mid")
    assert result.status_code == 405


def test_sentence_refresh_is_throttled(client, monkeypatch):
    register(client, "throttle-refresh@example.com")
    calls = []
    monkeypatch.setattr(
        "app.routes.card_routes.card_builder.refresh_sentence_cards",
        lambda db, user_id: calls.append(user_id),
    )
    client.get("/api/cards")
    client.get("/api/cards")
    assert len(calls) == 1


def test_card_studio_target_sources_and_only_creation_route(client):
    register(client, "studio@example.com")
    corpus_id = _make_corpus("studio@example.com", "People run with an adaptive quasar.")
    targets = client.post(
        "/api/card-studio/targets",
        json={
            "source": "corpus",
            "corpus_id": corpus_id,
            "from_rank": 1,
            "to_rank": 31000,
            "count": 50,
            "include_unknown": True,
        },
    )
    assert targets.status_code == 200
    assert "run" in {item["word"] for item in targets.json()["words"]}

    wordlist = client.post(
        "/api/card-studio/targets",
        json={
            "source": "wordlist",
            "text": "run\nrun\nset; mull it over",
            "from_rank": 1,
            "to_rank": 31000,
            "count": 50,
        },
    ).json()
    assert [item["word"] for item in wordlist["words"]] == ["run", "set", "mull it over"]

    generated = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    assert generated.status_code == 200
    assert generated.json()["created"] == 1


def test_card_studio_pasted_text_and_saved_words_imports_all(client):
    register(client, "studio-paste-saved@example.com")
    pasted = client.post(
        "/api/card-studio/targets",
        json={
            "source": "corpus",
            "text": "People run with an adaptive quasar and a bright nebula.",
            "from_rank": 1,
            "to_rank": 31000,
            "count": 50,
            "include_unknown": True,
        },
    )
    assert pasted.status_code == 200
    pasted_words = {item["word"] for item in pasted.json()["words"]}
    assert "run" in pasted_words
    assert "quasar" in pasted_words
    assert "nebula" in pasted_words

    for word in ("run", "set", "point"):
        lookup = client.post("/api/lookups", json={"text": word}).json()["lookup"]
        assert client.post(f"/api/lookups/{lookup['id']}/save").status_code == 200
    saved = client.post(
        "/api/card-studio/targets",
        json={"source": "saved", "count": 1, "card_type": "general"},
    )
    assert saved.status_code == 200
    assert len(saved.json()["words"]) == 3


def test_card_studio_saved_source_excludes_easy_and_mid_words(client):
    register(client, "studio-saved-no-easy@example.com")
    for word in ("run", "set"):
        lookup = client.post("/api/lookups", json={"text": word}).json()["lookup"]
        assert client.post(f"/api/lookups/{lookup['id']}/save").status_code == 200
    # point 通过批量标记面板标为 easy（词不在词库时直接创建 easy 行），
    # set 制卡后为 mid（已制卡）：两者都不应被提取
    marked = client.post(
        "/api/words/batch-status", json={"words": ["point"], "status": "easy"}
    )
    assert marked.status_code == 200
    assert marked.json()["updated"] == 1
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["set"], "card_type": "general"},
    )
    assert made.status_code == 200
    assert made.json()["created"] == 1

    saved = client.post(
        "/api/card-studio/targets",
        json={"source": "saved", "count": 1, "card_type": "general"},
    )
    assert saved.status_code == 200
    words = [item["word"] for item in saved.json()["words"]]
    assert "point" not in words
    assert "set" not in words
    assert words == ["run"]


def test_card_studio_wordlist_and_saved_sources_support_ngsl_filter(client):
    register(client, "studio-ngsl-filter@example.com")
    pasted = client.post(
        "/api/card-studio/targets",
        json={
            "source": "wordlist",
            "text": "the\npoint\nset\nrun\nquasar\nmull it over",
            "from_rank": 100,
            "to_rank": 200,
            "count": 50,
            "ngsl_filter": True,
        },
    )
    assert pasted.status_code == 200
    assert [item["word"] for item in pasted.json()["words"]] == [
        "point", "set", "run",
    ]
    assert all(100 <= item["rank"] <= 200 for item in pasted.json()["words"])

    for word in ("the", "run", "quasar"):
        lookup = client.post("/api/lookups", json={"text": word}).json()["lookup"]
        assert client.post(f"/api/lookups/{lookup['id']}/save").status_code == 200
    saved = client.post(
        "/api/card-studio/targets",
        json={
            "source": "saved",
            "from_rank": 100,
            "to_rank": 200,
            "ngsl_filter": True,
            "card_type": "general",
        },
    )
    assert saved.status_code == 200
    assert [item["word"] for item in saved.json()["words"]] == ["run"]


def test_cards_queue_reports_total_cards_for_empty_state(client):
    register(client, "empty-cards@example.com")
    queue = client.get("/api/cards")
    assert queue.status_code == 200
    assert queue.json()["total_cards"] == 0


def test_browse_ids_only_mode_lists_all_matching_ids(client):
    register(client, "browse-ids@example.com")
    for word in ("alpha", "beta"):
        made = client.post(
            "/api/card-studio/cards",
            json={"words": [word], "card_type": "general"},
        )
        assert made.status_code == 200
        assert made.json()["created"] == 1
    normal = client.get("/api/cards/browse", params={"limit": 100}).json()
    ids_only = client.get(
        "/api/cards/browse", params={"ids_only": "true", "limit": 2000}
    ).json()
    assert ids_only["total"] == normal["total"] == 2
    assert sorted(ids_only["ids"]) == sorted(card["id"] for card in normal["cards"])
    assert "ids" not in normal


def test_browse_cards_sort_by_alpha_and_ngsl(client):
    register(client, "browse-sort@example.com")
    for word in ("zebra", "apple", "quasar"):
        made = client.post(
            "/api/card-studio/cards",
            json={"words": [word], "card_type": "general"},
        )
        assert made.status_code == 200
        assert made.json()["created"] == 1

    alpha = client.get("/api/cards/browse", params={"sort": "alpha"}).json()
    assert [item["word"] for item in alpha["cards"]] == ["apple", "quasar", "zebra"]

    ngsl = client.get("/api/cards/browse", params={"sort": "ngsl"}).json()
    words = [item["word"] for item in ngsl["cards"]]
    # apple(12) < quasar(15115) < zebra(10768?)——zebra 排名低于 quasar 时按字母兜底
    ranks = [vocab.rank_of(w) for w in words]
    assert all(
        (ranks[i] is None or ranks[i + 1] is None or ranks[i] <= ranks[i + 1])
        for i in range(len(ranks) - 1)
    )
    # 每张卡片返回 ngsl_rank，且与排序一致（不在词表的为 None）
    assert all("ngsl_rank" in card for card in ngsl["cards"])
    assert [card["ngsl_rank"] for card in ngsl["cards"]] == ranks

    time_sorted = client.get("/api/cards/browse", params={"sort": "time"}).json()
    assert [item["word"] for item in time_sorted["cards"]] == [
        "quasar",
        "apple",
        "zebra",
    ]


def test_browse_ngsl_sort_is_global_across_pages(client):
    """NGSL 排序必须是全局的：跨页排名连续升序，不在词表的排最后。"""
    register(client, "browse-ngsl-global@example.com")
    common = [
        "the", "be", "and", "of", "a", "in", "to", "have", "it", "i",
        "that", "for", "you", "he", "with", "on", "do", "say", "this", "they",
        "at", "but", "we", "his", "from", "not", "by", "she", "or", "as",
    ]
    unknown = [f"zzzword{i}" for i in range(1, 31)]
    order = []
    for i in range(30):
        order.append(common[i])
        order.append(unknown[i])
    for word in order:
        made = client.post(
            "/api/card-studio/cards",
            json={"words": [word], "card_type": "general"},
        )
        assert made.status_code == 200
        assert made.json()["created"] == 1

    page1 = client.get(
        "/api/cards/browse", params={"sort": "ngsl", "limit": 50, "offset": 0}
    ).json()
    page2 = client.get(
        "/api/cards/browse", params={"sort": "ngsl", "limit": 50, "offset": 50}
    ).json()
    assert page1["total"] == 60
    ranks = [c["ngsl_rank"] for c in page1["cards"]] + [
        c["ngsl_rank"] for c in page2["cards"]
    ]
    known = [r for r in ranks if r is not None]
    assert known == sorted(known)
    first_unknown = next(i for i, r in enumerate(ranks) if r is None)
    assert all(r is None for r in ranks[first_unknown:])


def test_card_studio_targets_exclude_words_with_existing_card_of_same_type(client):
    """筛选时：已有目标卡片的单词被筛掉（同类型才筛，不同类型保留）。"""
    register(client, "studio-filter@example.com")
    created = client.post(
        "/api/card-studio/cards",
        json={"words": ["quasar"], "card_type": "reading"},
    )
    assert created.status_code == 200
    assert created.json()["created"] == 1

    targets = client.post(
        "/api/card-studio/targets",
        json={
            "source": "wordlist",
            "text": "quasar\nnebula\nnova",
            "from_rank": 1,
            "to_rank": 31000,
            "count": 50,
        },
    ).json()
    words = [item["word"] for item in targets["words"]]
    assert "quasar" not in words  # 已有 reading 卡 → 筛掉
    assert "nebula" in words
    assert "nova" in words

    # 换一种卡片类型：同词不筛（目标卡类型不同）。
    other_type = client.post(
        "/api/card-studio/targets",
        json={
            "source": "wordlist",
            "text": "quasar",
            "from_rank": 1,
            "to_rank": 31000,
            "count": 50,
            "card_type": "general",
        },
    ).json()
    assert [item["word"] for item in other_type["words"]] == ["quasar"]


def test_card_studio_ngsl_targets_exclude_same_type_card_and_fill_count(client):
    """NGSL 筛选：排除同类型已有卡片的词，再从起始排名按顺序取满 count 个。"""
    register(client, "ngsl-filter@example.com")
    created = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    assert created.status_code == 200
    assert created.json()["created"] == 1

    # 同类型（general）：run 被排除，且排除后仍取满 count
    same_type = client.post(
        "/api/card-studio/targets",
        json={
            "source": "ngsl",
            "from_rank": 100,
            "to_rank": 300,
            "count": 150,
            "card_type": "general",
        },
    ).json()
    words = [item["word"] for item in same_type["words"]]
    assert "run" not in words  # 同类型已有卡 → 排除
    assert len(words) == 150  # 排除后仍按顺序补位取满 count
    ranks = [item["rank"] for item in same_type["words"]]
    assert ranks == sorted(ranks)  # 从起始排名按顺序

    # 不同类型（reading）：run 保留
    other_type = client.post(
        "/api/card-studio/targets",
        json={
            "source": "ngsl",
            "from_rank": 100,
            "to_rank": 300,
            "count": 150,
            "card_type": "reading",
        },
    ).json()
    other_words = [item["word"] for item in other_type["words"]]
    assert "run" in other_words  # 已有 general 卡，不影响 reading 筛选
    assert len(other_words) == 150


def test_card_studio_ngsl_targets_respect_range_and_order(client):
    """NGSL 范围从 from_rank 开始按顺序取，最多 count 个。"""
    register(client, "ngsl-range@example.com")
    targets = client.post(
        "/api/card-studio/targets",
        json={
            "source": "ngsl",
            "from_rank": 500,
            "to_rank": 1000,
            "count": 30,
            "card_type": "general",
        },
    ).json()
    items = targets["words"]
    assert len(items) == 30
    ranks = [item["rank"] for item in items]
    assert ranks == sorted(ranks)
    assert ranks[0] >= 500 and ranks[-1] <= 1000


def test_card_studio_accepts_at_most_five_hundred_input_words(client):
    register(client, "studio-five-hundred@example.com")
    accepted = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"] * 500, "card_type": "general"},
    )
    assert accepted.status_code == 200, accepted.text
    rejected = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"] * 501, "card_type": "general"},
    )
    assert rejected.status_code == 422


def test_ai_reading_card_is_a_sentence_and_repairs_legacy_front(client, monkeypatch):
    register(client, "ai-reading-card@example.com")
    monkeypatch.setattr(
        "app.routes.card_routes._sentence_refresh_due", lambda _db, _user_id: True
    )

    def fake_generate(_db, _user_id, words, _card_template="reading"):
        results = {}
        for word in words:
            results[word] = {
                "w": word,
                "m": "v. | move quickly on foot | 跑，奔跑",
                "e": "She runs every morning before work.",
            }
        return (
            results,
            {},
            1,
            {"ai_wait_seconds": 0.0, "format_retry_count": 0, "db_write_seconds": 0.0},
        )

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_card_content_in_batches", fake_generate
    )
    generated = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    assert generated.status_code == 200
    assert generated.json()["created"] == 1

    browsed = client.get(
        "/api/cards/browse", params={"q": "run", "card_type": "reading"}
    ).json()
    card = browsed["cards"][0]
    assert card["front"] == "She **runs** every morning before work."
    assert "\n" not in card["front"]

    db = SessionLocal()
    try:
        row = db.query(Card).filter(Card.id == card["id"]).one()
        row.front = "run"
        row.context = "run"
        db.commit()
    finally:
        db.close()

    # 修复已改为响应后执行（BackgroundTasks）：先触发一次，
    # TestClient 会等后台任务完成，再取修复后的结果。
    client.get("/api/cards/browse", params={"q": "run", "card_type": "reading"})
    repaired = client.get(
        "/api/cards/browse", params={"q": "run", "card_type": "reading"}
    ).json()["cards"][0]
    assert repaired["front"] == "She **runs** every morning before work."
    assert "**runs**" in repaired["front"]
    assert len(repaired["front"].split()) >= 6


def test_refresh_repairs_general_cards_with_sentence_fronts(client, monkeypatch):
    """历史 bug 把通用卡正面写成了阅读材料的句子；刷新时必须恢复为单词。"""
    register(client, "repair-general@example.com")
    monkeypatch.setattr(
        "app.routes.card_routes._sentence_refresh_due", lambda _db, _user_id: True
    )
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    db = SessionLocal()
    try:
        row = db.query(Card).filter(Card.card_type == "general", Card.word == "run").first()
        row.front = "She runs every morning before work."
        db.commit()
    finally:
        db.close()

    # 修复已改为响应后执行（BackgroundTasks）：先触发，再取修复后的结果。
    client.get("/api/cards/browse", params={"q": "run", "card_type": "general"})
    browsed = client.get(
        "/api/cards/browse", params={"q": "run", "card_type": "general"}
    ).json()
    card = browsed["cards"][0]
    assert card["front"] == "run"


def test_extra_new_reports_no_new_cards_for_selected_type(client):
    """继续学新卡时，所选类型没新卡必须返回可学数量信息，而不是静默空队列。"""
    register(client, "extra-empty@example.com")
    client.put("/api/cards/settings", json={"new_cards_per_day": 0})
    created = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    assert created.status_code == 200
    assert created.json()["created"] == 1

    empty = client.get("/api/cards", params={"card_type": "general", "extra_new": 5})
    assert empty.status_code == 200
    data = empty.json()
    assert data["extra_new"] is True
    assert data["queue"] == []
    assert data["extra_available"] == 0
    assert data["extra_available_all"] == 1

    available = client.get("/api/cards", params={"card_type": "reading", "extra_new": 5})
    assert available.status_code == 200
    assert len(available.json()["queue"]) == 1
    assert available.json()["extra_available"] == 1
    assert available.json()["extra_available_all"] == 1


def test_extra_new_cards_persist_until_finished(client):
    """继续学习抽出的新卡要持久化：刷新后没学完的卡仍留在队列里。"""
    register(client, "extra-sticky@example.com")
    client.put("/api/cards/settings", json={"new_cards_per_day": 0})
    for word, card_type in (("run", "general"), ("set", "reading")):
        made = client.post(
            "/api/card-studio/cards",
            json={"words": [word], "card_type": card_type},
        )
        assert made.status_code == 200
        assert made.json()["created"] == 1

    first = client.get("/api/cards", params={"extra_new": 2}).json()
    extra_cards = {card["id"]: card["revision"] for card in first["new"]}
    assert len(extra_cards) == 2

    refreshed = client.get("/api/cards").json()
    assert {card["id"] for card in refreshed["new"]} == set(extra_cards)
    assert refreshed["remaining_counts"]["new"] == 2
    assert refreshed["can_extra_new"] is False

    extra_id = next(iter(extra_cards))
    done = client.post(
        f"/api/cards/{extra_id}/review",
        json={
            "rating": "good",
            "action_id": "extra-sticky-1",
            "expected_revision": extra_cards.pop(extra_id),
        },
    )
    assert done.status_code == 200
    refreshed = client.get("/api/cards").json()
    assert {card["id"] for card in refreshed["new"]} == set(extra_cards)
    assert refreshed["remaining_counts"]["new"] == 1

    extra_id = next(iter(extra_cards))
    done = client.post(
        f"/api/cards/{extra_id}/review",
        json={
            "rating": "good",
            "action_id": "extra-sticky-2",
            "expected_revision": extra_cards.pop(extra_id),
        },
    )
    assert done.status_code == 200
    refreshed = client.get("/api/cards").json()
    assert refreshed["new"] == []
    assert refreshed["remaining_counts"]["new"] == 0
    assert refreshed["can_extra_new"] is True


def test_extra_new_response_counts_match_queue(client):
    """加学响应必须与本次队列同口径：队列有几张，剩余新学就报几张。"""
    register(client, "extra-counts@example.com")
    client.put("/api/cards/settings", json={"new_cards_per_day": 0})
    for word, card_type in (("run", "general"), ("set", "reading")):
        made = client.post(
            "/api/card-studio/cards",
            json={"words": [word], "card_type": card_type},
        )
        assert made.status_code == 200
        assert made.json()["created"] == 1

    data = client.get("/api/cards", params={"extra_new": 2}).json()
    assert len(data["queue"]) == 2
    assert data["remaining_counts"] == {"due": 0, "new": 2, "again": 0}
    assert data["can_extra_new"] is False


def test_remaining_counts_follow_card_type_filter(client):
    """按类型筛选时，剩余计数必须与过滤后的队列一致，不能报全局总数。"""
    register(client, "type-counts@example.com")
    client.put("/api/cards/settings", json={"new_cards_per_day": 10})
    for word, card_type in (("run", "general"), ("set", "reading")):
        made = client.post(
            "/api/card-studio/cards",
            json={"words": [word], "card_type": card_type},
        )
        assert made.status_code == 200
        assert made.json()["created"] == 1

    data = client.get("/api/cards", params={"card_type": "reading"}).json()
    assert data["queue"]
    assert data["remaining_counts"] == {
        "due": len(data["due"]),
        "new": len(data["new"]),
        "again": len(data["again"]),
    }
    assert data["remaining_counts"]["new"] == len(data["new"])


def test_refresh_highlights_legacy_reading_fronts_and_bolds_word_only_cards(client, monkeypatch):
    """旧版刷新写出的无高亮阅读卡正面，必须重新凸显目标词；
    找不到例句的裸单词正面退化为加粗单词。"""
    register(client, "highlight-repair@example.com")
    monkeypatch.setattr(
        "app.routes.card_routes._sentence_refresh_due", lambda _db, _user_id: True
    )
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    client.post(
        "/api/card-studio/cards",
        json={"words": ["quasar"], "card_type": "reading"},
    )
    browsed_before = client.get("/api/cards/browse").json()["cards"]
    run_id = next(item["id"] for item in browsed_before if item["word"] == "run")
    quasar_id = next(item["id"] for item in browsed_before if item["word"] == "quasar")
    db = SessionLocal()
    try:
        run_card = db.get(Card, run_id)
        run_card.front = "She runs every morning before work."
        run_card.context = "She runs every morning before work."
        word_card = db.get(Card, quasar_id)
        word_card.front = "quasar"
        word_card.context = "quasar"
        db.commit()
    finally:
        db.close()

    # 修复已改为响应后执行（BackgroundTasks）：先触发，再取修复后的结果。
    client.get("/api/cards/browse")
    browsed = client.get("/api/cards/browse").json()
    cards = {item["word"]: item for item in browsed["cards"]}
    assert "**runs**" in cards["run"]["front"]
    assert cards["quasar"]["front"] == "**quasar**"


def test_bury_card_excludes_from_queue_and_can_unbury(client):
    """掩埋的卡不进入学习队列与每日分配；可在浏览器恢复。"""
    register(client, "bury@example.com")
    client.put("/api/cards/settings", json={"new_cards_per_day": 0})
    created = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    assert created.status_code == 200
    card_id = client.get("/api/cards/browse", params={"q": "run"}).json()["cards"][0]["id"]

    buried = client.post(f"/api/cards/{card_id}/bury")
    assert buried.status_code == 200
    assert buried.json()["card"]["buried"] is True

    queue = client.get("/api/cards", params={"card_type": "reading", "extra_new": 5}).json()
    assert queue["queue"] == []
    assert queue["extra_available"] == 0

    browsed = client.get("/api/cards/browse", params={"state": "buried"}).json()
    assert [item["id"] for item in browsed["cards"]] == [card_id]

    unburied = client.post(f"/api/cards/{card_id}/unbury")
    assert unburied.status_code == 200
    assert unburied.json()["card"]["buried"] is False

    queue2 = client.get("/api/cards", params={"card_type": "reading", "extra_new": 5}).json()
    assert len(queue2["queue"]) == 1

    missing = client.post("/api/cards/999999/bury")
    assert missing.status_code == 404


def test_cloze_and_reading_fronts_use_word_not_hint(client, monkeypatch):
    """带括号注解的词（taxi (verb)）制卡时，正面必须挖空/高亮单词本身，
    注解只用于选义，不能留在句子匹配里导致挖空失败。"""
    register(client, "hint-front@example.com")

    def fake_generate(_db, _user_id, words, _card_template="cloze"):
        results = {}
        for word in words:
            results[word] = {
                "w": word,
                "m": "（飞机）滑行",
                "e": "After landing, pilots taxi the aircraft slowly toward the assigned gate.",
            }
        return (
            results,
            {},
            1,
            {"ai_wait_seconds": 0.0, "format_retry_count": 0, "db_write_seconds": 0.0},
        )

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_card_content_in_batches", fake_generate
    )

    cloze = client.post(
        "/api/card-studio/cards",
        json={"words": ["taxi (verb)"], "card_type": "cloze"},
    )
    assert cloze.status_code == 200
    assert cloze.json()["created"] == 1
    cloze_card = client.get(
        "/api/cards", params={"card_type": "cloze"}
    ).json()["new"][0]
    assert "______" in cloze_card["front"]
    assert "taxi" not in cloze_card["front"]
    assert "taxi" in cloze_card["back"]
    assert cloze_card["word"] == "taxi"
    assert "(verb)" not in cloze_card["word"]

    reading = client.post(
        "/api/card-studio/cards",
        json={"words": ["taxi (verb)"], "card_type": "reading"},
    )
    assert reading.status_code == 200
    assert reading.json()["created"] == 1
    reading_card = client.get(
        "/api/cards", params={"card_type": "reading"}
    ).json()["new"][0]
    assert "**taxi**" in reading_card["front"]
    assert reading_card["word"] == "taxi"
    assert "(verb)" not in reading_card["word"]


def test_card_studio_rejects_invalid_ai_sentence_structure(client, monkeypatch):
    register(client, "invalid-ai-card@example.com")

    def fake_generate(_db, _user_id, words, _card_template="reading"):
        examples = {
            "quasar": "The distant object was easy to see through the telescope.",
            "run": "I run every day because a short run keeps me active.",
        }
        return (
            {
                word: {"w": word, "m": "测试释义", "e": examples[word]}
                for word in words
            },
            {},
            1,
            {"ai_wait_seconds": 0.0, "format_retry_count": 0, "db_write_seconds": 0.0},
        )

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_card_content_in_batches", fake_generate
    )
    reading = client.post(
        "/api/card-studio/cards",
        json={"words": ["quasar"], "card_type": "reading"},
    )
    assert reading.status_code == 200
    assert reading.json()["created"] == 0
    assert "未包含目标词" in reading.json()["failed"][0]

    cloze = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "cloze"},
    )
    assert cloze.status_code == 200
    assert cloze.json()["created"] == 0
    assert "只挖空一次" in cloze.json()["failed"][0]


def test_card_studio_batches_uncached_words_without_per_word_lookup(client, monkeypatch):
    register(client, "batch-card-studio@example.com")
    calls = []
    storage_checks = []

    def fail_single_lookup(*_args, **_kwargs):
        raise AssertionError("批量制卡不应逐词调用查词接口")

    def fake_batch(_db, _user_id, words, _card_template):
        calls.append(list(words))
        results = {}
        for word in words:
            results[word] = {
                "w": word,
                "m": "n. | a test target | 测试目标",
                "e": f"Readers can understand {word} from this clear example sentence.",
            }
        return (
            results,
            {},
            1,
            {"ai_wait_seconds": 0.0, "format_retry_count": 0, "db_write_seconds": 0.0},
        )

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.explain_lookup", fail_single_lookup
    )
    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_card_content_in_batches", fake_batch
    )
    monkeypatch.setattr(
        "app.routes.card_routes._require_storage_space",
        lambda _db, _user_id, additional_bytes: storage_checks.append(additional_bytes),
    )
    generated = client.post(
        "/api/card-studio/cards",
        json={
            "words": ["batchalpha", "batchbeta"],
            "card_type": "reading",
        },
    )

    assert generated.status_code == 200
    assert generated.json()["created"] == 2
    assert generated.json()["ai_requests"] == 1
    assert "ai_wait_seconds" in generated.json()["timings"]
    assert "db_write_seconds" in generated.json()["timings"]
    assert calls == [["batchalpha", "batchbeta"]]
    assert len(storage_checks) == 1
    assert storage_checks[0] > 0


def test_creating_card_marks_saved_word_mid_and_preserves_review_evidence(client):
    register(client, "saved-to-card@example.com")
    lookup = client.post("/api/lookups", json={"text": "run"}).json()["lookup"]
    assert client.post(f"/api/lookups/{lookup['id']}/save").status_code == 200
    assert client.get("/api/words", params={"q": "run"}).json()["words"]
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    assert made.status_code == 200
    assert made.json()["created"] == 1
    # 制卡后词保留在词库并标记为 mid
    words = client.get("/api/words", params={"q": "run"}).json()["words"]
    assert words and words[0]["status"] == "mid"
    card = client.get("/api/cards").json()["new"][0]
    reviewed = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "good",
            "action_id": "saved-to-card-action",
            "expected_revision": card["revision"],
        },
    )
    assert reviewed.status_code == 200
    assert client.get("/api/cards/browse", params={"q": "run"}).json()["total"] == 1
    db = SessionLocal()
    try:
        assert db.get(Card, card["id"]) is not None
        assert db.query(ReviewLog).filter(ReviewLog.card_id == card["id"]).count() == 1
    finally:
        db.close()

def test_delete_single_card_keeps_word_as_mid_until_last_card_gone(client):
    register(client, "delete-card@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    cards = client.get("/api/cards/browse", params={"q": "run"}).json()["cards"]
    assert len(cards) == 2

    first = client.delete(f"/api/cards/{cards[0]['id']}")
    assert first.status_code == 200
    # 仍有另一张卡：仍算 mid
    words = client.get("/api/words", params={"q": "run"}).json()["words"]
    assert words and words[0]["status"] == "mid"

    second = client.delete(f"/api/cards/{cards[1]['id']}")
    assert second.status_code == 200
    assert client.get("/api/cards/browse", params={"q": "run"}).json()["total"] == 0
    # 最后一张卡删除后：mid 失效，恢复为 hard
    words = client.get("/api/words", params={"q": "run"}).json()["words"]
    assert words and words[0]["status"] == "hard"


def test_batch_delete_learning_cards_downgrades_mid_to_hard(client):
    register(client, "batch-delete-mid@example.com")
    for word in ("run", "set", "point"):
        made = client.post(
            "/api/card-studio/cards",
            json={"words": [word], "card_type": "reading"},
        )
        assert made.status_code == 200
        assert made.json()["created"] == 1

    result = client.post(
        "/api/cards/delete-batch",
        json={"words": ["run", "set"]},
    )
    assert result.status_code == 200
    assert result.json()["deleted"] == 2
    run_cards = client.get("/api/cards/browse", params={"q": "run"}).json()["cards"]
    assert all(card["word"] != "run" for card in run_cards)
    set_cards = client.get("/api/cards/browse", params={"q": "set"}).json()["cards"]
    assert all(card["word"] != "set" for card in set_cards)
    point_cards = client.get("/api/cards/browse", params={"q": "point"}).json()["cards"]
    assert any(card["word"] == "point" for card in point_cards)
    words = client.get("/api/words").json()["words"]
    statuses = {item["word"]: item["status"] for item in words}
    assert statuses["run"] == "hard"
    assert statuses["set"] == "hard"
    assert statuses["point"] == "mid"


def test_batch_delete_cards_by_card_ids(client):
    register(client, "batch-delete-cards@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    client.post(
        "/api/card-studio/cards",
        json={"words": ["set"], "card_type": "reading"},
    )
    cards = client.get("/api/cards/browse", params={"card_type": "reading"}).json()["cards"]
    assert len(cards) == 2
    result = client.post(
        "/api/cards/delete-batch",
        json={"card_ids": [cards[0]["id"], cards[1]["id"]]},
    )
    assert result.status_code == 200
    assert result.json()["deleted"] == 2
    assert client.get(
        "/api/cards/browse", params={"card_type": "reading"}
    ).json()["total"] == 0


def test_batch_delete_matches_anki_label_suffix(client):
    register(client, "batch-delete-suffix@example.com")
    user_id = (
        SessionLocal()
        .query(User.id)
        .filter(User.email == "batch-delete-suffix@example.com")
        .scalar()
    )
    db = SessionLocal()
    try:
        db.add(
            Card(
                user_id=user_id,
                word="run [abc123]",
                card_type="anki",
                front="run",
                back="back",
            )
        )
        db.commit()
    finally:
        db.close()

    result = client.post("/api/cards/delete-batch", json={"words": ["run"]})
    assert result.status_code == 200
    assert result.json()["deleted"] == 1
    assert client.get("/api/words", params={"q": "run"}).json()["words"] == []


def test_batch_delete_card_downgrades_mid_to_hard(client):
    register(client, "batch-delete-card@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    result = client.post("/api/cards/delete-batch", json={"words": ["run"]})
    assert result.status_code == 200
    assert result.json()["deleted"] == 1
    words = client.get("/api/words", params={"q": "run"}).json()["words"]
    assert words and words[0]["status"] == "hard"


def test_card_batch_delete_does_not_remove_saved_word_without_card(client):
    register(client, "batch-delete-saved-nocard@example.com")
    lookup = client.post("/api/lookups", json={"text": "flabbergast"}).json()["lookup"]
    assert client.post(f"/api/lookups/{lookup['id']}/save").status_code == 200
    assert client.get("/api/words", params={"q": "flabbergast"}).json()["words"]

    result = client.post(
        "/api/cards/delete-batch", json={"words": ["flabbergast"]}
    )
    assert result.status_code == 200
    assert client.get("/api/words", params={"q": "flabbergast"}).json()["words"]


def test_wordlists_endpoint_lists_builtin_lists(client):
    register(client, "wordlists@example.com")
    response = client.get("/api/wordlists")
    assert response.status_code == 200
    lists = response.json()["lists"]
    ids = {item["id"] for item in lists}
    assert ids == {
        "primary", "junior", "senior", "cet4", "cet6",
        "kaoyan", "ielts", "toefl", "gre", "tem4", "tem8",
        "sat", "act", "awl", "coca5k", "mba", "zhicheng",
        "oxford3000", "oxford5000", "longman3000", "longman9000",
        "collins1", "collins2", "collins3", "collins4", "collins5",
        "vocabcom1000", "ngsl_core", "ngsl_spoken", "nawl", "avl", "eap_science",
        "bsl", "toeic", "phave150", "academic_collocations", "medical", "fitness",
        "ndl", "legal", "programming", "finance", "cefr_a1", "cefr_a2", "cefr_b1",
        "cefr_b2", "cefr_c1", "cefr_c2",
    }
    by_id = {item["id"]: item for item in lists}
    assert by_id["primary"]["name"] == "小学大纲"
    assert by_id["primary"]["count"] > 0


def test_card_targets_from_builtin_list(client):
    register(client, "builtin@example.com")
    response = client.post(
        "/api/card-studio/targets",
        json={
            "source": "builtin",
            "list_id": "primary",
            "count": 5,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "builtin"
    assert data["count"] == 5
    words = [item["word"] for item in data["words"]]
    assert all(isinstance(w, str) and w for w in words)


def test_card_targets_builtin_includes_non_ngsl_words_by_default(client):
    """默认不做 NGSL 筛选：不在 NGSL 的词（如短语行）也要能取出来。"""
    register(client, "builtin-all@example.com")
    response = client.post(
        "/api/card-studio/targets",
        json={
            "source": "builtin",
            "list_id": "primary",
            "count": 5000,
        },
    )
    assert response.status_code == 200
    ranks = [item["rank"] for item in response.json()["words"]]
    assert any(rank is None for rank in ranks), "默认应包含不在 NGSL 的词"


def test_card_targets_builtin_sorted_by_ngsl_rank_by_default(client):
    """默认排序按 NGSL 排名：有排名的按升序，未知词（None）排最后。"""
    register(client, "builtin-sorted@example.com")
    response = client.post(
        "/api/card-studio/targets",
        json={"source": "builtin", "list_id": "primary", "count": 5000},
    )
    assert response.status_code == 200
    ranks = [item["rank"] for item in response.json()["words"]]
    known = [r for r in ranks if r is not None]
    unknown_start = ranks.index(None) if None in ranks else len(ranks)
    assert known == sorted(known), "有排名的词应按 NGSL 排名升序"
    assert all(r is None for r in ranks[unknown_start:]), "未知词应排在最后"


def test_card_targets_builtin_rank_filter_needs_ngsl_filter_flag(client):
    """只有显式开启 ngsl_filter 才按排名区间过滤。"""
    register(client, "builtin-filter@example.com")
    response = client.post(
        "/api/card-studio/targets",
        json={
            "source": "builtin",
            "list_id": "primary",
            "from_rank": 1,
            "to_rank": 500,
            "count": 5000,
            "ngsl_filter": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["words"]
    assert all(1 <= item["rank"] <= 500 for item in response.json()["words"])


def test_card_targets_builtin_rejects_unknown_list(client):
    register(client, "builtin-bad@example.com")
    response = client.post(
        "/api/card-studio/targets",
        json={"source": "builtin", "list_id": "nope", "count": 5},
    )
    assert response.status_code == 400


def test_next_review_date_uses_site_timezone(client):
    import datetime as dt
    from zoneinfo import ZoneInfo

    from app import config

    register(client, "tz-date@example.com")
    client.post(
        "/api/card-studio/cards",
        json={"card_type": "general", "words": ["harbor"]},
    )
    queue = client.get("/api/cards").json()
    card = queue["new"][0]
    reviewed = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "easy",
            "action_id": "tz-date-1",
            "expected_revision": card["revision"],
        },
    )
    assert reviewed.status_code == 200
    reviewed_card = reviewed.json()["card"]
    tz = ZoneInfo(config.APP_TIMEZONE)
    due_at = dt.datetime.fromisoformat(reviewed_card["due_at"])
    expected = due_at.replace(tzinfo=dt.timezone.utc).astimezone(tz).date().isoformat()
    assert reviewed_card["next_review_date"] == expected


def test_card_targets_builtin_rank_filter_flag_applies_range(client):
    register(client, "builtin-rank@example.com")
    response = client.post(
        "/api/card-studio/targets",
        json={
            "source": "builtin",
            "list_id": "primary",
            "from_rank": 1,
            "to_rank": 500,
            "count": 50,
            "ngsl_filter": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["words"]
    assert all(1 <= item["rank"] <= 500 for item in response.json()["words"])


def test_anki_import_gzip_uses_apkg_decompress_limit(client, monkeypatch):
    import gzip

    from app import config
    from app.anki_exchange import AnkiExchangeError

    register(client, "apkg-gzip-limit@example.com")

    def fake_parse(_data, _max_cards):
        raise AnkiExchangeError("模拟解析失败")

    monkeypatch.setattr("app.routes.card_routes.anki_exchange.parse_apkg", fake_parse)
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1024)
    monkeypatch.setattr(config, "MAX_APKG_UPLOAD_BYTES", 4096)
    payload = gzip.compress(b"x" * 2048)
    response = client.post(
        "/api/cards/anki/import?filename=cards.apkg",
        content=payload,
        headers={
            "Content-Type": "application/octet-stream",
            "x-upload-encoding": "gzip",
        },
    )
    assert response.status_code == 400
    assert "模拟解析失败" in response.json()["detail"]


def test_expressions_targets_exclude_existing_same_type_fronts(client):
    """口语表达需求提取：已有同类型卡的需求（按 front 比对）被去重，
    未制卡的需求保留；换类型提取不去重。"""
    register(client, "expressions-dedup@example.com")
    existing_need = "对朋友说：婉拒邀请（不想去又不扫兴）"
    created = client.post(
        "/api/card-studio/cards",
        json={"card_type": "speaking", "words": [existing_need]},
    )
    assert created.status_code == 200
    assert created.json()["created"] == 1

    targets = client.post(
        "/api/card-studio/targets",
        json={
            "source": "expressions",
            "text": existing_need + "\n对路人说：礼貌地问路",
            "card_type": "speaking",
        },
    ).json()
    words = [item["word"] for item in targets["words"]]
    assert existing_need not in words  # 已有 speaking 卡 → 去重
    assert "对路人说：礼貌地问路" in words

    # 换一种卡片类型：同一需求不去重（目标卡类型不同）。
    other = client.post(
        "/api/card-studio/targets",
        json={
            "source": "expressions",
            "text": existing_need,
            "card_type": "general",
        },
    ).json()
    assert [item["word"] for item in other["words"]] == [existing_need]

    # 纯英文/无中文的行不是合法表达需求，被解析器过滤；
    # existing_need 已有 speaking 卡，同样被去重 → 结果为空。
    latin_only = client.post(
        "/api/card-studio/targets",
        json={
            "source": "expressions",
            "text": "just english words\n" + existing_need,
            "card_type": "speaking",
        },
    ).json()
    assert latin_only["words"] == []
