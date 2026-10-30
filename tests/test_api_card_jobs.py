import pytest
from fastapi import BackgroundTasks, HTTPException

from app import ai
from app.routes import card_routes
from tests.conftest import register


@pytest.fixture
def queued_tasks(monkeypatch):
    tasks = []
    monkeypatch.setattr(card_routes, "_CARD_STUDIO_ACTIVE", set(), raising=False)
    monkeypatch.setattr(ai, "_GENERATION_PROGRESS", {})
    monkeypatch.setattr(card_routes, "_sentence_refresh_due", lambda *_args: False)
    monkeypatch.setattr(BackgroundTasks, "add_task", lambda _self, fn, *args, **kw: tasks.append((fn, args, kw)))
    return tasks


def run_task(task):
    fn, args, kwargs = task
    fn(*args, **kwargs)


def test_thousand_card_job_returns_before_generation_and_results_remain_available(client, queued_tasks):
    register(client, "card-job@example.com")
    response = client.post("/api/card-studio/cards?background=true", json={
        "words": [f"jobword{i}" for i in range(1000)], "card_type": "reading",
    })
    assert response.status_code == 202, response.text
    assert response.json()["state"] == "generating"
    assert client.get("/api/cards/browse").json()["total"] == 0
    assert len(queued_tasks) == 1
    progress = client.get("/api/card-studio/progress").json()["progress"]
    assert progress["total"] == 1000
    assert not progress.get("done")
    # 浏览器提交连接已结束；独立会话仍完成制卡，进度接口返回最终结果。
    run_task(queued_tasks[0])
    result = client.get("/api/card-studio/progress").json()["progress"]
    assert result["done"]
    assert result["result"]["created"] == 1000
    assert result["result"]["processed"] == 1000
    assert client.get("/api/cards/browse").json()["total"] == 1000


def test_duplicate_job_does_not_overwrite_running_progress(client, queued_tasks):
    register(client, "duplicate-card-job@example.com")
    payload = {"words": ["run"], "card_type": "reading"}
    assert client.post("/api/card-studio/cards?background=true", json=payload).status_code == 202
    before = client.get("/api/card-studio/progress").json()
    for url in ["/api/card-studio/cards?background=true", "/api/card-studio/cards"]:
        duplicate = client.post(url, json=payload)
        assert duplicate.status_code == 409
        assert duplicate.json()["state"] == "generating"
        assert client.get("/api/card-studio/progress").json() == before
    assert len(queued_tasks) == 1
    run_task(queued_tasks[0])
    assert client.get("/api/card-studio/progress").json()["progress"]["result"]["created"] == 1


@pytest.mark.parametrize("failure", [HTTPException(429, "今日免费制卡额度已用完"), RuntimeError("private details")])
def test_job_error_is_visible_and_slot_released(client, monkeypatch, queued_tasks, failure):
    register(client, "failed-card-job@example.com")
    payload = {"words": ["run"], "card_type": "reading"}

    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(card_routes, "_create_cards_from_studio", fail, raising=False)
    assert client.post("/api/card-studio/cards?background=true", json=payload).status_code == 202
    run_task(queued_tasks[0])
    progress = client.get("/api/card-studio/progress").json()["progress"]
    assert progress["done"]
    assert progress["result"]["error"]
    assert "private details" not in progress["result"]["error"]
    assert client.post("/api/card-studio/cards?background=true", json=payload).status_code == 202


def test_card_jobs_require_authentication_and_progress_is_user_scoped(client, queued_tasks):
    payload = {"words": ["run"], "card_type": "reading"}
    assert client.post("/api/card-studio/cards?background=true", json=payload).status_code == 401
    register(client, "first-job-owner@example.com")
    assert client.post("/api/card-studio/cards?background=true", json=payload).status_code == 202
    client.post("/api/logout")
    register(client, "second-job-owner@example.com")
    assert client.get("/api/card-studio/progress").json()["progress"] is None
    assert client.post("/api/card-studio/cards?background=true", json=payload).status_code == 202
    assert len(queued_tasks) == 2
    for task in queued_tasks:
        run_task(task)
    assert client.get("/api/cards/browse").json()["total"] == 1


def test_new_job_resets_previous_completed_progress(queued_tasks):
    ai._report_card_progress(1, total=1000, completed=1000)
    ai.mark_card_generation_done(1, {"created": 1000})
    ai._report_card_progress(1, total=500, completed=0, detail="排队中")
    progress = ai.card_generation_progress(1)
    assert progress["total"] == 500
    assert progress["completed"] == 0
    assert not progress.get("done")
    assert "result" not in progress
