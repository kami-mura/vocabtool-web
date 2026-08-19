from types import SimpleNamespace

from app import ai, config
from app.ai import (
    _STREAMLIT_SIMPLE_LOOKUP_PROMPT,
    _build_speaking_front_prompts,
    _card_fields_from_streamlit_result,
    _card_generation_batch_size,
    _parse_ai_card_batch,
    _parse_speaking_expressions,
)


def test_lookup_prompt_requests_multiple_common_senses():
    assert "Give 2-3 core high-frequency meanings" in _STREAMLIT_SIMPLE_LOOKUP_PROMPT
    assert "genuinely single-sense" in _STREAMLIT_SIMPLE_LOOKUP_PROMPT
    assert "obscure, rare, technical, or outdated" in _STREAMLIT_SIMPLE_LOOKUP_PROMPT


def test_deepseek_thinking_uses_official_parameters(monkeypatch):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return object()

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(ai, "_active_provider", lambda: "deepseek")
    ai._chat_completion(
        client,
        model="deepseek-v4-flash",
        messages=[],
        thinking=True,
        reasoning_effort="low",
        temperature=0.4,
    )
    assert captured["reasoning_effort"] == "low"
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in captured


def test_deepseek_fast_mode_explicitly_disables_thinking(monkeypatch):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return object()

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    monkeypatch.setattr(ai, "_active_provider", lambda: "deepseek")
    monkeypatch.setattr(config, "DEEPSEEK_DISABLE_THINKING", True)
    ai._chat_completion(
        client,
        model="deepseek-v4-flash",
        messages=[],
        temperature=0.2,
    )
    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}
    assert captured["temperature"] == 0.2
    assert "reasoning_effort" not in captured


def test_streamlit_lookup_format_extracts_complete_sentence_card():
    content = """run /rʌn/
1. 跑，奔跑 | Move quickly on foot
• She runs every morning before work.
她每天上班前跑步。"""
    front, back = _card_fields_from_streamlit_result(content, "run", "word")
    assert front == "She runs every morning before work."
    assert back == "跑，奔跑 | Move quickly on foot\n她每天上班前跑步。"
    assert not back.startswith("run\n")


def test_sentence_lookup_keeps_original_complete_sentence():
    front, back = _card_fields_from_streamlit_result(
        "这句话表示一个已经完成的动作。", "She has finished the book", "sentence"
    )
    assert front == "She has finished the book."
    assert back == "这句话表示一个已经完成的动作。"


def test_batch_card_parser_accepts_only_structurally_complete_rows():
    parsed = _parse_ai_card_batch(
        """```text
gross ||| ||| adj. | very unpleasant | 令人恶心的 ||| The gross smell forced everyone to leave the kitchen. ||| |||
healthy ||| ||| adj. | in good health | 健康的 ||| Daily walks help older adults stay healthy and independent. ||| |||
```""",
        ["gross", "healthy"],
        "reading",
    )
    assert set(parsed) == {"gross", "healthy"}
    assert parsed["gross"]["e"].startswith("The gross smell")
    assert parsed["healthy"]["e"].startswith("Daily walks")


def test_general_card_parser_accepts_every_matched_row():
    """不做内容审核：AI 给出什么就收什么，只按词条身份匹配。"""
    parsed = _parse_ai_card_batch(
        """```text
taxi (verb) ||| ||| （飞机）滑行 ||| ||| |||
run ||| ||| 跑 ||| She runs every morning. ||| |||
```""",
        ["taxi (verb)", "run"],
        "general",
    )
    assert set(parsed) == {"taxi (verb)", "run"}
    assert parsed["taxi (verb)"]["m"] == "（飞机）滑行"


def test_cloze_card_parser_accepts_every_matched_row():
    parsed = _parse_ai_card_batch(
        """```text
taxi (verb) ||| ||| （飞机）滑行 ||| After landing, pilots taxi the aircraft slowly toward the assigned gate. ||| |||
run ||| ||| 跑 ||| She runs. ||| |||
```""",
        ["taxi (verb)", "run"],
        "cloze",
    )
    assert set(parsed) == {"taxi (verb)", "run"}
    assert parsed["taxi (verb)"]["e"].startswith("After landing")


