"""AI 文章生成功能测试：学习完成门槛、单词范围筛选、高亮与 prompt。"""

import datetime as dt

import pytest

from app import ai as ai_mod
from app.ai import (
    _article_highlight_items,
    _article_length_guidance,
    _article_max_tokens,
    _build_article_prompt,
    _highlight_article_paragraph,
    _parse_article_json,
)
from app.db import SessionLocal
from app.models import Card, Corpus, User
from app.routes.card_routes import _article_word_groups
from app.vocab import rank_of


def _force_due(card_id):
    """把卡片到期时间改到过去，模拟卡片已到期。"""
    db = SessionLocal()
    try:
        row = db.get(Card, card_id)
        row.due_at = dt.datetime.now(dt.timezone.utc).replace(
            tzinfo=None
        ) - dt.timedelta(seconds=1)
        db.commit()
    finally:
        db.close()


def _graduate_known(client, card_id, count=2):
    """按到期规则连续点“认识”，完成多次复习。"""
    db = SessionLocal()
    try:
        revision = int(db.get(Card, card_id).revision or 0)
    finally:
        db.close()
    reviewed = None
    for index in range(count):
        # 新卡第一次评分本来就未到期；只有后续复习需要先等到期。
        if index > 0:
            _force_due(card_id)
        reviewed = client.post(
            f"/api/cards/{card_id}/review",
            json={
                "rating": "easy",
                "action_id": f"graduate-{card_id}-{index}",
                "expected_revision": revision,
            },
        )
        assert reviewed.status_code == 200
        revision = reviewed.json()["card"]["revision"]
    return reviewed


@pytest.fixture(autouse=True)
def _fake_card_generation(monkeypatch):
    """制卡统一走确定性假结果，避免连真实 DeepSeek。"""

    def fake_generate(_db, _user_id, words, card_template="reading"):
        results = {}
        errors = {}
        for word in words:
            if card_template == "general":
                results[word] = {"w": word, "m": "测试释义", "e": ""}
            elif card_template == "cloze":
                results[word] = {
                    "w": word,
                    "m": "测试释义",
                    "e": f"The {word} appears clearly in this test sentence, so you can answer.",
                }
            else:
                results[word] = {
                    "w": word,
                    "m": "n. | a test meaning | 测试释义",
                    "e": f"The {word} appears clearly in this test sentence.",
                }
        timings = {
            "ai_wait_seconds": 0.0,
            "format_retry_count": 0,
            "db_write_seconds": 0.0,
        }
        return results, errors, 1, timings

    monkeypatch.setattr(ai_mod, "generate_card_content_in_batches", fake_generate)


def _register(client, email):
    res = client.post(
        "/api/register", json={"email": email, "password": "password123"}
    )
    assert res.status_code == 200, res.text
    return res


def _user_id(email):
    db = SessionLocal()
    try:
        return db.query(User.id).filter(User.email == email).scalar()
    finally:
        db.close()


def _add_card(db, user_id, word, card_type="general"):
    card = Card(
        user_id=user_id,
        word=word,
        card_type=card_type,
        front=word,
        back="释义",
        context="",
    )
    db.add(card)
    db.commit()
    return card


def _prepare_article_user(client, email):
    """注册并学完 run/develop，今天点过「重来」的 point，返回可生成文章的用户。"""
    _register(client, email)
    created = client.post(
        "/api/card-studio/cards",
        json={"words": ["run", "develop", "point"], "card_type": "general"},
    )
    assert created.status_code == 200
    queue = client.get("/api/cards").json()["queue"]
    for item in queue:
        if item["word"] == "point":
            # 今天点「重来」：point 成为今日短文来源。
            reviewed = client.post(
                f"/api/cards/{item['id']}/review",
                json={
                    "rating": "again",
                    "action_id": f"prepare-again-{item['id']}",
                    "expected_revision": item["revision"],
                },
            )
            assert reviewed.status_code == 200
            continue
        _graduate_known(client, item["id"], 2)


def _age_article(corpus_id, days=1):
    """把文章创建时间改到过去，模拟非今天生成的文章。"""
    db = SessionLocal()
    try:
        corpus = db.get(Corpus, corpus_id)
        corpus.created_at = dt.datetime.now(dt.timezone.utc).replace(
            tzinfo=None
        ) - dt.timedelta(days=days)
        db.commit()
    finally:
        db.close()


def _fake_generate_article(
    _db, _user_id, new_words, review_words, thinking=False, effort=None, **_kwargs
):
    result = {
        "title": "Test Article",
        "paragraphs": [
            "<p>" + ", ".join(new_words + review_words) + "</p>"
        ],
        "new_words": new_words,
        "review_words": review_words,
    }
    return result, None


