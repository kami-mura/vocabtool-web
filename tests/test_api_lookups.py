from types import SimpleNamespace

from tests.conftest import register


def test_guest_lookup_allows_twenty_then_requires_login(client):
    for index in range(20):
        res = client.post("/api/lookups", json={"text": "run"})
        assert res.status_code == 200, res.text
        assert res.json()["guest_remaining"] == 19 - index
    blocked = client.post("/api/lookups", json={"text": "run"})
    assert blocked.status_code == 429
    assert "登录" in blocked.json()["detail"]


def test_guest_can_use_quick_and_qa_sharing_guest_quota(client, monkeypatch):
    from app import ai as ai_mod

    def fake_quick(_db, _uid, text):
        return {"explanation": "【释义】\n竞技场", "headword": "arena", "rank": 123}, None

    def fake_answer(_db, _uid, question):
        return "lie 表示主动躺下，lay 表示放置或下蛋。", None

    monkeypatch.setattr(ai_mod, "quick_lookup", fake_quick)
    monkeypatch.setattr(ai_mod, "answer_question", fake_answer)

    quick = client.post("/api/lookups/quick", json={"text": "arena"})
    assert quick.status_code == 200, quick.text
    assert quick.json()["lookup"]["headword"] == "arena"
    assert quick.json()["guest_remaining"] == 19

    qa = client.post("/api/lookups/question", json={"question": "lie 和 lay 的区别？"})
    assert qa.status_code == 200, qa.text
    assert "lie" in qa.json()["answer"]
    assert qa.json()["guest_remaining"] == 18

    # 游客查询不写历史（历史接口本身要求登录）；游客共享同一份 20 次体验额度。
    for _ in range(18):
        res = client.post("/api/lookups", json={"text": "run"})
        assert res.status_code == 200
    blocked = client.post("/api/lookups/quick", json={"text": "arena"})
    assert blocked.status_code == 429
    assert "登录" in blocked.json()["detail"]


def test_quick_lookup_and_qa_and_topic_endpoints(client, monkeypatch):
    register(client, "quick-qa@example.com")
    from app import ai as ai_mod

    def fake_quick(_db, _uid, text):
        return {"explanation": "【释义】\n竞技场", "headword": "arena", "rank": 123}, None

    def fake_answer(_db, _uid, question):
        return "lie 表示主动躺下，lay 表示放置或下蛋。", None

    monkeypatch.setattr(ai_mod, "quick_lookup", fake_quick)
    monkeypatch.setattr(ai_mod, "answer_question", fake_answer)

    quick = client.post("/api/lookups/quick", json={"text": "arena"})
    assert quick.status_code == 200
    assert quick.json()["lookup"]["headword"] == "arena"
    assert quick.json()["lookup"]["rank"] == 123

    qa = client.post("/api/lookups/question", json={"question": "lie 和 lay 的区别？"})
    assert qa.status_code == 200
    assert "lie" in qa.json()["answer"]

    topic = client.post("/api/words/topic", json={"topic": "sports", "count": 10})
    assert topic.status_code == 400
    assert "DEEPSEEK_API_KEY" in topic.json()["detail"]

    bad_quick = client.post("/api/lookups/quick", json={"text": ""})
    assert bad_quick.status_code == 422


def test_quick_and_word_qa_results_can_be_saved_to_vocabulary(client, monkeypatch):
    register(client, "quick-save@example.com")
    from app import ai as ai_mod

    def fake_quick(_db, _uid, text):
        return {"explanation": "【释义】\n竞技场", "headword": "arena", "rank": 123}, None

    def fake_answer(_db, _uid, question):
        return "apple 是苹果的意思。", None

    monkeypatch.setattr(ai_mod, "quick_lookup", fake_quick)
    monkeypatch.setattr(ai_mod, "answer_question", fake_answer)

    quick = client.post("/api/lookups/quick", json={"text": "arena"})
    assert quick.status_code == 200
    lookup = quick.json()["lookup"]
    assert lookup.get("id")
    assert lookup["query_type"] == "word"
    assert lookup["saved"] is False
    saved = client.post(f"/api/lookups/{lookup['id']}/save")
    assert saved.status_code == 200
    assert saved.json()["created"] is True

    qa = client.post("/api/lookups/question", json={"question": "apple"})
    assert qa.status_code == 200
    qa_lookup = qa.json().get("lookup") or {}
    assert qa_lookup.get("id")
    assert qa_lookup["query_type"] == "word"
    saved2 = client.post(f"/api/lookups/{qa_lookup['id']}/save")
    assert saved2.status_code == 200
    assert saved2.json()["created"] is True

    qa2 = client.post("/api/lookups/question", json={"question": "lie 和 lay 的区别？"})
    assert qa2.status_code == 200
    assert "lookup" not in qa2.json()


