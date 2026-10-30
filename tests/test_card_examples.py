import datetime as dt
import json

import pytest

from app import ai, card_builder, config
from app.api_support import _card_dict
from app.models import Card

EXAMPLES = [
    "She runs by the river before breakfast.",
    "He runs to catch the bus after work.",
    "Our dog runs across the garden every evening.",
]


def card_content(template="reading", examples=None):
    return json.dumps({"items": [{
        "id": "I01", "pos": "v.", "en_def": "move quickly on foot",
        "zh_def": "跑步", "meaning": "跑步",
        "examples": EXAMPLES if examples is None else examples,
    }]})


@pytest.mark.parametrize("template", ["reading", "cloze"])
def test_parser_preserves_three_distinct_examples(template):
    parsed = ai._parse_ai_card_batch(card_content(template), ["run"], template)
    assert parsed["run"]["examples"] == EXAMPLES
    assert parsed["run"]["e"] == EXAMPLES[0]


@pytest.mark.parametrize("examples", [
    [], EXAMPLES[:2], EXAMPLES + ["She runs home after school."],
    [EXAMPLES[0], EXAMPLES[0], EXAMPLES[2]],
    [EXAMPLES[0], "  SHE runs by the river before breakfast.  ", EXAMPLES[2]],
    [EXAMPLES[0], 123, EXAMPLES[2]],
    [EXAMPLES[0], "She walks to school.", EXAMPLES[2]],
    "She runs to school.",
])
@pytest.mark.parametrize("template", ["reading", "cloze"])
def test_parser_rejects_invalid_example_sets(template, examples):
    assert ai._parse_ai_card_batch(card_content(template, examples), ["run"], template) == {}


def test_cloze_rejects_unrenderable_later_example():
    assert ai._parse_ai_card_batch(
        card_content("cloze", [EXAMPLES[0], "She runs.", EXAMPLES[2]]), ["run"], "cloze"
    ) == {}


def test_generation_retries_single_sentence_until_three_are_available(monkeypatch):
    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())
    replies = [json.dumps({"items": [{
        "id": "I01", "pos": "v.", "en_def": "move quickly on foot",
        "zh_def": "跑步", "example": EXAMPLES[0],
    }]}), card_content()]
    calls = []

    def fake_call(*args, **kwargs):
        calls.append(1)
        return replies.pop(0)

    monkeypatch.setattr(ai, "_call_ai_card_batch", fake_call)
    results, errors, _, _ = ai.generate_card_content_in_batches(None, 1, ["run"])
    assert len(calls) == 2
    assert results["run"]["examples"] == EXAMPLES
    assert errors == {}


def make_card(template):
    return Card(
        id=1, user_id=1, word="run", card_type=template,
        front=card_builder.sentence_front(EXAMPLES[0], "run", cloze=template == "cloze"),
        back=(card_builder.sentence_front(EXAMPLES[0], "run") + "\n\n跑步"
              if template == "cloze" else "v. | move quickly on foot | 跑步"),
        context=EXAMPLES[0], examples_json=json.dumps(EXAMPLES),
        example_day="", example_index=0, state="new", due_at=None,
    )


@pytest.mark.parametrize("template", ["reading", "cloze"])
def test_rotation_advances_only_on_studied_days_and_wraps(template):
    card = make_card(template)
    # 本地 10 月 1 日；制卡后直到首次学习都显示第一句。
    now = dt.datetime(2026, 10, 1, 3)
    assert _card_dict(card, now=now)["context"] == EXAMPLES[0]
    assert _card_dict(card, now=now + dt.timedelta(days=10))["context"] == EXAMPLES[0]
    for index, day in enumerate(["2026-10-01", "2026-10-06", "2026-10-09", "2026-10-20"]):
        now = dt.datetime.fromisoformat(day + "T03:00:00")
        before = _card_dict(card, now=now)
        assert before["context"] == EXAMPLES[index % 3]
        # 仅序列化、浏览和刷新不算学习，不改变持久化轮换状态。
        assert _card_dict(card, now=now)["context"] == before["context"]
        card_builder.mark_example_studied(card, day)
        card_builder.mark_example_studied(card, day)
        after = _card_dict(card, now=now + dt.timedelta(hours=6))
        assert after["context"] == before["context"]
        assert card.example_index == index % 3
        assert after["example_number"] == index % 3 + 1
        assert after["example_count"] == 3
        if template == "cloze":
            assert "______" in after["front"]
            assert after["context"] == after["back"].split("\n\n")[0].replace("**", "")
            assert after["back"].endswith("\n\n跑步")
        else:
            assert after["front"].replace("**", "") == after["context"]
            assert after["back"] == card.back
    assert card.context == EXAMPLES[0]