def _start_article_for_test(client, **extra):
    response = client.post("/api/cards/article", json=extra or {})
    assert response.status_code == 200
    assert response.json()["state"] == "generating"
    latest_payload = client.get("/api/cards/article/latest").json()
    assert latest_payload["generation"]["state"] == "done"
    return response, latest_payload["article"]


# ---------- 纯函数 ----------


def test_article_prompt_follows_user_template():
    prompt = _build_article_prompt(["run", "pale blue"], ["develop"], 3000)
    assert "- run" in prompt
    assert "- pale blue" in prompt
    assert "- develop" in prompt
    assert "适合英语学习者背单词的短文" in prompt
    assert "about 18-36 words" in prompt
    assert "3000 words" in prompt
    assert "所有目标单词必须在同一篇短文中自然出现" in prompt
    assert "句子要简单、口语化、有画面感" in prompt
    assert "以自然完整为先" in prompt
    assert "离奇事件" in prompt
    assert '"paragraphs"' in prompt
    # 所有 JSON 花括号都已转义，format 不会再报错。
    assert "{" in prompt and "{{" not in prompt


def test_article_length_scales_with_target_count():
    short = _build_article_prompt(["run"], ["develop", "point"], 3000)
    assert "about 18-36 words" in short
    medium = _build_article_prompt(["run"] * 10, [], 3000)
    assert "about 60-120 words" in medium
    maximum = _build_article_prompt([f"word{i}" for i in range(12)], [], 3000)
    assert "about 72-144 words" in maximum
    assert _article_length_guidance(1) == ("about 6-12 words", 3, 20)
    assert _article_length_guidance(12) == ("about 72-144 words", 36, 192)


def test_article_word_groups_are_balanced_and_never_exceed_twelve():
    words = [f"word{i}" for i in range(45)]
    groups = _article_word_groups(words)
    assert [len(group) for group in groups] == [12, 11, 11, 11]
    assert [word for group in groups for word in group] == words
    assert _article_word_groups(["one"]) == [["one"]]


def test_article_output_token_limit_scales_with_article_length():
    assert _article_max_tokens(1) == 65536
    assert _article_max_tokens(100) == 65536
    assert _article_max_tokens(1000) == 65536


def test_article_parse_accepts_plain_and_fenced_json():
    parsed = _parse_article_json(
        '{"title": "My Day", "paragraphs": ["First.", "  Second.  "]}'
    )
    assert parsed == ("My Day", ["First.", "Second."])
    fenced = _parse_article_json(
        'Some prefix\n```json\n{"title": "T", "paragraphs": ["A"]}\n```'
    )
    assert fenced == ("T", ["A"])
    assert _parse_article_json("not json at all") is None
    assert _parse_article_json('{"title": "T", "paragraphs": []}') is None


def test_article_highlight_marks_target_words_with_inflections():
    items = _article_highlight_items(["run"], ["develop"])
    html = _highlight_article_paragraph(
        "She runs every day, and the project developed well.", items
    )
    assert '<mark class="article-word">runs</mark>' in html
    assert '<mark class="article-word">developed</mark>' in html
    assert html.startswith("She ")
    assert html.endswith(" well.")


def test_article_highlight_is_case_sensitive_for_capitalized_targets():
    items = _article_highlight_items(["March"], [])
    html = _highlight_article_paragraph(
        "March comes after February, and we march in step.", items
    )
    assert '<mark class="article-word">March</mark>' in html
    assert '<mark class="article-word">march</mark>' not in html