def test_old_streamlit_prompt_builders_and_batch_sizes():
    _system_word, user_word = ai._build_word_front_prompts("taxi (verb)")
    _system_reading, user_reading = ai._build_reading_front_prompts("adamant")
    _system_cloze, user_cloze = ai._build_definition_front_prompts("taxi (verb)")
    assert "1. 通用卡" in user_word and "Fields 2, 4, 5, and 6 are empty" in user_word
    assert "SENSE DECISION" in user_reading and "FORMAT EXAMPLES" in user_reading
    assert "ANSWERABILITY TEST" in user_cloze and "9–18 whitespace-delimited words" in user_cloze
    assert _card_generation_batch_size("cloze") == 5
    assert _card_generation_batch_size("reading") == 10
    assert _card_generation_batch_size("general") == 10
    assert _card_generation_batch_size("speaking") == 10


def test_speaking_prompt_and_parser():
    _system, user = _build_speaking_front_prompts("婉拒朋友的邀约（不想去，又不想扫兴）")
    assert "口语卡" in user
    assert "3 most common" in user
    assert " || " in user
    assert "对……说：" in user
    assert "Field 1 exactly matches its input" in user

    parsed = _parse_ai_card_batch(
        """```text
婉拒朋友的邀约（不想去，又不想扫兴） ||| ||| 1. I'd love to, but I've already got plans. —— 最常用，不伤人 || 2. I'm going to have to pass this time. —— 稍正式 || 3. Not this time, maybe next time. —— 轻松口语 ||| ||| |||
```""",
        ["婉拒朋友的邀约（不想去，又不想扫兴）"],
        "speaking",
    )
    assert set(parsed) == {"婉拒朋友的邀约（不想去，又不想扫兴）"}
    expressions = _parse_speaking_expressions(
        parsed["婉拒朋友的邀约（不想去，又不想扫兴）"]["m"]
    )
    assert len(expressions) == 3
    assert all("——" in expression for expression in expressions)
    assert expressions[0].startswith("I'd love to")


def test_speaking_parser_requires_at_least_two_expressions():
    parsed = _parse_ai_card_batch(
        """```text
某个需求 ||| ||| 1. Only one option here. —— 提示 ||| ||| |||
```""",
        ["某个需求"],
        "speaking",
    )
    assert parsed == {}


def test_speaking_parser_accepts_multiline_expression_rows():
    content = """```text
总结讨论内容 ||| ||| 1. Let me wrap up what we discussed. —— 总结要点
2. To sum up, the main points are these. —— 常用开场
3. So in a nutshell, we agreed on the next steps. —— 口语 ||| ||| |||
礼貌打断并补充一句 ||| ||| 1. Sorry to jump in, but I have a point. —— 礼貌打断
2. Can I add something here? —— 更委婉
3. Just to add to that, I think we should wait. —— 轻松 ||| ||| |||
```"""
    parsed = _parse_ai_card_batch(
        content, ["总结讨论内容", "礼貌打断并补充一句"], "speaking"
    )
    assert set(parsed) == {"总结讨论内容", "礼貌打断并补充一句"}
    assert len(_parse_speaking_expressions(parsed["总结讨论内容"]["m"])) == 3


def test_speaking_parser_tolerates_trailing_punctuation_and_uses_order_fallback():
    content = """```text
总结讨论内容。 ||| ||| 1. Let me wrap up what we discussed. —— 总结要点 || 2. To sum up, here are the main points. —— 常用开场 || 3. So in a nutshell, that is the plan. —— 口语 ||| ||| |||
礼貌打断并补充一句（换个说法） ||| ||| 1. Sorry to jump in, but I have a point. —— 礼貌打断 || 2. Can I add something here? —— 更委婉 || 3. Just to add to that, I would wait. —— 轻松 ||| ||| |||
```"""
    parsed = _parse_ai_card_batch(
        content, ["总结讨论内容", "礼貌打断并补充一句"], "speaking"
    )
    assert set(parsed) == {"总结讨论内容", "礼貌打断并补充一句"}
    assert "Let me wrap up" in parsed["总结讨论内容"]["m"]
    assert "Sorry to jump in" in parsed["礼貌打断并补充一句"]["m"]


