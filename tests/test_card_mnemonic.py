import pytest
from app import ai
from app.models import Card, User
from app.db import SessionLocal
from tests.conftest import register


def test_parse_ai_card_rows_with_mnemonic():
    raw_text = """```text
adamant ||| ||| adj. | refusing to change an opinion or decision | 坚定不改的 ||| She remained adamant despite pressure from the entire board. ||| ||| 💎 钻石坚硬 ➔ 🔨 铁锤猛砸 ➔ 🛡️ 纹丝不动。像金刚石一样坚决。
taxi (verb) ||| ||| （飞机）滑行 ||| ||| ||| ✈️ 飞机降落 ➔ 🛞 轮子触地 ➔ 🛣️ 缓缓滑行。飞机像出租车一样在地面慢行。
```"""
    parsed = ai._parse_ai_card_rows(raw_text)
    assert len(parsed) == 2
    assert parsed[0]["w"] == "adamant"
    assert "💎" in parsed[0]["r"]
    assert "像金刚石一样坚决" in parsed[0]["r"]

    assert parsed[1]["w"] == "taxi (verb)"
    assert "✈️" in parsed[1]["r"]
    assert "飞机像出租车一样在地面慢行" in parsed[1]["r"]


def test_generate_word_mnemonic_unit(monkeypatch):
    class FakeChoice:
        message = type("Msg", (), {"content": "⏰ 闹钟响 ➔ 🛌 继续睡 ➔ 😱 迟到。拖延一时爽。"})()

    class FakeResponse:
        choices = [FakeChoice()]

    monkeypatch.setattr(ai, "_chat_completion", lambda *args, **kwargs: FakeResponse())

    res = ai.generate_word_mnemonic("procrastinate", "拖延", "He procrastinates constantly.")
    assert "⏰" in res
    assert "拖延一时爽" in res


def test_card_mnemonic_api_endpoint(client, monkeypatch):
    register(client, "mnemonic-test@example.com")

    # Mock ai.generate_word_mnemonic
    monkeypatch.setattr(
        ai,
        "generate_word_mnemonic",
        lambda word, meaning="", context="", user_api_key=None: "🦆 鸭子戏水 ➔ 🌿 荷叶摇曳 ➔ 💧 泛起波澜。池塘清澈。",
    )

    # 1. 创建一张已有助记的卡片
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "mnemonic-test@example.com").one()
        card_with_m = Card(
            user_id=user.id,
            word="pond",
            card_type="reading",
            front="The duck swam in the **pond**.",
            back="n. | a small body of still water | 池塘\n\n💡 助记：🦆 鸭子戏水 ➔ 🌿 荷叶摇曳。池塘微波。",
            state="new",
        )
        card_without_m = Card(
            user_id=user.id,
            word="lake",
            card_type="reading",
            front="The boat crossed the **lake**.",
            back="n. | a large body of water | 湖泊",
            state="new",
        )
        db.add_all([card_with_m, card_without_m])
        db.commit()
        id_with_m = card_with_m.id
        id_without_m = card_without_m.id
    finally:
        db.close()

    # 2. 测试获取已有助记
    res1 = client.post(f"/api/cards/{id_with_m}/mnemonic")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["ok"] is True
    assert "🦆 鸭子戏水" in data1["mnemonic"]

    # 3. 测试为没有助记的旧卡动态生成并写入
    res2 = client.post(f"/api/cards/{id_without_m}/mnemonic")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["ok"] is True
    assert "🦆 鸭子戏水" in data2["mnemonic"]

    # 验证数据库中已经持久化更新了 back
    db = SessionLocal()
    try:
        updated_card = db.query(Card).filter(Card.id == id_without_m).one()
        assert "💡 助记：" in updated_card.back
        assert "🦆 鸭子戏水" in updated_card.back
    finally:
        db.close()