def test_article_highlight_escapes_ai_html_before_marking():
    items = _article_highlight_items(["run"], [])
    html = _highlight_article_paragraph(
        'Run <script>alert(1)</script> now!', items
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert '<mark class="article-word">Run</mark>' in html


def test_article_highlight_longer_word_wins_over_shorter():
    items = _article_highlight_items(["the"], ["therapist"])
    html = _highlight_article_paragraph(
        "Therapist saw the cat.", items
    )
    # therapist 整体高亮，独立出现的 the 也高亮，不嵌套。
    assert html.count("<mark") == 2
    assert '<mark class="article-word">Therapist</mark>' in html
    assert '<mark class="article-word">the</mark>' in html


def test_article_highlight_no_targets_returns_escaped_text():
    assert _highlight_article_paragraph("a < b", []) == "a &lt; b"


def _register_user(db, email="ai-article-unit@example.com"):
    from app.auth import hash_password

    pw_hash, salt = hash_password("password123")
    user = User(email=email, password_hash=pw_hash, salt=salt)
    db.add(user)
    db.commit()
    return user


def test_generate_article_calls_ai_and_returns_highlighted_html(monkeypatch):
    """完整链路：额度记账 → AI 调用 → JSON 解析 → 新学/复习高亮。"""
    db = SessionLocal()
    try:
        user = _register_user(db)
        class FakeResponse:
            class FakeMessage:
                content = (
                    '{"title": "My Day", "paragraphs": ['
                    '"I ran fast, and the plan developed well with a taxi."]'
                    "}"
                )

            choices = [type("Choice", (), {"message": FakeMessage})()]

        calls = []
        monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
        monkeypatch.setattr(ai_mod, "_new_ai_client", lambda: object())

        def fake_completion(_client, **kwargs):
            calls.append(kwargs.get("messages"))
            return FakeResponse()

        monkeypatch.setattr(ai_mod, "_chat_completion", fake_completion)
        result, error = ai_mod.generate_article(
            db, user.id, ["run", "taxi"], ["develop"]
        )
        assert error is None
        assert result["title"] == "My Day"
        assert '<mark class="article-word">ran</mark>' in result["paragraphs"][0]
        assert '<mark class="article-word">developed</mark>' in result["paragraphs"][0]
        # prompt 里包含全部目标词与 JSON 输出契约。
        joined = " ".join(
            str(message.get("content") or "")
            for message in calls[0]
        )
        assert "- run" in joined
        assert "- taxi" in joined
        assert "- develop" in joined
        assert '"paragraphs"' in joined
    finally:
        db.close()


def test_generate_article_accepts_single_word(monkeypatch):
    """学多少就输入多少：1 个词也能生成文章。"""
    db = SessionLocal()
    try:
        user = _register_user(db, "ai-article-few@example.com")
        monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)

        class FakeResponse:
            class FakeMessage:
                content = (
                    '{"title": "One Word", "paragraphs": ["Run is the word."]}'
                )

            choices = [type("Choice", (), {"message": FakeMessage})()]

        monkeypatch.setattr(ai_mod, "_new_ai_client", lambda: object())
        monkeypatch.setattr(
            ai_mod,
            "_chat_completion",
            lambda _client, **kwargs: FakeResponse(),
        )
        result, error = ai_mod.generate_article(db, user.id, ["run"], [])
        assert error is None
        assert result["title"] == "One Word"
        assert '<mark class="article-word">Run</mark>' in result["paragraphs"][0]
    finally:
        db.close()


def test_generate_article_rejects_more_than_twelve_targets(monkeypatch):
    """单篇最多 12 个目标词；上层今日短文负责先分组。"""
    db = SessionLocal()
    try:
        user = _register_user(db, "ai-article-many@example.com")
        monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
        words = [f"word{i}" for i in range(13)]
        calls = []
        monkeypatch.setattr(ai_mod, "_chat_completion", lambda *_a, **_k: calls.append(1))
        result, error = ai_mod.generate_article(db, user.id, words, [])
        assert result is None
        assert error == "每篇最多使用 12 个目标词"
        assert calls == []
    finally:
        db.close()


def _fake_article_response(responses):
    """按顺序返回文章 JSON；耗尽后继续返回最后一个。"""
    def fake_completion(_client, **kwargs):
        content = responses[-1]
        if len(responses) > 1:
            content = responses.pop(0)
        message = type("M", (), {"content": content})()
        return type("R", (), {"choices": [type("C", (), {"message": message})()]})()
    return fake_completion


def test_generate_article_rewrites_complete_draft_for_missing_targets(monkeypatch):
    """AI 漏词时完整重写，不能把补写段落拼到旧稿后面。"""
    db = SessionLocal()
    try:
        user = _register_user(db, "article-missing-fix@example.com")
        monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
        monkeypatch.setattr(ai_mod, "_new_ai_client", lambda: object())
        responses = [
            '{"title": "My Day", "paragraphs": ["I ran fast, and the plan developed well."]}',
            '{"title": "A Better Trip", "paragraphs": ["I ran to the taxi, where my developed plan helped us leave safely today."]}',
        ]
        calls = []

        def fake_completion(_client, **kwargs):
            calls.append(kwargs)
            return _fake_article_response(responses)(_client, **kwargs)

        monkeypatch.setattr(ai_mod, "_chat_completion", fake_completion)
        result, error = ai_mod.generate_article(
            db, user.id, ["run", "taxi", "develop"], [],
            thinking=True, effort="max",
        )
        assert error is None
        assert result["title"] == "A Better Trip"
        assert len(result["paragraphs"]) == 1
        assert "taxi" in result["paragraphs"][0]
        assert "plan developed well" not in result["paragraphs"][0]
        assert len(calls) == 2
        assert calls[0]["thinking"] is True
        assert calls[0]["reasoning_effort"] == "max"
        repair_prompt = calls[1]["messages"][-1]["content"]
        assert "Rewrite the COMPLETE article from scratch" in repair_prompt
        assert "Do not continue or append" in repair_prompt
    finally:
        db.close()