def test_speaking_parser_rejects_blank_slots():
    parsed = _parse_ai_card_batch(
        """```text
问路时礼貌开头 ||| ||| 1. Excuse me, how do I get to ___? —— 提示 || 2. Could you point me to Central Station? —— 更正式 || 3. Is the City Library near here? —— 口语 ||| ||| |||
```""",
        ["问路时礼貌开头"],
        "speaking",
    )
    assert parsed == {}


def test_speaking_expression_parser_tolerates_newlines_and_single_pipes():
    expressions = _parse_speaking_expressions(
        "1. First option. —— 提示\n2. Second option. —— 提示 | 3. Third option. —— 提示"
    )
    assert len(expressions) == 3
    assert expressions[0].startswith("First option.")
    assert expressions[2].startswith("Third option.")


def test_batch_card_generation_requeues_only_incomplete_words(monkeypatch):
    calls = []
    responses = [
        """```text
gross ||| ||| adj. | very unpleasant | 令人恶心的 ||| The gross smell forced everyone to leave the kitchen. ||| |||
healthy ||| ||| ||| Daily walks help older adults stay healthy and independent. ||| |||
```""",
        """```text
healthy ||| ||| adj. | in good health | 健康的 ||| Daily walks help older adults stay healthy and independent. ||| |||
```""",
    ]

    class FakeDb:
        def __init__(self):
            self.added = []

        def add(self, value):
            self.added.append(value)

    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())

    def fake_call(_client, words, _card_template):
        calls.append(list(words))
        return responses.pop(0)

    monkeypatch.setattr(ai, "_call_ai_card_batch", fake_call)
    results, errors, request_count, timings = ai.generate_card_content_in_batches(
        FakeDb(), 1, ["gross", "healthy"]
    )

    assert calls == [["gross", "healthy"], ["healthy"]]
    assert set(results) == {"gross", "healthy"}
    assert errors == {}
    assert request_count == 2
    assert timings["format_retry_count"] >= 1
    assert timings["ai_wait_seconds"] >= 0


def test_batch_card_generation_retries_transient_network_error(monkeypatch):
    calls = []

    class FakeDb:
        def add(self, _value):
            pass

    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())
    monkeypatch.setattr(ai.time, "sleep", lambda _seconds: None)

    def fake_call(_client, words, _card_template):
        calls.append(list(words))
        if len(calls) == 1:
            raise TimeoutError("temporary")
        return """```text
healthy ||| ||| adj. | in good health | 健康的 ||| Daily walks help older adults stay healthy and independent. ||| |||
```"""

    monkeypatch.setattr(ai, "_call_ai_card_batch", fake_call)
    results, errors, request_count, timings = ai.generate_card_content_in_batches(
        FakeDb(), 1, ["healthy"]
    )

    assert calls == [["healthy"], ["healthy"]]
    assert set(results) == {"healthy"}
    assert errors == {}
    assert request_count == 2
    assert timings["ai_wait_seconds"] >= 0


def test_card_generation_runs_ten_ten_word_batches_sequentially(monkeypatch):
    words = [f"alpha{index}" for index in range(100)]
    calls = []

    class FakeDb:
        def add(self, _value):
            pass

    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())

    def fake_call(_client, batch, _card_template):
        calls.append(list(batch))
        rows = [
            f"{word} ||| ||| n. | a test target | 测试目标 ||| "
            f"Readers remember {word} through one clear example sentence. ||| |||"
            for word in batch
        ]
        return "```text\n" + "\n".join(rows) + "\n```"

    monkeypatch.setattr(ai, "_call_ai_card_batch", fake_call)
    results, errors, request_count, timings = ai.generate_card_content_in_batches(
        FakeDb(), 1, words
    )

    assert len(calls) == 10
    assert all(len(batch) == 10 for batch in calls)
    assert calls[0] == words[:10]
    assert calls[9] == words[90:]
    assert set(results) == set(words)
    assert errors == {}
    assert request_count == 10
    assert timings["format_retry_count"] == 0