def test_priority_select_endpoint(client, monkeypatch):
    register(client, "priority@example.com")
    from app import ai as ai_mod

    monkeypatch.setattr(
        ai_mod,
        "select_priority_words",
        lambda _db, _uid, candidates, count: (
            {"selected": ["apple"], "remaining": ["cherry"]},
            None,
        ),
    )
    res = client.post(
        "/api/words/priority-select",
        json={"candidates": ["apple", "cherry"], "count": 1},
    )
    assert res.status_code == 200
    assert res.json()["selected"] == ["apple"]
    assert res.json()["remaining"] == ["cherry"]

    bad = client.post("/api/words/priority-select", json={"candidates": [], "count": 1})
    assert bad.status_code == 422


def test_lookup_history_is_separated_by_mode_and_deletable(client, monkeypatch):
    register(client, "history-mode@example.com")
    from app import ai as ai_mod

    monkeypatch.setattr(
        ai_mod,
        "quick_lookup",
        lambda _db, _uid, text: ({"explanation": "【释义】\n竞技场", "headword": text, "rank": 1}, None),
    )
    monkeypatch.setattr(
        ai_mod,
        "answer_question",
        lambda _db, _uid, question: ("这是回答。", None),
    )

    normal = client.post("/api/lookups", json={"text": "run"})
    assert normal.status_code == 200
    quick = client.post("/api/lookups/quick", json={"text": "arena"})
    assert quick.status_code == 200
    qa = client.post("/api/lookups/question", json={"question": "lie 和 lay 的区别？"})
    assert qa.status_code == 200

    normal_list = client.get("/api/lookups?mode=normal").json()
    quick_list = client.get("/api/lookups?mode=quick").json()
    qa_list = client.get("/api/lookups?mode=qa").json()
    assert [item["query"] for item in normal_list["lookups"]] == ["run"]
    assert [item["query"] for item in quick_list["lookups"]] == ["arena"]
    assert [item["query"] for item in qa_list["lookups"]] == ["lie 和 lay 的区别？"]

    quick_id = quick_list["lookups"][0]["id"]
    assert client.delete(f"/api/lookups/{quick_id}").status_code == 200
    assert client.get("/api/lookups?mode=quick").json()["lookups"] == []
    assert client.delete(f"/api/lookups/{quick_id}").status_code == 404

    # 全部删除只清空当前模式，不影响其他模式。
    assert client.get("/api/lookups?mode=normal").json()["lookups"]
    assert client.delete("/api/lookups?mode=normal").status_code == 200
    assert client.get("/api/lookups?mode=normal").json()["lookups"] == []
    assert len(client.get("/api/lookups?mode=qa").json()["lookups"]) == 1


def test_lookup_history_is_private(client):
    register(client, "lookup@example.com")
    lookup = client.post("/api/lookups", json={"text": "mull it over"})
    assert lookup.status_code == 200
    data = lookup.json()
    assert data["lookup"]["query_type"] == "phrase"
    assert data["ai_enabled"] is False
    lookup_id = data["lookup"]["id"]
    assert client.get("/api/lookups").json()["lookups"][0]["query"] == "mull it over"

    register(client, "lookup2@example.com")
    assert client.post(f"/api/lookups/{lookup_id}/reopen").status_code == 404
    assert client.delete(f"/api/lookups/{lookup_id}").status_code == 404


def test_streamlit_lookup_accepts_terms_but_rejects_sentences(client):
    register(client, "lookup-validation@example.com")
    assert client.post("/api/lookups", json={"text": "run"}).status_code == 200
    assert client.post(
        "/api/lookups", json={"text": "mull it over"}
    ).status_code == 200
    chinese = client.post("/api/lookups", json={"text": "活力"})
    assert chinese.status_code == 200
    assert chinese.json()["lookup"]["query_type"] == "gloss"

    title = client.post("/api/lookups", json={"text": "3 idiots"})
    assert title.status_code == 200
    assert title.json()["lookup"]["query_type"] == "phrase"

    sentence = client.post(
        "/api/lookups", json={"text": "She has finished the book."}
    )
    # 查词不做单词清理：句子直接交给 AI 处理
    assert sentence.status_code == 200
    question = client.post(
        "/api/lookups", json={"text": "what does run mean"}
    )
    assert question.status_code == 200
    assert len(client.get("/api/lookups").json()["lookups"]) == 6

    too_long = client.post(
        "/api/lookups", json={"text": "a" * 300}
    )
    assert too_long.status_code == 400