def test_generate_article_rejects_a_rewrite_that_still_misses_targets(monkeypatch):
    """完整重写仍遗漏目标词时拒绝保存。"""
    db = SessionLocal()
    try:
        user = _register_user(db, "article-missing-fail@example.com")
        monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
        monkeypatch.setattr(ai_mod, "_new_ai_client", lambda: object())
        monkeypatch.setattr(
            ai_mod,
            "_chat_completion",
            _fake_article_response(
                [
                    '{"title": "T", "paragraphs": ["Run fast and plan well."]}',
                    '{"title": "T", "paragraphs": ["The plan was ready at last."]}',
                ]
            ),
        )
        result, error = ai_mod.generate_article(
            db, user.id, ["run", "taxi", "develop"], []
        )
        assert result is None
        assert error is not None
        assert "未包含全部目标词" in error
    finally:
        db.close()


# ---------- 端点 ----------


def test_article_available_without_completing_tasks(client, monkeypatch):
    """随时可用：不需要完成今日学习任务，今天点过「重来」的词即可生成。"""
    _register(client, "article-gate@example.com")
    created = client.post(
        "/api/card-studio/cards",
        json={"words": ["run", "develop", "point"], "card_type": "general"},
    )
    assert created.status_code == 200
    queue = client.get("/api/cards").json()["queue"]
    assert len(queue) == 3
    # 只点「重来」，不完成学习任务。
    first = queue[0]
    reviewed = client.post(
        f"/api/cards/{first['id']}/review",
        json={
            "rating": "again",
            "action_id": "article-gate-again",
            "expected_revision": first["revision"],
        },
    )
    assert reviewed.status_code == 200

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", _fake_generate_article
    )
    _response, article = _start_article_for_test(client)
    assert article["article_title"] == "Test Article"


def test_free_user_can_generate_only_one_article_per_day(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "AI_FREE_DAILY_ARTICLE_LIMIT", 1)
    _prepare_article_user(client, "free-article-limit@example.com")
    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", _fake_generate_article
    )

    first, _article = _start_article_for_test(client)
    assert first.status_code == 200
    blocked = client.post("/api/cards/article")
    assert blocked.status_code == 429
    assert "今日免费短文额度已用完" in blocked.json()["detail"]


def test_own_api_key_bypasses_free_article_limit(client, monkeypatch):
    from app import config
    from app.models import AiFreeDailyQuota

    monkeypatch.setattr(config, "AI_FREE_DAILY_ARTICLE_LIMIT", 1)
    _prepare_article_user(client, "own-key-article-limit@example.com")
    credential = object()
    monkeypatch.setattr(
        "app.routes.card_routes._user_ai_credential", lambda _db, _user: credential
    )
    used_credentials = []

    def fake_with_own_key(
        db, user_id, new_words, review_words, *, user_api_key=None, **kwargs
    ):
        used_credentials.append(user_api_key)
        return _fake_generate_article(
            db, user_id, new_words, review_words, **kwargs
        )

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", fake_with_own_key
    )

    _start_article_for_test(client)
    _start_article_for_test(client)
    assert used_credentials == [credential, credential]

    db = SessionLocal()
    try:
        assert db.query(AiFreeDailyQuota).count() == 0
    finally:
        db.close()


def test_article_keeps_only_latest_with_naming_rule(client, monkeypatch):
    """生成新文章只保留最新一篇，标题按 日期-标题 命名（如 7.28-a good day）。"""
    _prepare_article_user(client, "article-shelf@example.com")
    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", _fake_generate_article
    )
    _first, data = _start_article_for_test(client)
    assert data["corpus_id"] is not None
    assert data["book_title"].startswith("8.")
    assert data["book_title"].endswith("-test article")

    _second, second_article = _start_article_for_test(client)
    assert second_article["book_title"].endswith("-test article")

    # 最新接口返回最近一篇。
    latest = client.get("/api/cards/article/latest").json()["article"]
    assert latest is not None
    assert latest["book_title"] == second_article["book_title"]
    # 只保留一篇 AI 文章。
    user_id = _user_id("article-shelf@example.com")
    db = SessionLocal()
    try:
        assert (
            db.query(Corpus)
            .filter(Corpus.user_id == user_id, Corpus.source_type == "ai")
            .count()
            == 1
        )
    finally:
        db.close()


