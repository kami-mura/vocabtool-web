import datetime as dt
import json

import pytest

from app import ai, api_support, config
from app.ai import generate_card_content_in_batches as generate_cards
from app.db import SessionLocal
from app.models import Card, User
from app.routes import card_routes
from tests.conftest import register
from tests.test_card_examples import EXAMPLES, card_content


def create_example_card(client, monkeypatch, template="reading"):
    register(client, "examples@example.com")
    monkeypatch.setattr(config, "AI_DAILY_REQUEST_LIMIT", 0)
    monkeypatch.setattr(ai, "generate_card_content_in_batches", generate_cards)
    monkeypatch.setattr(ai, "ai_enabled", lambda: True)
    monkeypatch.setattr(ai, "_new_ai_client", lambda: object())
    monkeypatch.setattr(ai, "_call_ai_card_batch", lambda *a, **kw: card_content(template))
    response = client.post("/api/card-studio/cards", json={"words": ["run"], "card_type": template})
    assert response.status_code == 200, response.text
    assert response.json()["created"] == 1
    card = client.get("/api/cards").json()["new"][0]
    with SessionLocal() as db:
        stored = db.get(Card, card["id"])
        assert json.loads(stored.examples_json) == EXAMPLES
        assert stored.example_day == ""
    return card


def rate(client, card, action, batch=False, day=None):
    payload = {
        "rating": "again", "action_id": action,
        "expected_revision": card["revision"],
        "expected_example_day": day or card["example_learning_day"],
    }
    if batch:
        return client.post("/api/cards/reviews/batch", json={"ratings": [{"card_id": card["id"], **payload}]})
    return client.post(f"/api/cards/{card['id']}/review", json=payload)


@pytest.mark.parametrize("template", ["reading", "cloze"])
def test_three_examples_persist_and_rotate_with_single_batch_retry_and_undo(client, monkeypatch, template):
    card = create_example_card(client, monkeypatch, template)
    real_learning_day = api_support._learning_day
    start_day = dt.date.fromisoformat(card["example_learning_day"])
    clock = {"day": start_day.isoformat()}

    def learning_day(now=None):
        _, start, end = real_learning_day(now)
        return clock["day"], start, end

    monkeypatch.setattr(api_support, "_learning_day", learning_day)
    monkeypatch.setattr(card_routes, "_learning_day", learning_day)
    for index, gap in enumerate([0, 4, 9, 10]):
        clock["day"] = (start_day + dt.timedelta(days=gap)).isoformat()
        # 漏学的日期不消耗例句；浏览不改变轮换状态。
        queued = client.get("/api/cards").json()["queue"][0]
        assert queued["context"] == EXAMPLES[index % 3]
        assert queued["example_number"] == index % 3 + 1
        assert client.get("/api/cards").json()["queue"][0]["context"] == queued["context"]
        assert client.get("/api/cards/browse").json()["cards"][0]["context"] == queued["context"]
        batch = index % 2 == 1
        response = rate(client, queued, f"day-{index}", batch=batch)
        assert response.status_code == 200, response.text
        result = response.json()
        if batch:
            assert not result["errors"]
            result = result["cards"][0]
        card = result["card"]
        assert card["context"] == queued["context"]
        retry = rate(client, queued, f"day-{index}", batch=batch).json()
        retry = retry["cards"][0] if batch else retry
        assert retry["idempotent"]
        assert retry["card"]["context"] == card["context"]
        undone = client.post("/api/cards/reviews/undo")
        assert undone.status_code == 200, undone.text
        restored = undone.json()["card"]
        assert restored["context"] == card["context"]
        repeated = rate(client, restored, f"day-{index}-again").json()["card"]
        assert repeated["context"] == card["context"]
        with SessionLocal() as db:
            stored = db.get(Card, card["id"])
            assert stored.example_day == clock["day"]
            assert stored.example_index == index % 3
            assert stored.context == EXAMPLES[0]


@pytest.mark.parametrize("batch", [False, True])
def test_rejected_stale_and_cross_day_reviews_do_not_advance_examples(client, monkeypatch, batch):
    card = create_example_card(client, monkeypatch)
    for action, stale in [
        ("wrong-revision", {**card, "revision": 999}),
        ("old-day", {**card, "example_learning_day": "2000-01-01"}),
    ]:
        response = rate(client, stale, action, batch=batch)
        if batch:
            assert response.status_code == 200
            assert response.json()["errors"][0]["status"] == 409
        else:
            assert response.status_code == 409
        with SessionLocal() as db:
            stored = db.get(Card, card["id"])
            assert stored.example_index == 0
            assert stored.example_day == ""
            assert stored.revision == 0
    assert rate(client, card, "valid-after-fail", batch=batch).status_code == 200


def test_example_rotation_is_private_to_card_owner(client, monkeypatch):
    card = create_example_card(client, monkeypatch)
    client.post("/api/logout")
    register(client, "other-examples@example.com")
    assert client.get("/api/cards").json()["queue"] == []
    assert rate(client, card, "other-user").status_code == 404
    with SessionLocal() as db:
        stored = db.get(Card, card["id"])
        assert stored.user_id != db.query(User).filter_by(email="other-examples@example.com").one().id
        assert stored.example_day == ""