def test_builtin_ngsl_lookup_skips_deepseek_and_reports_speed(client, monkeypatch):
    register(client, "builtin-lookup@example.com")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("内置词命中时不应调用 DeepSeek")

    monkeypatch.setattr("app.routes.lookup_routes.ai_mod.ai_enabled", lambda: True)
    monkeypatch.setattr("app.routes.lookup_routes.ai_mod.explain_lookup", fail_if_called)
    result = client.post("/api/lookups", json={"text": "run"})
    assert result.status_code == 200
    data = result.json()
    assert data["lookup_source"] == "builtin"
    assert isinstance(data["elapsed_ms"], int)
    assert data["lookup"]["explanation"].startswith("run /rʌn/")
    assert data["lookup"]["ngsl_rank"] == 194
    assert data["lookup"]["card_front"] == "She runs every morning before work."
    assert data["lookup"]["has_card"] is False
    assert data["lookup"]["saved"] is False
    assert data["lookup"]["easy"] is False
    assert data["lookup"]["word_status"] is None
    assert client.get("/api/words", params={"q": "run"}).json()["words"] == []
    history_count = len(client.get("/api/lookups").json()["lookups"])
    saved = client.post(f"/api/lookups/{data['lookup']['id']}/save")
    assert saved.status_code == 200
    assert saved.json()["created"] is True
    assert client.post(f"/api/lookups/{data['lookup']['id']}/save").json()["created"] is False
    reopened = client.post(f"/api/lookups/{data['lookup']['id']}/reopen")
    assert reopened.status_code == 200
    assert reopened.json()["lookup_source"] == "history"
    assert reopened.json()["lookup"]["saved"] is True
    assert reopened.json()["lookup"]["easy"] is False
    assert reopened.json()["lookup"]["word_status"] == "hard"
    assert len(client.get("/api/lookups").json()["lookups"]) == history_count

    # 查词结果不再提供制卡入口（查词只能加入生词库），卡片只能从制卡向导创建。
    made = client.post(
        "/api/card-studio/cards",
        json={"words": ["run"], "card_type": "reading"},
    )
    assert made.status_code == 200
    assert made.json()["created"] == 1
    reopened = client.post(f"/api/lookups/{data['lookup']['id']}/reopen")
    assert reopened.json()["lookup"]["has_card"] is True
    assert reopened.json()["lookup"]["saved"] is False
    assert reopened.json()["lookup"]["easy"] is False
    assert reopened.json()["lookup"]["word_status"] == "mid"
    cannot_save = client.post(f"/api/lookups/{data['lookup']['id']}/save")
    assert cannot_save.status_code == 409
    # 制卡后词保留在词库并显示为 mid（已制卡）
    words = client.get("/api/words", params={"q": "run"}).json()["words"]
    assert words and words[0]["status"] == "mid"

    info = client.get("/api/lookups/builtin-info")
    assert info.status_code == 200
    assert info.json()["builtin_count"] == 15
    assert "run" in info.json()["sample_terms"]


def test_easy_lookup_can_be_added_to_saved_words(client):
    register(client, "easy-lookup-save@example.com")
    marked = client.post(
        "/api/words/batch-status",
        json={"words": ["run"], "status": "easy"},
    )
    assert marked.status_code == 200
    assert marked.json()["updated"] == 1

    lookup = client.post("/api/lookups", json={"text": "run"}).json()["lookup"]
    assert lookup["has_card"] is False
    assert lookup["saved"] is True  # Easy 词已在生词库
    assert lookup["easy"] is True
    assert lookup["word_status"] == "easy"

    saved = client.post(f"/api/lookups/{lookup['id']}/save")
    assert saved.status_code == 200
    assert saved.json()["created"] is True
    assert saved.json()["promoted_from_easy"] is True

    reopened = client.post(f"/api/lookups/{lookup['id']}/reopen").json()["lookup"]
    assert reopened["has_card"] is False
    assert reopened["saved"] is True
    assert reopened["easy"] is False
    assert reopened["word_status"] == "hard"