def test_rotation_uses_site_midnight(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "APP_TIMEZONE", "Asia/Shanghai")
    card = make_card("reading")
    card_builder.mark_example_studied(card, "2026-10-01")
    assert _card_dict(card, now=dt.datetime(2026, 10, 1, 15, 59))["context"] == EXAMPLES[0]
    after = _card_dict(card, now=dt.datetime(2026, 10, 1, 16))
    assert after["context"] == EXAMPLES[1]
    assert after["example_valid_until"] == "2026-10-02T16:00:00+00:00"


@pytest.mark.parametrize("raw", [None, "", "[]", "null", "{}", "broken", '[123]'])
def test_legacy_or_invalid_examples_preserve_original_card(raw):
    card = make_card("reading")
    card.examples_json = raw
    result = _card_dict(card)
    assert (result["front"], result["back"], result["context"]) == (card.front, card.back, card.context)
    assert result["example_count"] == 1


@pytest.mark.parametrize("template", ["reading", "cloze"])
@pytest.mark.parametrize("pos", ["n. / v.", "adj.; adv.", ["n.", "v."], "", None, "verb or noun"])
def test_generation_rejects_missing_or_multiple_parts_of_speech(template, pos):
    payload = json.loads(card_content(template))
    payload["items"][0]["pos"] = pos
    assert ai._parse_ai_card_batch(json.dumps(payload), ["run"], template) == {}


@pytest.mark.parametrize("template, field", [
    ("reading", "en_def"), ("reading", "zh_def"), ("cloze", "meaning"),
])
def test_generation_rejects_lists_of_definitions(template, field):
    payload = json.loads(card_content(template))
    payload["items"][0][field] = ["move quickly on foot 跑步", "operate a business 经营"]
    assert ai._parse_ai_card_batch(json.dumps(payload), ["run"], template) == {}


@pytest.mark.parametrize("template", ["reading", "cloze"])
def test_generation_retries_multiple_pos_then_accepts_single_pos(monkeypatch, template):
    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())
    invalid = json.loads(card_content(template))
    invalid["items"][0]["pos"] = "n. / v."
    responses = [json.dumps(invalid), card_content(template)]
    repairs = []

    def fake_call(*args, **kwargs):
        repairs.append(kwargs.get("repair_instruction", ""))
        return responses.pop(0)

    monkeypatch.setattr(ai, "_call_ai_card_batch", fake_call)
    results, errors, _, _ = ai.generate_card_content_in_batches(None, 1, ["run"], template)
    assert len(repairs) == 2
    assert "exactly one part of speech" in repairs[1]
    assert results["run"]["pos"] == "v."
    assert results["run"]["examples"] == EXAMPLES
    assert errors == {}


@pytest.mark.parametrize("template", ["general", "reading", "cloze"])
def test_initial_and_repair_prompts_keep_one_core_sense_and_pos(template):
    builder = {
        "general": ai._build_word_front_prompts,
        "reading": ai._build_reading_front_prompts,
        "cloze": ai._build_definition_front_prompts,
    }[template]
    _, prompt = builder(ai._card_prompt_items(["run (operate a business)"], template))
    for text in [prompt, ai._card_repair_instruction(template, ["run"])]:
        assert "single most common core meaning" in text
        assert "exactly one part of speech" in text
        assert "Do not let a sense hint override" in text
        assert "vary the situation, never the meaning or grammatical role" in text
    assert "hint selects the sense" not in prompt
    assert "Use the hinted sense" not in prompt
    if template != "general":
        assert '"pos"' in prompt