def test_latest_hides_and_deletes_yesterday_article(client, monkeypatch):
    """最新文章接口只返回今天生成的；访问时清掉昨天的旧文章。"""
    _prepare_article_user(client, "article-old@example.com")
    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", _fake_generate_article
    )
    _old, old_article = _start_article_for_test(client)
    _age_article(old_article["corpus_id"])

    latest = client.get("/api/cards/article/latest").json()["article"]
    assert latest is None
    user_id = _user_id("article-old@example.com")
    db = SessionLocal()
    try:
        assert (
            db.query(Corpus)
            .filter(Corpus.user_id == user_id, Corpus.source_type == "ai")
            .count()
            == 0
        )
    finally:
        db.close()


def test_reading_hides_and_deletes_yesterday_article(client, monkeypatch):
    """阅读接口只允许今天生成的文章；访问旧文章时直接删除并返回 404。"""
    _prepare_article_user(client, "article-old-read@example.com")
    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", _fake_generate_article
    )
    _old, old_article = _start_article_for_test(client)
    _age_article(old_article["corpus_id"])

    assert (
        client.get(f"/api/corpora/{old_article['corpus_id']}/reading").status_code
        == 404
    )
    user_id = _user_id("article-old-read@example.com")
    db = SessionLocal()
    try:
        assert (
            db.query(Corpus)
            .filter(Corpus.user_id == user_id, Corpus.source_type == "ai")
            .count()
            == 0
        )
    finally:
        db.close()


def test_article_latest_returns_newest_article(client, monkeypatch):
    _register(client, "article-latest@example.com")
    created = client.post(
        "/api/card-studio/cards",
        json={"words": ["run", "develop", "point"], "card_type": "general"},
    )
    assert created.status_code == 200
    queue = client.get("/api/cards").json()["queue"]
    assert len(queue) == 3

    empty = client.get("/api/cards/article/latest").json()
    assert empty["article"] is None

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", _fake_generate_article
    )
    reviewed = client.post(
        f"/api/cards/{queue[0]['id']}/review",
        json={
            "rating": "again",
            "action_id": "article-latest-again",
            "expected_revision": queue[0]["revision"],
        },
    )
    assert reviewed.status_code == 200
    assert client.post("/api/cards/article").status_code == 200
    latest = client.get("/api/cards/article/latest").json()["article"]
    assert latest is not None
    assert latest["book_title"].endswith("-test article")
    assert latest["article_title"] == "Test Article"
    assert latest["paragraphs"]
    assert latest["word_count"] > 0
    # 目标单词按内置词库排名排序。
    article = client.get(
        f"/api/corpora/{latest['corpus_id']}/reading"
    ).json()["article"]
    words = article["target_words"]
    assert words == sorted(
        words, key=lambda w: (rank_of(w) or 10**9, w.lower())
    )


def test_article_highlights_phrase_targets(client, monkeypatch):
    _register(client, "article-phrase@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["mull it over"], "card_type": "reading"},
    )
    assert made.status_code == 200
    assert made.json()["created"] == 1
    card = client.get("/api/cards").json()["new"][0]
    reviewed = client.post(
        f"/api/cards/{card['id']}/review",
        json={
            "rating": "again",
            "action_id": "article-phrase-again",
            "expected_revision": card["revision"],
        },
    )
    assert reviewed.status_code == 200

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", _fake_generate_article
    )
    generated = client.post("/api/cards/article")
    assert generated.status_code == 200
    latest = client.get("/api/cards/article/latest").json()["article"]
    assert "mull it over" in latest["target_words"]
    assert "<mark" in latest["paragraphs"][0]