def test_successful_deepseek_lookup_is_saved_and_reused_locally(client, monkeypatch):
    register(client, "growing-cache@example.com")
    calls = []

    def fake_lookup(db, user_id, text, query_type):
        calls.append(text)
        return {
            "explanation": """adaptive /əˈdæptɪv/
1. 适应性强的 | Able to adjust to change
• An adaptive system improves through experience.
一个适应性系统会通过经验不断改进。""",
            "card_front": "An adaptive system improves through experience.",
            "card_back": "适应性强的 | Able to adjust to change\n一个适应性系统会通过经验不断改进。",
        }, None, True

    monkeypatch.setattr("app.routes.lookup_routes.ai_mod.ai_enabled", lambda: True)
    monkeypatch.setattr("app.routes.lookup_routes.ai_mod.explain_lookup", fake_lookup)
    first = client.post("/api/lookups", json={"text": "adaptive"}).json()
    second = client.post("/api/lookups", json={"text": "Adaptive"}).json()
    third = client.post("/api/lookups", json={"text": "adaptive"}).json()
    assert first["lookup_source"] == "deepseek"
    # 严格区分大小写：Adaptive 不再复用 adaptive 的缓存。
    assert second["lookup_source"] == "deepseek"
    assert third["lookup_source"] == "local_cache"
    assert first["lookup"]["explanation"] == second["lookup"]["explanation"]
    assert calls == ["adaptive", "Adaptive"]

    info = client.get("/api/lookups/builtin-info").json()
    assert info["learned_count"] == 2


def test_lookup_does_not_add_to_saved_words_without_user_action(client):
    register(client, "lookup-sync@example.com")
    assert client.get("/api/words", params={"q": "run"}).json()["words"] == []
    lookup = client.post("/api/lookups", json={"text": "running"})
    assert lookup.status_code == 200
    assert client.get("/api/words", params={"q": "run"}).json()["words"] == []