def test_card_generation_rejects_second_concurrent_task(monkeypatch):
    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())
    monkeypatch.setattr(
        ai,
        "_call_ai_card_batch",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not call AI")),
    )

    user_id = 987654
    assert ai._acquire_card_generation_slot(user_id)
    try:
        results, errors, request_count, _timings = (
            ai.generate_card_content_in_batches(None, user_id, ["alpha"])
        )
        assert results == {}
        assert request_count == 0
        assert errors["alpha"]
        assert "已有制卡任务" in errors["alpha"]
    finally:
        ai._release_card_generation_slot(user_id)


def test_card_generation_deadline_stops_without_new_requests(monkeypatch):
    monkeypatch.setattr(ai, "_AI_CARD_GENERATION_DEADLINE_SECONDS", 0.0)
    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())
    monkeypatch.setattr(
        ai,
        "_call_ai_card_batch",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not call AI")),
    )

    results, errors, request_count, _timings = ai.generate_card_content_in_batches(
        None, 1, ["alpha"]
    )
    assert results == {}
    assert request_count == 0
    assert errors["alpha"]
    assert "超时" in errors["alpha"]


def test_ai_quota_blocks_card_generation(monkeypatch):
    from app.db import SessionLocal
    from app.models import AiUsage, User

    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 1)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())
    monkeypatch.setattr(
        ai,
        "_call_ai_card_batch",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not call AI")),
    )

    db = SessionLocal()
    try:
        user = User(email="quota@example.com", password_hash="x", salt="y")
        db.add(user)
        db.commit()
        db.refresh(user)
        db.add(AiUsage(user_id=user.id))
        db.commit()
        results, errors, request_count, _timings = ai.generate_card_content_in_batches(
            db, user.id, ["alpha"]
        )
        assert results == {}
        assert request_count == 0
        assert errors["alpha"]
        assert "上限" in errors["alpha"]
    finally:
        db.close()


def test_ai_quota_reserve_is_atomic_across_calls(monkeypatch):
    from app.db import SessionLocal
    from app.models import User

    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 2)
    db = SessionLocal()
    try:
        user = User(email="quota-atomic@example.com", password_hash="x", salt="y")
        db.add(user)
        db.commit()
        db.refresh(user)
        assert ai.ai_quota_reserve(db, user.id) is None
        assert ai.ai_quota_reserve(db, user.id) is None
        error = ai.ai_quota_reserve(db, user.id)
        assert error
        assert "上限" in error
    finally:
        db.close()


def test_free_ai_query_and_card_quotas_are_separate_and_atomic(monkeypatch):
    from app.db import SessionLocal
    from app.models import AiFreeDailyQuota, User

    monkeypatch.setattr(config, "AI_FREE_DAILY_QUERY_LIMIT", 2)
    monkeypatch.setattr(config, "AI_FREE_DAILY_CARD_LIMIT", 3)
    db = SessionLocal()
    try:
        user = User(email="free-quota@example.com", password_hash="x", salt="y")
        db.add(user)
        db.commit()
        db.refresh(user)

        assert ai.free_ai_quota_reserve(db, user.id, "query") is None
        assert ai.free_ai_quota_reserve(db, user.id, "query") is None
        assert "免费 AI 查询额度" in ai.free_ai_quota_reserve(
            db, user.id, "query"
        )
        assert ai.free_ai_quota_reserve(db, user.id, "card", need=3) is None
        assert "免费制卡额度" in ai.free_ai_quota_reserve(
            db, user.id, "card"
        )

        row = db.query(AiFreeDailyQuota).filter_by(user_id=user.id).one()
        assert row.query_count == 2
        assert row.card_count == 3

        monkeypatch.setattr(ai, "_quota_day", lambda: "2099-01-02")
        assert ai.free_ai_quota_reserve(db, user.id, "query") is None
        assert (
            db.query(AiFreeDailyQuota).filter_by(user_id=user.id).count() == 2
        )
    finally:
        db.close()


