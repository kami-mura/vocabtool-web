import os
import tempfile

# 必须在导入 app 之前设置环境变量
_tmp = tempfile.mkdtemp(prefix="vocabflow_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["EMAIL_VERIFICATION_REQUIRED"] = "false"
os.environ["VOCABFLOW_SKIP_DOTENV"] = "true"
os.environ["CSRF_REQUIRE_ORIGIN"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app

init_db()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_database_between_tests():
    """每个测试结束后清空全部业务表，避免跨测试状态串扰。

    整个测试会话共享同一个 SQLite 文件库，而 TestClient 的所有请求
    共享同一个匿名身份（testclient），限流计数、会话、验证码等短命
    数据若不清理会泄漏到下一个测试。清表按外键依赖倒序，避免违反
    PRAGMA foreign_keys=ON 的约束；schema_migrations 等非 ORM 表
    不在 Base.metadata 中，不会被清掉。
    """
    yield
    from app.db import Base, SessionLocal

    db = SessionLocal()
    try:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _fake_ai_card_generation_for_api_tests(monkeypatch, request):
    """API 集成测试不连真实 DeepSeek；制卡统一走确定性假结果。"""
    if "/test_api" not in request.node.nodeid:
        return
    from app import ai as ai_mod

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
            elif card_template == "speaking":
                results[word] = {
                    "w": word,
                    "m": (
                        "1. I'd love to, but I've got other plans. —— 最常用，不伤人"
                        " || 2. I'm going to have to pass this time. —— 稍正式"
                        " || 3. Not this time, maybe next time. —— 轻松口语"
                    ),
                    "e": "",
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

    monkeypatch.setattr(
        ai_mod, "generate_card_content_in_batches", fake_generate
    )


def register(client, email="alice@example.com", password="password123"):
    res = client.post(
        "/api/register",
        json={"email": email, "password": password},
    )
    assert res.status_code == 200, res.text
    return res