def test_lookup_preserves_user_case_and_distinguishes_march(client, monkeypatch):
    register(client, "case-lookup@example.com")
    from app import ai as ai_mod

    seen = []

    def fake_explain(_db, _uid, text, query_type):
        seen.append(text)
        return {
            "explanation": f"explain {text}",
            "card_front": text,
            "card_back": f"def {text}",
        }, None, True

    monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai_mod, "explain_lookup", fake_explain)

    first = client.post("/api/lookups", json={"text": "March"})
    second = client.post("/api/lookups", json={"text": "march"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert seen == ["March", "march"]
    assert first.json()["lookup"]["query"] == "March"
    assert second.json()["lookup"]["query"] == "march"

    history = client.get("/api/lookups").json()["lookups"]
    assert [item["query"] for item in history] == ["march", "March"]

    # 全小写变形词仍按词头身份缓存（running -> run），行为不变。
    running = client.post("/api/lookups", json={"text": "running"})
    assert running.status_code == 200
    assert running.json()["lookup"]["query"] == "run"


def test_builtin_lookup_is_case_sensitive(client):
    register(client, "builtin-case@example.com")
    lower = client.post("/api/lookups", json={"text": "apple"})
    upper = client.post("/api/lookups", json={"text": "Apple"})
    assert lower.status_code == 200
    assert upper.status_code == 200
    assert lower.json()["lookup_source"] == "builtin"
    # 大写 Apple 不再命中内置小写 apple，进入 AI/不可用分支。
    assert upper.json()["lookup_source"] != "builtin"


def test_failed_lookup_does_not_add_hard_even_when_reopened(client):
    register(client, "failed-lookup-status@example.com")
    result = client.post("/api/lookups", json={"text": "zzqvocabfailure"})
    assert result.status_code == 200
    assert result.json()["lookup"]["explanation"] == ""
    assert client.get(
        "/api/words", params={"q": "zzqvocabfailure"}
    ).json()["words"] == []
    reopened = client.post(f"/api/lookups/{result.json()['lookup']['id']}/reopen")
    assert reopened.status_code == 200
    assert client.get(
        "/api/words", params={"q": "zzqvocabfailure"}
    ).json()["words"] == []


def test_ai_corrected_headword_marks_spelling_note(client, monkeypatch):
    """AI 对拼写错误词返回正确词的释义时，从词头识别并提示拼写错误。"""
    register(client, "ai-corrected@example.com")

    def fake_lookup(db, user_id, text, query_type):
        return {
            "explanation": "environment /ɪnˈvaɪrənmənt/\n1. 环境 | The surroundings in which someone lives",
            "card_front": "The environment needs our protection.",
            "card_back": "环境\nThe environment needs our protection.",
        }, None, True

    monkeypatch.setattr("app.routes.lookup_routes.ai_mod.ai_enabled", lambda: True)
    monkeypatch.setattr("app.routes.lookup_routes.ai_mod.explain_lookup", fake_lookup)
    result = client.post("/api/lookups", json={"text": "environemnt"})
    assert result.status_code == 200
    data = result.json()
    assert data["spelling_note"] == {
        "original": "environemnt",
        "corrected": "environment",
    }
    # 结果展示正确词的释义。
    assert data["lookup"]["explanation"].startswith("environment /")
    assert data["lookup"]["query"] == "environemnt"


def test_correctly_spelled_word_has_no_suggestion(client):
    """词表内正确的词不返回拼写建议。"""
    register(client, "correct-no-suggestion@example.com")
    result = client.post("/api/lookups", json={"text": "environment"})
    assert result.status_code == 200
    assert result.json()["spelling_note"] is None
    assert result.json()["lookup"]["query"] == "environment"


def test_vocab_suggest_correction_units():
    from app import vocab

    assert vocab.suggest_correction("environemnt") == "environment"
    assert vocab.suggest_correction("adress") == "address"
    assert vocab.suggest_correction("langauge") == "language"
    assert vocab.suggest_correction("definately") == "definitely"
    assert vocab.suggest_correction("environment") is None
    assert vocab.suggest_correction("zzzqqqvvv") is None
    assert vocab.suggest_correction("a") is None


def test_lookup_always_cleans_and_preserves_pos(client, monkeypatch):
    """查词一律清洗（running -> run）；括号词性保留给 AI；词源/问答不走此路径。"""
    register(client, "always-clean@example.com")
    from app import ai as ai_mod

    seen = []

    def fake_explain(_db, _uid, text, query_type):
        seen.append(text)
        return {
            "explanation": f"explain {text}",
            "card_front": text,
            "card_back": f"def {text}",
        }, None, True

    monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai_mod, "explain_lookup", fake_explain)

    cleaned = client.post("/api/lookups", json={"text": "running"})
    assert cleaned.status_code == 200
    assert cleaned.json()["lookup"]["query"] == "run"

    # 括号词性：词形还原只作用于词头，原文（含词性）给 AI
    pos = client.post("/api/lookups", json={"text": "quasar (n.)"})
    assert pos.status_code == 200
    assert pos.json()["lookup"]["query"] == "quasar (n.)"
    assert pos.json()["lookup"]["query_type"] == "word"
    assert seen == ["quasar (n.)"]
    run_pos = client.post("/api/lookups", json={"text": "run (v.)"})
    assert run_pos.json()["lookup_source"] == "builtin"


def test_lookup_pos_suffix_classified_as_word_with_separate_cache(client, monkeypatch):
    """括号词性按词头归类为 word：命中内置词库，缓存按含词性的键隔离。"""
    register(client, "pos-word@example.com")
    from app import ai as ai_mod

    calls = []

    def fake_explain(_db, _uid, text, query_type, reserve_quota=True):
        calls.append((text, query_type))
        return {
            "explanation": f"explain {text}",
            "card_front": text,
            "card_back": f"def {text}",
        }, None, True

    monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai_mod, "explain_lookup", fake_explain)

    # 普通查词先建缓存
    first = client.post("/api/lookups", json={"text": "adaptive"})
    assert first.status_code == 200
    assert calls == [("adaptive", "word")]

    # 带括号词性：分类为 word、原文给 AI；词性不同不命中普通词的缓存
    pos = client.post("/api/lookups", json={"text": "adaptive (adj.)"})
    assert pos.status_code == 200
    body = pos.json()
    assert body["lookup"]["query_type"] == "word"
    assert body["lookup"]["query"] == "adaptive (adj.)"
    assert body["lookup_source"] == "deepseek"
    assert calls == [("adaptive", "word"), ("adaptive (adj.)", "word")]

    # 词形还原只作用于词头：running (v.) -> run (v.)；run 命中内置词库不再调 AI
    run_pos = client.post("/api/lookups", json={"text": "running (v.)"})
    assert run_pos.status_code == 200
    rbody = run_pos.json()
    assert rbody["lookup"]["query"] == "run (v.)"
    assert rbody["lookup"]["query_type"] == "word"
    assert rbody["lookup_source"] == "builtin"
    assert calls == [("adaptive", "word"), ("adaptive (adj.)", "word")]


def test_lookup_pos_variants_do_not_share_cache(client, monkeypatch):
    """quasar (n.) 与 quasar (v.) 各自使用独立的缓存键，释义不串味。"""
    from app import ai as ai_mod

    calls = []

    def fake_explain(_db, _uid, text, query_type, reserve_quota=True):
        calls.append(text)
        return {
            "explanation": f"explain {text}",
            "card_front": text,
            "card_back": f"def {text}",
        }, None, True

    monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai_mod, "explain_lookup", fake_explain)

    run_v = client.post("/api/lookups", json={"text": "quasar (n.)"})
    assert run_v.status_code == 200
    assert run_v.json()["lookup_source"] == "deepseek"
    assert run_v.json()["lookup"]["explanation"] == "explain quasar (n.)"

    run_n = client.post("/api/lookups", json={"text": "quasar (v.)"})
    assert run_n.status_code == 200
    assert run_n.json()["lookup_source"] == "deepseek"
    assert run_n.json()["lookup"]["explanation"] == "explain quasar (v.)"
    assert calls == ["quasar (n.)", "quasar (v.)"]

    run_v_again = client.post("/api/lookups", json={"text": "quasar (n.)"})
    assert run_v_again.status_code == 200
    assert run_v_again.json()["lookup_source"] == "local_cache"
    assert run_v_again.json()["lookup"]["explanation"] == "explain quasar (n.)"
    assert calls == ["quasar (n.)", "quasar (v.)"]