@pytest.mark.parametrize("target, sentence, surface", [
    ("wrote", "She has written a letter to her friend.", "written"),
    ("deteriorated", "The bridge will deteriorate without regular repairs.", "deteriorate"),
])
def test_matching_and_rendering_use_same_inflection_family(target, sentence, surface):
    assert card_builder.is_complete_sentence(sentence, target)
    assert f"**{surface}**" in card_builder.sentence_front(sentence, target)
    assert card_builder.sentence_front(sentence, target, cloze=True).count("______") == 1


def test_format_repairs_isolate_failed_words(monkeypatch):
    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())
    calls = []
    batches = []

    def fake_call(_client, words, _template, repair_instruction=""):
        calls.append(repair_instruction)
        batches.append(words)
        if len(calls) == 1:
            return '{"items":[]}'
        return json.dumps({"items": [
            {"id": f"I{index:02d}", "pos": "n.", "en_def": "a test target", "zh_def": "测试目标", "examples": [
                f"Readers discuss {word} during class.",
                f"She writes {word} on the board.",
                f"They found {word} in a book.",
            ]}
            for index, word in enumerate(words, start=1)
        ]})

    monkeypatch.setattr(ai, "_call_ai_card_batch", fake_call)
    results, errors, _, _ = ai.generate_card_content_in_batches(None, 1, ["alpha", "beta"])
    assert set(results) == {"alpha", "beta"}
    assert errors == {}
    assert batches == [["alpha", "beta"], ["alpha"], ["beta"]]
    assert "ids I01" in calls[1]
    assert calls[1].count("The previous response") == 1
    assert "I02" not in calls[1]
    assert "I02" not in calls[2]


def test_repair_rebinds_reasons_to_current_batch_ids():
    instruction = ai._card_repair_instruction("reading", ["fresh", "beta", "alpha"], {
        "beta": "必须返回 3 个例句", "alpha": "缺少中文释义",
    })
    details = json.loads(instruction.split("\nVALIDATION_FAILURES\n")[1].split("\n")[0])
    assert details == [
        {"id": "I02", "reason": "必须返回 3 个例句"},
        {"id": "I03", "reason": "缺少中文释义"},
    ]


@pytest.mark.parametrize("field, value, reason", [
    ("examples", EXAMPLES[:2], "必须返回 3 个例句"),
    ("examples", [EXAMPLES[0]] * 3, "例句不能重复"),
    ("examples", [EXAMPLES[0], "He walks to school every day.", EXAMPLES[2]], "例句 2：未包含目标词"),
    ("pos", "n. / v.", "单一词性标签"),
    ("en_def", "to run very fast", "英文释义包含目标词"),
    ("en_def", ["move quickly"], "释义字段缺失"),
])
def test_rejected_cards_report_the_actual_constraint(field, value, reason):
    payload = json.loads(card_content())
    payload["items"][0][field] = value
    errors = {}
    assert ai._parse_ai_card_batch(json.dumps(payload), ["run"], "reading", errors) == {}
    assert reason in errors["run"]


def test_exhausted_repairs_show_specific_reason(monkeypatch):
    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())
    repairs = []

    def fake_call(*args, **kwargs):
        repairs.append(kwargs.get("repair_instruction", ""))
        return card_content(examples=EXAMPLES[:2])

    monkeypatch.setattr(ai, "_call_ai_card_batch", fake_call)
    results, errors, requests, _ = ai.generate_card_content_in_batches(None, 1, ["run"])
    assert results == {}
    assert requests == 2
    assert "必须返回 3 个例句" in repairs[1]
    assert "必须返回 3 个例句" in errors["run"]
    assert "不完整卡片" not in errors["run"]