def test_article_treats_case_variants_as_distinct_words(client, monkeypatch):
    """march 与 March 是独立目标词：制卡分开、文章生成同时保留。"""
    _register(client, "article-case@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["march", "March"], "card_type": "general"},
    )
    assert made.status_code == 200
    assert made.json()["created"] == 2
    queue = client.get("/api/cards").json()["queue"]
    assert sorted(item["word"] for item in queue) == ["March", "march"]
    for card in queue:
        reviewed = client.post(
            f"/api/cards/{card['id']}/review",
            json={
                "rating": "again",
                "action_id": f"article-case-again-{card['id']}",
                "expected_revision": card["revision"],
            },
        )
        assert reviewed.status_code == 200

    captured = {}

    def fake(_db, _uid, new_words, review_words, thinking=False, effort=None):
        captured["new_words"] = list(new_words)
        return {
            "title": "Test Article",
            "paragraphs": [
                "<p>" + ", ".join(new_words + review_words) + "</p>"
            ],
            "new_words": new_words,
            "review_words": review_words,
        }, None

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", fake
    )
    resp = client.post("/api/cards/article")
    assert resp.status_code == 200
    assert set(captured["new_words"]) == {"march", "March"}


def test_article_splits_large_word_sets_into_a_balanced_reading_pack(client, monkeypatch):
    """当天新词均匀分篇，每篇不超过 12 个目标词且全部覆盖。"""
    _register(client, "article-single@example.com")
    words = [f"word{i}" for i in range(45)]
    made = client.post(
        "/api/card-studio/cards",
        json={"words": words, "card_type": "general"},
    )
    assert made.status_code == 200
    user_id = _user_id("article-single@example.com")
    db = SessionLocal()
    try:
        card_ids = [
            row[0]
            for row in db.query(Card.id)
            .filter(Card.user_id == user_id)
            .all()
        ]
    finally:
        db.close()
    assert len(card_ids) == 45
    db = SessionLocal()
    try:
        revisions = {
            row[0]: int(row[1] or 0)
            for row in db.query(Card.id, Card.revision)
            .filter(Card.user_id == user_id)
            .all()
        }
    finally:
        db.close()
    for card_id in card_ids:
        reviewed = client.post(
            f"/api/cards/{card_id}/review",
            json={
                "rating": "again",
                "action_id": f"article-balance-again-{card_id}",
                "expected_revision": revisions[card_id],
            },
        )
        assert reviewed.status_code == 200

    calls = []

    def fake(_db, _uid, new_words, review_words, thinking=False, effort=None):
        calls.append((list(new_words), list(review_words)))
        return {
            "title": "One Article",
            "paragraphs": ["<p>" + ", ".join(new_words + review_words) + "</p>"],
            "new_words": new_words,
            "review_words": review_words,
            "word_count": len(new_words) + len(review_words),
        }, None

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", fake
    )
    _resp, article = _start_article_for_test(client)
    assert [len(new) for new, _review in calls] == [12, 11, 11, 11]
    assert all(review == [] for _new, review in calls)
    assert {word for new, _review in calls for word in new} == set(words)
    assert len(article["chapters"]) == 4
    assert {
        word
        for chapter in article["chapters"]
        for word in chapter["target_words"]
    } == set(words)


def test_article_includes_new_words_rated_again(client, monkeypatch):
    """今天新学但点了“不认识/again”的卡也要进入文章，不能因未通过被排除。"""
    _register(client, "article-again@example.com")
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["quasar"], "card_type": "general"},
    )
    assert made.status_code == 200
    first_card = client.get("/api/cards").json()["queue"][0]
    reviewed = client.post(
        f"/api/cards/{first_card['id']}/review",
        json={
            "rating": "again",
            "action_id": "article-again-action",
            "expected_revision": first_card["revision"],
        },
    )
    assert reviewed.status_code == 200

    captured = {}

    def fake(_db, _uid, new_words, review_words, thinking=False, effort=None):
        captured["new_words"] = list(new_words)
        return {
            "title": "Test Article",
            "paragraphs": ["<p>" + ", ".join(new_words + review_words) + "</p>"],
            "new_words": new_words,
            "review_words": review_words,
            "word_count": len(new_words) + len(review_words),
        }, None

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", fake
    )
    _resp, article = _start_article_for_test(client)
    assert captured["new_words"] == ["quasar"]
    assert article["target_words"] == ["quasar"]