def test_guest_spelling_correction_charges_ai_quota_once(client, monkeypatch):
    """拼写纠错对同一请求第二次查 AI 时复用第一次的配额，只扣一次。"""
    from app import ai as ai_mod
    from app import config
    from app.db import SessionLocal
    from app.models import GuestAiQuota

    monkeypatch.setattr(config, "GUEST_AI_DAILY_LIMIT", 10)
    monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
    calls = []

    def fake_explain(db, uid, text, query_type, reserve_quota=True):
        calls.append((text, reserve_quota))
        if uid is None and reserve_quota:
            ai_mod.guest_ai_quota_reserve(db, need=1)
        if text == "environemnt":
            # 首次调用已实际扣费（登录用户语义，或游客尚未退还时），纠错应复用。
            return None, "deepseek-v4-flash 查询暂时失败", True
        return {
            "explanation": "environment /ɪnˈvaɪrənmənt/\n1. 环境 | The surroundings\n• The environment needs our protection.\n环境需要我们的保护。",
            "card_front": "The environment needs our protection.",
            "card_back": "环境\nThe environment needs our protection.",
        }, None, True

    monkeypatch.setattr(ai_mod, "explain_lookup", fake_explain)
    result = client.post("/api/lookups", json={"text": "environemnt"})
    assert result.status_code == 200
    data = result.json()
    assert data["spelling_note"] == {
        "original": "environemnt",
        "corrected": "environment",
    }
    assert data["lookup"]["explanation"].startswith("environment")
    # 第二次查询复用首次已扣配额，不再预占。
    assert calls == [("environemnt", True), ("environment", False)]
    db = SessionLocal()
    try:
        day = ai_mod._quota_day()
        row = db.get(GuestAiQuota, day)
        assert row is not None
        assert row.count == 1
    finally:
        db.close()


def test_spelling_correction_reserves_quota_when_first_call_did_not_charge(
    client, monkeypatch
):
    """首次 AI 查询未实际扣费（配额不足被拒）时，拼写纠错必须重新预占，
    不能在配额耗尽后获得免费查词通道。"""
    from app import ai as ai_mod

    monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
    calls = []

    def fake_explain(_db, uid, text, query_type, reserve_quota=True):
        calls.append((text, reserve_quota))
        return None, "今日 AI 请求已达上限", False

    monkeypatch.setattr(ai_mod, "explain_lookup", fake_explain)
    result = client.post("/api/lookups", json={"text": "environemnt"})
    assert result.status_code == 200
    data = result.json()
    assert data["lookup"]["explanation"] == ""
    assert data["ai_error"] == "今日 AI 请求已达上限"
    # 两次调用都带 reserve_quota=True：纠错路径不绕过配额。
    assert calls == [("environemnt", True), ("environment", True)]


def test_guest_lookup_ai_quota_exhausted_returns_quota_error(client, monkeypatch):
    from app import ai as ai_mod
    from app import config
    from app.db import SessionLocal
    from app.models import GuestAiQuota

    monkeypatch.setattr(config, "GUEST_AI_DAILY_LIMIT", 1)
    monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
    real_explain = ai_mod.explain_lookup

    def fake_explain(db, uid, text, query_type, reserve_quota=True):
        return real_explain(db, uid, text, query_type, reserve_quota=reserve_quota)

    monkeypatch.setattr(ai_mod, "explain_lookup", fake_explain)
    db = SessionLocal()
    try:
        db.add(GuestAiQuota(day=ai_mod._quota_day(), count=1))
        db.commit()
    finally:
        db.close()
    result = client.post("/api/lookups", json={"text": "quasar (n.)"})
    assert result.status_code == 200
    data = result.json()
    assert data["lookup"]["explanation"] == ""
    assert data["ai_error"]
    assert "上限" in data["ai_error"]


