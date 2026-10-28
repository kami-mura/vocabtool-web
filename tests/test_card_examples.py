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