def test_article_highlights_only_today_targets(client, monkeypatch):
    """文章只高亮今天的真实目标词，不把历史学习状态的词一起高亮。"""
    _prepare_article_user(client, "article-target-only@example.com")
    user_id = _user_id("article-target-only@example.com")
    db = SessionLocal()
    try:
        db.add(
            Card(
                user_id=user_id,
                word="bonus",
                card_type="general",
                front="bonus",
                back="extra",
            )
        )
        db.commit()
    finally:
        db.close()

    def fake_with_extra(
        _db, _user_id, new_words, review_words, thinking=False, effort=None
    ):
        return {
            "title": "Test Article",
            "paragraphs": [
                "<p>" + ", ".join(new_words + review_words) + " and bonus</p>"
            ],
            "new_words": new_words,
            "review_words": review_words,
        }, None

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", fake_with_extra
    )
    resp = client.post("/api/cards/article")
    assert resp.status_code == 200
    latest = client.get("/api/cards/article/latest").json()["article"]
    assert latest is not None
    paragraph = latest["paragraphs"][0]
    # 今天点过「重来」的词被高亮，历史学习词 bonus 不高亮。
    assert '<mark class="article-word">point</mark>' in paragraph
    assert '<mark class="article-word">bonus</mark>' not in paragraph
    assert "bonus" not in latest["target_words"]


def test_article_always_uses_thinking_max(client, monkeypatch):
    """短文固定用思考模式 max，不提供降级参数。"""
    _prepare_article_user(client, "article-mode@example.com")
    calls = []

    def spy_generate(
        _db, _user_id, new_words, review_words, thinking=False, effort=None
    ):
        calls.append((thinking, effort))
        return _fake_generate_article(
            _db, _user_id, new_words, review_words, thinking=thinking, effort=effort
        )

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", spy_generate
    )
    first = client.post("/api/cards/article")
    assert first.status_code == 200
    second = client.post("/api/cards/article")
    assert second.status_code == 200
    assert calls == [(True, "max"), (True, "max")]


def test_article_uses_only_today_again_cards(client, monkeypatch):
    """今日短文只用今天点过「重来」的卡片：复习卡点重来进入，只点认识的排除。"""
    _register(client, "article-split@example.com")
    created = client.post(
        "/api/card-studio/cards",
        json={"words": ["run", "develop", "point", "quasar"], "card_type": "general"},
    )
    assert created.status_code == 200
    queue = client.get("/api/cards").json()["queue"]
    assert len(queue) == 4
    # 今天先通过 3 张新卡（只点认识，不进入今日短文）。
    for item in queue:
        if item["word"] != "quasar":
            _graduate_known(client, item["id"], 1)
    # quasar 改成昨天到期：今天作为复习卡点「重来」，进入今日短文。
    db = SessionLocal()
    try:
        user_id = _user_id("article-split@example.com")
        quasar_card = (
            db.query(Card)
            .filter(Card.user_id == user_id, Card.word == "quasar")
            .first()
        )
        quasar_card.due_at = dt.datetime.now(dt.timezone.utc).replace(
            tzinfo=None
        ) - dt.timedelta(days=1)
        db.commit()
        quasar_id = quasar_card.id
        quasar_revision = int(quasar_card.revision or 0)
    finally:
        db.close()
    assert client.post(
        f"/api/cards/{quasar_id}/review",
        json={
            "rating": "again",
            "action_id": "article-split-quasar-again",
            "expected_revision": quasar_revision,
        },
    ).status_code == 200

    calls = []
    def spy_generate(
        _db, _user_id, new_words, review_words, thinking=False, effort=None
    ):
        calls.append((list(new_words), list(review_words)))
        return _fake_generate_article(
            _db, _user_id, new_words, review_words, thinking=thinking, effort=effort
        )

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", spy_generate
    )
    resp = client.post("/api/cards/article")
    assert resp.status_code == 200
    assert sorted(calls[-1][0]) == ["quasar"]
    assert calls[-1][1] == []


def test_article_includes_new_and_review_cards_rated_again(client, monkeypatch):
    """“今天点过重来”包含新卡和复习卡，但排除只点过认识的卡。"""
    _register(client, "article-again-source@example.com")
    created = client.post(
        "/api/card-studio/cards",
        json={"words": ["run", "develop", "point"], "card_type": "general"},
    )
    assert created.status_code == 200
    cards = {
        item["word"]: {"id": item["id"], "revision": item["revision"]}
        for item in client.get("/api/cards").json()["queue"]
    }

    first = client.post(
        f"/api/cards/{cards['run']['id']}/review",
        json={
            "rating": "again",
            "action_id": "again-source-run",
            "expected_revision": cards["run"]["revision"],
        },
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/cards/{cards['develop']['id']}/review",
        json={
            "rating": "easy",
            "action_id": "again-source-develop-1",
            "expected_revision": cards["develop"]["revision"],
        },
    )
    assert second.status_code == 200
    _force_due(cards["develop"]["id"])
    third = client.post(
        f"/api/cards/{cards['develop']['id']}/review",
        json={
            "rating": "again",
            "action_id": "again-source-develop-2",
            "expected_revision": second.json()["card"]["revision"],
        },
    )
    assert third.status_code == 200
    assert client.post(
        f"/api/cards/{cards['point']['id']}/review",
        json={
            "rating": "easy",
            "action_id": "again-source-point",
            "expected_revision": cards["point"]["revision"],
        },
    ).status_code == 200

    captured = []

    def spy_generate(
        _db, _user_id, new_words, review_words, thinking=False, effort=None
    ):
        captured.extend(new_words)
        assert review_words == []
        return _fake_generate_article(
            _db, _user_id, new_words, review_words, thinking=thinking, effort=effort
        )

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", spy_generate
    )
    response = client.post("/api/cards/article")
    assert response.status_code == 200
    assert set(captured) == {"run", "develop"}
    assert "point" not in captured