def test_guest_quick_lookup_charges_ai_quota_and_refunds_on_failure(client, monkeypatch):
    from app import ai as ai_mod
    from app import config
    from app.db import SessionLocal
    from app.models import GuestAiQuota

    monkeypatch.setattr(config, "GUEST_AI_DAILY_LIMIT", 10)
    monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
    content = (
        "【释义】\n竞技场；活动场所\n\n【底层逻辑】\n沙地上的舞台。\n\n"
        "【🌱 Etymology 词源史诗】\narena 来自拉丁语 harena，意思是沙子。"
    )

    def fake_chat(_client, **kwargs):
        return _api_fake_chat_response(content)

    monkeypatch.setattr(ai_mod, "_chat_completion", fake_chat)
    quick = client.post("/api/lookups/quick", json={"text": "arena"})
    assert quick.status_code == 200, quick.text
    assert quick.json()["lookup"]["headword"] == "arena"
    db = SessionLocal()
    try:
        day = ai_mod._quota_day()
        row = db.get(GuestAiQuota, day)
        assert row is not None
        assert row.count == 1
    finally:
        db.close()

    def failing_chat(_client, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr(ai_mod, "_chat_completion", failing_chat)
    failed = client.post("/api/lookups/quick", json={"text": "arena"})
    assert failed.status_code == 400
    db = SessionLocal()
    try:
        day = ai_mod._quota_day()
        row = db.get(GuestAiQuota, day)
        assert row is not None
        assert row.count == 1
    finally:
        db.close()


def test_guest_answer_question_charges_ai_quota_and_refunds_on_failure(client, monkeypatch):
    from app import ai as ai_mod
    from app import config
    from app.db import SessionLocal
    from app.models import GuestAiQuota

    monkeypatch.setattr(config, "GUEST_AI_DAILY_LIMIT", 10)
    monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)

    def fake_chat(_client, **kwargs):
        return _api_fake_chat_response("lie 表示主动躺下，lay 表示放置或下蛋。")

    monkeypatch.setattr(ai_mod, "_chat_completion", fake_chat)
    qa = client.post("/api/lookups/question", json={"question": "lie 和 lay 的区别？"})
    assert qa.status_code == 200, qa.text
    assert "lie" in qa.json()["answer"]
    db = SessionLocal()
    try:
        day = ai_mod._quota_day()
        row = db.get(GuestAiQuota, day)
        assert row is not None
        assert row.count == 1
    finally:
        db.close()

    def failing_chat(_client, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr(ai_mod, "_chat_completion", failing_chat)
    failed = client.post("/api/lookups/question", json={"question": "lie 和 lay 的区别？"})
    assert failed.status_code == 400
    db = SessionLocal()
    try:
        day = ai_mod._quota_day()
        row = db.get(GuestAiQuota, day)
        assert row is not None
        assert row.count == 1
    finally:
        db.close()


def test_guest_quick_and_answer_reject_exhausted_ai_quota(client, monkeypatch):
    from app import ai as ai_mod
    from app import config
    from app.db import SessionLocal
    from app.models import GuestAiQuota

    monkeypatch.setattr(config, "GUEST_AI_DAILY_LIMIT", 1)
    monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
    db = SessionLocal()
    try:
        db.add(GuestAiQuota(day=ai_mod._quota_day(), count=1))
        db.commit()
    finally:
        db.close()
    quick = client.post("/api/lookups/quick", json={"text": "arena"})
    assert quick.status_code == 400
    assert "上限" in quick.json()["detail"]
    qa = client.post("/api/lookups/question", json={"question": "lie 和 lay 的区别？"})
    assert qa.status_code == 400
    assert "上限" in qa.json()["detail"]


def _api_fake_chat_response(content: str):
    class Message:
        pass

    class Choice:
        pass

    class Response:
        pass

    message = Message()
    message.content = content
    choice = Choice()
    choice.message = message
    response = Response()
    response.choices = [choice]
    return response


def test_long_phrase_lookup_cache_uses_hashed_key(client, monkeypatch):
    """超过 80 字符的查询写入 LookupCache 用 sha256 摘要键，且能再次命中。"""
    from app import ai as ai_mod
    from app.db import SessionLocal
    from app.models import LookupCache

    long_phrase = (
        "the quick brown fox jumps over the lazy dog and then runs around the "
        "forest looking for shelter"
    )
    assert len(long_phrase) > 80
    calls = []

    def fake_explain(_db, _uid, text, query_type, reserve_quota=True):
        calls.append(text)
        return {
            "explanation": f"explain {text}",
            "card_front": text,
            "card_back": f"def {text}",
        }, None, True

    monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai_mod, "explain_lookup", fake_explain)

    first = client.post("/api/lookups", json={"text": long_phrase})
    assert first.status_code == 200
    assert first.json()["lookup_source"] == "deepseek"
    assert calls == [long_phrase]

    second = client.post("/api/lookups", json={"text": long_phrase})
    assert second.status_code == 200
    assert second.json()["lookup_source"] == "local_cache"
    assert second.json()["lookup"]["explanation"] == f"explain {long_phrase}"
    assert calls == [long_phrase]

    db = SessionLocal()
    try:
        import hashlib

        stored_key = hashlib.sha256(long_phrase.encode()).hexdigest()
        assert db.get(LookupCache, long_phrase) is None
        row = db.get(LookupCache, stored_key)
        assert row is not None
        assert row.explanation == f"explain {long_phrase}"
    finally:
        db.close()