def test_own_api_key_bypasses_free_query_quota(monkeypatch):
    from app.db import SessionLocal
    from app.models import AiFreeDailyQuota, User

    monkeypatch.setattr(config, "AI_FREE_DAILY_QUERY_LIMIT", 1)
    monkeypatch.setattr(ai, "_new_ai_client", lambda *_args: object())
    monkeypatch.setattr(
        ai,
        "_chat_completion",
        lambda *_args, **_kwargs: _fake_chat_response(
            "【释义】\n竞技场\n\n【底层逻辑】\n沙地舞台。\n\n"
            "【🌱 Etymology 词源史诗】\n来自拉丁语 harena。"
        ),
    )
    db = SessionLocal()
    try:
        user = User(email="own-key-query@example.com", password_hash="x", salt="y")
        db.add(user)
        db.commit()
        db.refresh(user)
        for _ in range(2):
            result, error = ai.quick_lookup(
                db, user.id, "arena", "sk-own-key-1234567890"
            )
            assert result and error is None
        assert (
            db.query(AiFreeDailyQuota).filter_by(user_id=user.id).first() is None
        )
    finally:
        db.close()


def test_platform_query_stops_at_free_daily_limit(monkeypatch):
    from app.db import SessionLocal
    from app.models import User

    monkeypatch.setattr(config, "AI_FREE_DAILY_QUERY_LIMIT", 1)
    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "ai_enabled", lambda *_args: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda *_args: object())
    monkeypatch.setattr(
        ai,
        "_chat_completion",
        lambda *_args, **_kwargs: _fake_chat_response(
            "【释义】\n竞技场\n\n【底层逻辑】\n沙地舞台。\n\n"
            "【🌱 Etymology 词源史诗】\n来自拉丁语 harena。"
        ),
    )
    db = SessionLocal()
    try:
        user = User(email="platform-query@example.com", password_hash="x", salt="y")
        db.add(user)
        db.commit()
        db.refresh(user)
        result, error = ai.quick_lookup(db, user.id, "arena")
        assert result and error is None
        blocked, error = ai.quick_lookup(db, user.id, "arena")
        assert blocked is None
        assert "免费 AI 查询额度" in error
    finally:
        db.close()


def test_card_generation_reserves_card_count_and_own_key_bypasses_it(monkeypatch):
    from app.db import SessionLocal
    from app.models import AiFreeDailyQuota, User

    monkeypatch.setattr(config, "AI_FREE_DAILY_CARD_LIMIT", 2)
    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "ai_enabled", lambda *_args: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda *_args: object())

    def fake_cards(_client, words, _template):
        rows = [
            f"{word} ||| ||| n. | test meaning | 测试释义 ||| "
            f"Readers remember {word} through this clear example sentence. ||| |||"
            for word in words
        ]
        return "```text\n" + "\n".join(rows) + "\n```"

    monkeypatch.setattr(ai, "_call_ai_card_batch", fake_cards)
    db = SessionLocal()
    try:
        user = User(email="card-free-quota@example.com", password_hash="x", salt="y")
        db.add(user)
        db.commit()
        db.refresh(user)

        results, errors, _, _ = ai.generate_card_content_in_batches(
            db, user.id, ["alpha", "beta"]
        )
        assert len(results) == 2 and not errors
        blocked, errors, request_count, _ = ai.generate_card_content_in_batches(
            db, user.id, ["gamma"]
        )
        assert blocked == {} and request_count == 0
        assert "免费制卡额度" in errors["gamma"]

        own_results, own_errors, _, _ = ai.generate_card_content_in_batches(
            db,
            user.id,
            ["gamma", "delta", "epsilon"],
            user_api_key="sk-own-key-1234567890",
        )
        assert len(own_results) == 3 and not own_errors
        row = db.query(AiFreeDailyQuota).filter_by(user_id=user.id).one()
        assert row.card_count == 2
    finally:
        db.close()


def test_guest_ai_quota_reserve_is_atomic_across_calls(monkeypatch):
    from app.db import SessionLocal

    monkeypatch.setattr(config, "GUEST_AI_DAILY_LIMIT", 2)
    db = SessionLocal()
    try:
        assert ai.guest_ai_quota_reserve(db) is None
        assert ai.guest_ai_quota_reserve(db) is None
        error = ai.guest_ai_quota_reserve(db)
        assert error
        assert "上限" in error
    finally:
        db.close()