def test_article_available_after_completing_today_tasks(client, monkeypatch):
    """学完今日任务后，今天点过「重来」的词仍可生成今日短文。"""
    _register(client, "article-done@example.com")
    created = client.post(
        "/api/card-studio/cards",
        json={"words": ["run", "develop", "point"], "card_type": "general"},
    )
    assert created.status_code == 200
    queue = client.get("/api/cards").json()["queue"]
    assert len(queue) == 3
    develop = next(item for item in queue if item["word"] == "develop")
    # 学完全部 3 张新卡（每次“认识”等到期后再复习），
    # 任务完成后卡片移出任务队列。
    for item in queue:
        _graduate_known(client, item["id"], 2)
    done_queue = client.get("/api/cards").json()
    assert done_queue["queue"] == []
    assert done_queue["can_extra_new"] is True
    # 把 develop 提前到期并点「重来」，作为今日短文来源。
    _force_due(develop["id"])
    db = SessionLocal()
    try:
        develop_revision = int(db.get(Card, develop["id"]).revision or 0)
    finally:
        db.close()
    assert client.post(
        f"/api/cards/{develop['id']}/review",
        json={
            "rating": "again",
            "action_id": "article-done-develop-again",
            "expected_revision": develop_revision,
        },
    ).status_code == 200

    calls = []
    def spy_generate(
        _db, _user_id, new_words, review_words, thinking=False, effort=None
    ):
        calls.append((sorted(new_words), sorted(review_words)))
        return _fake_generate_article(
            _db, _user_id, new_words, review_words, thinking=thinking, effort=effort
        )

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", spy_generate
    )
    resp = client.post("/api/cards/article")
    assert resp.status_code == 200
    assert calls[-1][0] == ["develop"]
    assert calls[-1][1] == []


def test_article_deduplicates_same_word(client, monkeypatch):
    """同一单词有多张今天点过「重来」的卡时，在今日短文中只出现一次。"""
    _register(client, "article-priority@example.com")
    created = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "general"},
    )
    assert created.status_code == 200
    queue = client.get("/api/cards").json()["queue"]
    assert len(queue) == 1
    first = client.post(
        f"/api/cards/{queue[0]['id']}/review",
        json={
            "rating": "again",
            "action_id": "article-dedup-run-1",
            "expected_revision": queue[0]["revision"],
        },
    )
    assert first.status_code == 200
    # 再给 run 建一张 reading 卡并改成昨天到期：同词出现在复习队列。
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    assert made.status_code == 200
    db = SessionLocal()
    try:
        user_id = _user_id("article-priority@example.com")
        reading_run = (
            db.query(Card)
            .filter(
                Card.user_id == user_id,
                Card.word == "run",
                Card.card_type == "reading",
            )
            .first()
        )
        reading_run.due_at = dt.datetime.now(dt.timezone.utc).replace(
            tzinfo=None
        ) - dt.timedelta(days=1)
        db.commit()
        reading_id = reading_run.id
        reading_revision = int(reading_run.revision or 0)
    finally:
        db.close()
    assert client.post(
        f"/api/cards/{reading_id}/review",
        json={
            "rating": "again",
            "action_id": "article-dedup-reading",
            "expected_revision": reading_revision,
        },
    ).status_code == 200

    calls = []
    def spy_generate(
        _db, _user_id, new_words, review_words, thinking=False, effort=None
    ):
        calls.append((list(new_words), list(review_words)))
        return _fake_generate_article(
            _db, _user_id, new_words, review_words, thinking=thinking, effort=effort
        )

    monkeypatch.setattr(
        "app.routes.card_routes.ai_mod.generate_article", spy_generate
    )
    generated = client.post("/api/cards/article")
    assert generated.status_code == 200
    assert calls[-1][0] == ["run"]
    assert calls[-1][1] == []