def test_ai_failure_falls_back_to_existing_word_entry(client, monkeypatch):
    """AI 已启用但调用失败时，若 WordEntry 已有该词释义则使用本地释义。"""
    register(client, "wordentry-fallback@example.com")
    from app import ai as ai_mod
    from app.db import SessionLocal
    from app.models import WordEntry

    db = SessionLocal()
    try:
        db.add(
            WordEntry(
                word="quasar",
                pos="n.",
                en_def="a very distant bright object in space",
                zh_def="类星体",
                source="deepseek",
            )
        )
        db.commit()
    finally:
        db.close()

    def fake_explain(_db, _uid, text, query_type, reserve_quota=True):
        return None, "deepseek-v4-flash 查询暂时失败，请稍后重试", False

    monkeypatch.setattr(ai_mod, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai_mod, "explain_lookup", fake_explain)
    result = client.post("/api/lookups", json={"text": "quasar"})
    assert result.status_code == 200
    data = result.json()
    assert data["lookup"]["explanation"]
    assert "类星体" in data["lookup"]["explanation"]
    assert data["lookup_source"] == "word_cache"


def test_trusted_proxy_ips_restrict_forwarded_identity(monkeypatch):
    """配置 TRUSTED_PROXY_IPS 后，仅白名单来源的转发头被信任。"""
    from app import config
    from app.api_support import _anonymous_request_identity, _is_trusted_proxy_peer

    monkeypatch.setattr(config, "TRUSTED_PROXY_IPS", "127.0.0.1")
    assert _is_trusted_proxy_peer("127.0.0.1") is True
    assert _is_trusted_proxy_peer("10.0.0.5") is False
    assert _is_trusted_proxy_peer("::1") is False

    request = SimpleNamespace(
        client=SimpleNamespace(host="10.0.0.5"),
        headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"},
    )
    assert _anonymous_request_identity(request) == "10.0.0.5"

    monkeypatch.setattr(config, "TRUSTED_PROXY_IPS", "loopback")
    assert _is_trusted_proxy_peer("127.0.0.1") is True
    assert _is_trusted_proxy_peer("10.0.0.5") is False


def test_reserve_guest_lookup_atomic_refuses_at_limit(monkeypatch):
    from fastapi import HTTPException

    from app.db import SessionLocal
    from app.models import GuestLookupQuota
    from app.routes import lookup_routes

    monkeypatch.setattr(
        lookup_routes, "_anonymous_request_identity", lambda _request: "unit-anon"
    )
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
    key = lookup_routes._guest_lookup_key(request)
    db = SessionLocal()
    try:
        db.add(GuestLookupQuota(key=key, count=20))
        db.commit()
        try:
            lookup_routes._reserve_guest_lookup(db, request)
        except HTTPException as exc:
            assert exc.status_code == 429
            assert "登录" in exc.detail
        else:
            raise AssertionError("配额用尽时应拒绝")
        row = db.get(GuestLookupQuota, key)
        assert row.count == 20
    finally:
        db.close()


def test_reserve_guest_lookup_atomic_increments_below_limit(monkeypatch):
    from app.db import SessionLocal
    from app.models import GuestLookupQuota
    from app.routes import lookup_routes

    monkeypatch.setattr(
        lookup_routes, "_anonymous_request_identity", lambda _request: "unit-anon-2"
    )
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
    key = lookup_routes._guest_lookup_key(request)
    db = SessionLocal()
    try:
        db.add(GuestLookupQuota(key=key, count=5))
        db.commit()
        remaining = lookup_routes._reserve_guest_lookup(db, request)
        assert remaining == 14
        row = db.get(GuestLookupQuota, key)
        assert row.count == 6
        remaining = lookup_routes._reserve_guest_lookup(db, request)
        assert remaining == 13
        row = db.get(GuestLookupQuota, key)
        assert row.count == 7
    finally:
        db.close()


def test_analyze_keeps_surface_forms_when_normalize_off():
    from app import vocab

    text = "Running is fun. He runs every day. Running again."
    normalized = vocab.analyze(text)
    assert normalized["run"] == 3  # running×2 + runs 都还原到 run
    assert "running" not in normalized
    raw = vocab.analyze(text, normalize=False)
    assert raw["running"] == 2
    assert raw["runs"] == 1
    assert "run" not in raw
