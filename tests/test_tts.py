import asyncio
import os
import time

from app import tts
from tests.test_api_auth import register


def test_normalize_strips_cloze_and_html():
    assert tts._normalize_tts_text("She is ______ today.") == "She is today."
    assert tts._normalize_tts_text("<b>hello</b> world") == "hello world"
    assert tts._normalize_tts_text("  a   b  ") == "a b"
    assert tts._normalize_tts_text("The **apple** is red.") == "The apple is red."
    assert tts._normalize_tts_text("She ate {{c1::the apple}}.") == "She ate the apple."
    assert len(tts._normalize_tts_text("x" * 500)) == 500


def test_long_texts_sharing_prefix_share_one_cache_file(monkeypatch, tmp_path):
    """前 240 字符相同的长文本生成同一段音频：缓存键与实际生成输入一致，
    不会重复调用 edge-tts 或多占缓存文件。"""
    generated = []

    def fake_generate(text, voice, path):
        generated.append((text, path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 200)
        return True

    monkeypatch.setattr(tts, "TTS_DIR", tmp_path)
    monkeypatch.setattr(tts, "_generate_audio_blocking", fake_generate)
    prefix = "word " * 100
    url_a = asyncio.run(tts.audio_url_for_text(prefix + "alpha"))
    url_b = asyncio.run(tts.audio_url_for_text(prefix + "beta"))
    assert url_a and url_b
    assert url_a == url_b
    assert len(generated) == 1
    assert all(len(text) <= tts.TTS_TEXT_MAX_CHARS for text, _path in generated)


def test_pick_voice(monkeypatch):
    monkeypatch.setattr(tts.config, "TTS_PROVIDER", "edge")
    assert tts._pick_voice("hello world") == tts._TTS_VOICE_EN
    assert tts._pick_voice("你好") == tts._TTS_VOICE_ZH

    monkeypatch.setattr(tts.config, "TTS_PROVIDER", "mimo")
    assert tts._pick_voice("hello world") == tts.config.MIMO_TTS_VOICE_EN
    assert tts._pick_voice("你好") == tts.config.MIMO_TTS_VOICE_ZH


def test_audio_path_stable():
    path1, voice1 = tts._audio_path("apple")
    path2, voice2 = tts._audio_path("apple")
    assert path1 == path2
    assert voice1 == voice2
    assert path1.suffix == ".mp3"
    assert tts.is_audio_filename(path1.name)
    assert not tts.is_audio_filename("x.mp3")
    assert not tts.is_audio_filename("abc.mp3")


def test_audio_url_generates_and_caches(monkeypatch, tmp_path):
    def fake_generate(text, voice, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 200)
        return True

    monkeypatch.setattr(tts, "TTS_DIR", tmp_path)
    monkeypatch.setattr(tts, "_generate_audio_blocking", fake_generate)
    url1 = asyncio.run(tts.audio_url_for_text("apple"))
    assert url1 and url1.startswith("/tts-audio/")
    name = url1.rsplit("/", 1)[1]
    assert (tmp_path / name).is_file()

    calls = []

    def fake_generate2(text, voice, path):
        calls.append(text)
        return True

    monkeypatch.setattr(tts, "_generate_audio_blocking", fake_generate2)
    url2 = asyncio.run(tts.audio_url_for_text("apple"))
    assert url2 == url1
    assert not calls


def test_audio_url_skips_empty_text(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "TTS_DIR", tmp_path)
    assert asyncio.run(tts.audio_url_for_text("   ")) is None
    assert asyncio.run(tts.audio_url_for_text("______")) is None


def test_tts_endpoint_generates_url_and_serves_file(client, monkeypatch, tmp_path):
    def fake_generate(text, voice, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 200)
        return True

    monkeypatch.setattr(tts, "TTS_DIR", tmp_path)
    monkeypatch.setattr(tts, "_generate_audio_blocking", fake_generate)
    register(client, "tts-user@example.com")

    res = client.post("/api/tts", json={"text": "apple"})
    assert res.status_code == 200
    url = res.json()["url"]
    assert url.startswith("/tts-audio/")

    audio = client.get(url)
    assert audio.status_code == 200
    assert audio.headers["cache-control"].startswith("private")

    assert client.get("/tts-audio/not-a-file.mp3").status_code == 404


def test_tts_prefetch_schedules_background_generation(client, monkeypatch, tmp_path):
    def fake_generate(text, voice, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 200)
        return True

    monkeypatch.setattr(tts, "TTS_DIR", tmp_path)
    monkeypatch.setattr(tts, "_generate_audio_blocking", fake_generate)
    register(client, "prefetch@example.com")

    res = client.post("/api/tts/prefetch", json={"texts": ["apple", "banana", "apple"]})
    assert res.status_code == 200
    assert res.json()["scheduled"] == 2  # 重复文本已去重

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if len(list(tmp_path.glob("*.mp3"))) >= 2:
            break
        time.sleep(0.05)
    assert len(list(tmp_path.glob("*.mp3"))) == 2


def test_tts_guest_generates_and_serves_audio(client, monkeypatch, tmp_path):
    """游客（查词结果发音）可生成并播放音频；预生成仍仅限登录用户。"""
    def fake_generate(text, voice, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 200)
        return True

    monkeypatch.setattr(tts, "TTS_DIR", tmp_path)
    monkeypatch.setattr(tts, "_generate_audio_blocking", fake_generate)

    res = client.post("/api/tts", json={"text": "apple"})
    assert res.status_code == 200
    url = res.json()["url"]
    assert url.startswith("/tts-audio/")
    assert client.get(url).status_code == 200

    assert (
        client.post("/api/tts/prefetch", json={"texts": ["apple"]}).status_code
        == 401
    )
    assert client.get("/tts-audio/aaaaaaaaaaaaaaaaaaaaaaaa.mp3").status_code == 404


def test_tts_cache_prunes_oldest_when_over_quota(monkeypatch, tmp_path):
    def fake_generate(text, voice, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 200)
        return True

    monkeypatch.setattr(tts, "TTS_DIR", tmp_path)
    monkeypatch.setattr(tts.config, "TTS_CACHE_MAX_BYTES", 1000)
    # time.monotonic() 是系统启动以来的秒数：CI runner 刚启动时很小，
    # 不能直接设 0.0（会触发节流导致清理被跳过）。用「当前-301」保证必然执行。
    monkeypatch.setattr(tts, "_last_prune_monotonic", time.monotonic() - 301)
    monkeypatch.setattr(tts, "_generate_audio_blocking", fake_generate)

    oldest = tmp_path / ("a" * 24 + ".mp3")
    newer = tmp_path / ("b" * 24 + ".mp3")
    oldest.write_bytes(b"y" * 600)
    newer.write_bytes(b"y" * 600)
    os.utime(oldest, (1_000_000, 1_000_000))
    os.utime(newer, (1_100_000, 1_100_000))

    assert asyncio.run(tts.audio_url_for_text("apple")) is not None
    assert not oldest.exists()  # 超限时最旧的先被清理
    assert newer.exists()


def test_prune_removes_stale_part_files_and_keeps_fresh_ones(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "TTS_DIR", tmp_path)
    monkeypatch.setattr(tts, "_last_prune_monotonic", time.monotonic() - 301)

    stale_part = tmp_path / ("a" * 24 + ".mp3.123.part")
    fresh_part = tmp_path / ("b" * 24 + ".mp3.456.part")
    kept_mp3 = tmp_path / ("c" * 24 + ".mp3")
    stale_part.write_bytes(b"z" * 50)
    fresh_part.write_bytes(b"z" * 50)
    kept_mp3.write_bytes(b"y" * 200)
    os.utime(stale_part, (time.time() - 90_000, time.time() - 90_000))
    os.utime(fresh_part, (time.time() - 100, time.time() - 100))

    tts._prune_tts_cache_if_due()
    assert not stale_part.exists()
    assert fresh_part.exists()
    assert kept_mp3.exists()


def test_prefetch_dropped_when_max_tasks_active(monkeypatch):
    monkeypatch.setattr(tts, "_active_prefetch_tasks", tts._TTS_MAX_PREFETCH_TASKS)
    assert tts.schedule_prefetch(["apple"]) == 0


def test_mimo_tts_generation_and_fallback(monkeypatch, tmp_path):
    import base64

    monkeypatch.setattr(tts, "TTS_DIR", tmp_path)
    monkeypatch.setattr(tts.config, "TTS_PROVIDER", "mimo")
    monkeypatch.setattr(tts.config, "MIMO_API_KEY", "sk-test-mimo-key")

    dummy_audio = b"ID3" + b"\x00" * 200
    dummy_b64 = base64.b64encode(dummy_audio).decode("utf-8")

    class MockResp:
        status_code = 200
        def json(self):
            return {
                "choices": [{
                    "message": {
                        "audio": {"data": dummy_b64}
                    }
                }]
            }

    class MockClient:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, json=None, headers=None):
            return MockResp()

    monkeypatch.setattr("httpx.Client", MockClient)

    staging = tmp_path / "test_mimo.part"
    ok = tts._generate_mimo_audio("Hello", "mimo_default", staging)
    assert ok is True
    assert staging.read_bytes() == dummy_audio

    # 测试 MiMo 失败时自动 Fallback 到 Edge
    edge_called = []
    def fake_edge_generate(text, staging_path):
        edge_called.append(text)
        staging_path.write_bytes(b"edge-audio" * 20)
        return True

    monkeypatch.setattr(tts, "_generate_edge_audio", fake_edge_generate)
    monkeypatch.setattr(tts, "_generate_mimo_audio", lambda *args: False)

    target_path = tmp_path / "fallback.mp3"
    result = tts._generate_audio_blocking("Hello World", "mimo_default", target_path)
    assert result is True
    assert target_path.is_file()
    assert edge_called == ["Hello World"]