def test_guest_ai_quota_refund_restores_allowance(monkeypatch):
    from app.db import SessionLocal
    from app.models import GuestAiQuota

    monkeypatch.setattr(config, "GUEST_AI_DAILY_LIMIT", 2)
    db = SessionLocal()
    try:
        assert ai.guest_ai_quota_reserve(db) is None
        assert ai.guest_ai_quota_reserve(db) is None
        day = ai._quota_day()
        row = db.get(GuestAiQuota, day)
        assert row.count == 2
        ai.guest_ai_quota_refund(db)
        row = db.get(GuestAiQuota, day)
        assert row.count == 1
        assert ai.guest_ai_quota_reserve(db) is None
        row = db.get(GuestAiQuota, day)
        assert row.count == 2
    finally:
        db.close()


def test_guest_ai_quota_refund_never_goes_negative(monkeypatch):
    from app.db import SessionLocal
    from app.models import GuestAiQuota

    monkeypatch.setattr(config, "GUEST_AI_DAILY_LIMIT", 2)
    db = SessionLocal()
    try:
        ai.guest_ai_quota_refund(db)
        day = ai._quota_day()
        assert db.get(GuestAiQuota, day) is None
        assert ai.guest_ai_quota_reserve(db) is None
        ai.guest_ai_quota_refund(db)
        ai.guest_ai_quota_refund(db)
        row = db.get(GuestAiQuota, day)
        assert row.count == 0
    finally:
        db.close()


def test_explain_lookup_guest_ai_failure_refunds_quota(monkeypatch):
    from app.db import SessionLocal
    from app.models import GuestAiQuota

    monkeypatch.setattr(config, "GUEST_AI_DAILY_LIMIT", 5)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())
    monkeypatch.setattr(ai.time, "sleep", lambda _seconds: None)

    def fake_chat(_client, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr(ai, "_chat_completion", fake_chat)
    db = SessionLocal()
    try:
        result, error, charged = ai.explain_lookup(db, None, "quasar", "word")
        assert result is None
        assert error
        # 游客失败已退还配额：charged 必须为 False，供纠错路径决定重新预占。
        assert charged is False
        day = ai._quota_day()
        row = db.get(GuestAiQuota, day)
        assert row is None or row.count == 0
        assert ai.guest_ai_quota_reserve(db) is None
        row = db.get(GuestAiQuota, day)
        assert row.count == 1
    finally:
        db.close()


def test_explain_lookup_reserve_quota_false_skips_guest_quota(monkeypatch):
    from app.db import SessionLocal
    from app.models import GuestAiQuota

    monkeypatch.setattr(config, "GUEST_AI_DAILY_LIMIT", 5)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())

    def fake_chat(_client, **kwargs):
        return _fake_chat_response(
            "environment /ɪnˈvaɪrənmənt/\n1. 环境 | The surroundings\n"
            "• The environment needs our protection.\n环境需要我们的保护。"
        )

    monkeypatch.setattr(ai, "_chat_completion", fake_chat)
    db = SessionLocal()
    try:
        result, error, charged = ai.explain_lookup(
            db, None, "environment", "word", reserve_quota=False
        )
        assert error is None
        assert result["explanation"].startswith("environment")
        # 未预占配额时 charged 为 False。
        assert charged is False
        day = ai._quota_day()
        assert db.get(GuestAiQuota, day) is None
    finally:
        db.close()


def test_quick_lookup_guest_charges_then_refunds_quota(monkeypatch):
    from app.db import SessionLocal
    from app.models import GuestAiQuota

    monkeypatch.setattr(config, "GUEST_AI_DAILY_LIMIT", 5)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())

    def ok_chat(_client, **kwargs):
        return _fake_chat_response(
            "【释义】\n竞技场；活动场所\n\n【底层逻辑】\n沙地上的舞台。\n\n"
            "【🌱 Etymology 词源史诗】\narena 来自拉丁语 harena，意思是沙子。"
        )

    def fail_chat(_client, **kwargs):
        raise ConnectionError("network down")

    db = SessionLocal()
    try:
        monkeypatch.setattr(ai, "_chat_completion", ok_chat)
        result, error = ai.quick_lookup(db, None, "arena")
        assert error is None
        assert result["headword"] == "arena"
        day = ai._quota_day()
        row = db.get(GuestAiQuota, day)
        assert row is not None
        assert row.count == 1

        monkeypatch.setattr(ai, "_chat_completion", fail_chat)
        result, error = ai.quick_lookup(db, None, "arena")
        assert result is None
        assert error
        row = db.get(GuestAiQuota, day)
        assert row is not None
        assert row.count == 1
    finally:
        db.close()


def test_answer_question_guest_charges_then_refunds_quota(monkeypatch):
    from app.db import SessionLocal
    from app.models import GuestAiQuota

    monkeypatch.setattr(config, "GUEST_AI_DAILY_LIMIT", 5)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())

    def ok_chat(_client, **kwargs):
        return _fake_chat_response("lie 表示主动躺下，lay 表示放置或下蛋。")

    def fail_chat(_client, **kwargs):
        raise ConnectionError("network down")

    db = SessionLocal()
    try:
        monkeypatch.setattr(ai, "_chat_completion", ok_chat)
        result, error = ai.answer_question(db, None, "lie 和 lay 的区别？")
        assert error is None
        assert "lie" in result
        day = ai._quota_day()
        row = db.get(GuestAiQuota, day)
        assert row is not None
        assert row.count == 1

        monkeypatch.setattr(ai, "_chat_completion", fail_chat)
        result, error = ai.answer_question(db, None, "lie 和 lay 的区别？")
        assert result is None
        assert error
        row = db.get(GuestAiQuota, day)
        assert row is not None
        assert row.count == 1
    finally:
        db.close()


class _FakeDb:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    def commit(self):
        pass

    def rollback(self):
        pass


def _fake_chat_response(content: str):
    class Message:
        content: str

    Message.content = content

    class Choice:
        message = Message()

    class Response:
        choices = [Choice()]

    return Response()


def test_missing_lookup_input_detection():
    assert ai._looks_like_missing_lookup_input("please provide the word to explain")
    assert ai._looks_like_missing_lookup_input("请输入你想查询的单词")
    assert not ai._looks_like_missing_lookup_input("arena 来自拉丁语 harena，意思是沙子。")


def test_parse_ai_word_block_cleans_lines():
    words = ai._parse_ai_word_block("```text\n1. apple\n• orange\nselected:\npear\n```")
    assert words == ["apple", "orange", "pear"]


def test_quick_lookup_retries_when_model_asks_for_input(monkeypatch):
    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())
    calls = []

    def fake_chat(_client, **kwargs):
        calls.append(kwargs["messages"][-1]["content"])
        if len(calls) == 1:
            content = "Please provide the word you want to know."
        else:
            content = "【释义】\n竞技场；活动场所；公开较量的领域\n\n【底层逻辑】\n沙地上的舞台。\n\n【🌱 Etymology 词源史诗】\narena 来自拉丁语 harena，意思是沙子。"
        return _fake_chat_response(content)

    monkeypatch.setattr(ai, "_chat_completion", fake_chat)
    result, error = ai.quick_lookup(_FakeDb(), 1, "arena")
    assert error is None
    assert result["headword"] == "arena"
    assert "词源史诗" in result["explanation"]
    assert len(calls) == 2


def test_topic_word_list_parses_code_block(monkeypatch):
    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())
    monkeypatch.setattr(
        ai,
        "_chat_completion",
        lambda _client, **kwargs: _fake_chat_response(
            "```text\nmarket\nsupply\ndemand\n```"
        ),
    )
    words, error = ai.generate_topic_word_list(_FakeDb(), 1, "economy", 50)
    assert error is None
    assert words == ["market", "supply", "demand"]


def test_priority_select_maps_ai_output_back_to_input(monkeypatch):
    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())
    monkeypatch.setattr(
        ai,
        "_chat_completion",
        lambda _client, **kwargs: _fake_chat_response(
            "```text\napple\nbanana\n```\n```text\ncherry\n```"
        ),
    )
    result, error = ai.select_priority_words(
        _FakeDb(), 1, ["apple", "cherry", "banana"], 2
    )
    assert error is None
    assert set(result["selected"]) == {"apple", "banana"}
    assert result["remaining"] == ["cherry"]
