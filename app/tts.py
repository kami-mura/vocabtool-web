from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
import time
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

# 学习/查词时按需生成音频并永久缓存（文件名 = 文本哈希，天然不可变）。
TTS_DIR = config.DATA_DIR / "tts"
TTS_TEXT_MAX_CHARS = 240
_TTS_TIMEOUT_SECONDS = 20.0
_TTS_MIN_AUDIO_SIZE = 100
_TTS_VOICE_EN = "en-US-JennyNeural"
_TTS_VOICE_ZH = "zh-CN-XiaoxiaoNeural"
_TTS_URL_PREFIX = "/tts-audio/"
# 全进程同时进行的 edge-tts 生成上限（网络调用是主要成本，超发不增加吞吐）。
_TTS_GENERATION_SLOTS = 6
# 同一时刻最多允许的后台预生成任务数；超出时直接丢弃本次预生成请求。
_TTS_MAX_PREFETCH_TASKS = 8
# 缓存总量超过上限时按最旧优先清理；清理本身按时间节流避免每次生成都扫目录。
_TTS_PRUNE_INTERVAL_SECONDS = 300

# 同一文本的并发生成锁（asyncio.Lock，跨协程等待不会冻结事件循环）。
_generation_locks: dict[str, asyncio.Lock] = {}
_generation_locks_guard = threading.Lock()
# 全局生成信号量：限制所有用户同时进行的 edge-tts 网络调用数。
_generation_slots = asyncio.Semaphore(_TTS_GENERATION_SLOTS)
# 当前活跃的后台预生成任务数（只在事件循环线程读写）。
_active_prefetch_tasks = 0
# 上次缓存清理的时间（time.monotonic），用于节流。
_last_prune_monotonic = 0.0


def _normalize_tts_text(text: str) -> str:
    cleaned = str(text or "").replace("______", "")  # Cloze 挖空下划线不朗读
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)  # Anki 导入卡可能带 HTML 标签
    # 阅读卡例句里的 **加粗** 标记与 Anki cloze 占位符（{{c1::word}}）都不朗读。
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\{\{c\d+::(.*?)\}\}", r"\1", cleaned)
    cleaned = cleaned.replace("*", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:TTS_TEXT_MAX_CHARS]


def _pick_voice(text: str) -> str:
    return _TTS_VOICE_ZH if re.search(r"[\u4e00-\u9fff]", text) else _TTS_VOICE_EN


def _audio_path(text: str) -> tuple[Path, str]:
    voice = _pick_voice(text)
    digest = hashlib.sha1((text + "\x1f" + voice).encode("utf-8")).hexdigest()[:24]
    return TTS_DIR / f"{digest}.mp3", voice


def _audio_exists(path: Path) -> bool:
    return path.is_file() and path.stat().st_size >= _TTS_MIN_AUDIO_SIZE


def _generation_lock(text: str) -> asyncio.Lock:
    with _generation_locks_guard:
        lock = _generation_locks.get(text)
        if lock is None:
            lock = asyncio.Lock()
            _generation_locks[text] = lock
        return lock


def _generate_audio_blocking(text: str, voice: str, path: Path) -> bool:
    """在工作线程里生成音频：edge-tts 的任何阻塞都不会冻结事件循环。"""
    import asyncio

    import edge_tts

    path.parent.mkdir(parents=True, exist_ok=True)
    # 先写 .part 再原子替换，避免并发或中断留下半截文件。
    staging = path.with_name(f"{path.name}.{threading.get_ident()}.part")
    try:
        async def _run() -> None:
            communicate = edge_tts.Communicate(text, voice)
            await asyncio.wait_for(
                communicate.save(str(staging)), timeout=_TTS_TIMEOUT_SECONDS
            )

        asyncio.run(_run())
        if not staging.is_file() or staging.stat().st_size < _TTS_MIN_AUDIO_SIZE:
            raise RuntimeError("generated audio file is missing or too small")
        staging.replace(path)
        return True
    except Exception as exc:
        text_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        logger.warning(
            "tts generation failed text_sha256=%s length=%s error=%s",
            text_digest,
            len(text),
            type(exc).__name__,
        )
        staging.unlink(missing_ok=True)
        return False


def _prune_tts_cache_if_due() -> None:
    """缓存总量超过上限时删除最旧的音频；按时间节流避免频繁扫目录。"""
    global _last_prune_monotonic
    now = time.monotonic()
    if now - _last_prune_monotonic < _TTS_PRUNE_INTERVAL_SECONDS:
        return
    _last_prune_monotonic = now
    limit = max(1, int(config.TTS_CACHE_MAX_BYTES))
    try:
        entries = [
            (path, path.stat().st_mtime, path.stat().st_size)
            for path in TTS_DIR.glob("*.mp3")
        ]
    except OSError:
        return
    overflow = sum(size for _path, _mtime, size in entries) - limit
    if overflow <= 0:
        return
    for path, _mtime, size in sorted(entries, key=lambda entry: entry[1]):
        if overflow <= 0:
            break
        try:
            path.unlink(missing_ok=True)
            overflow -= size
        except OSError:
            continue


async def audio_url_for_text(raw_text: str) -> str | None:
    """按文本返回可播放音频 URL：已有缓存直接返回，否则生成一次。失败返回 None。"""
    text = _normalize_tts_text(raw_text)
    if not text:
        return None
    path, voice = _audio_path(text)
    if _audio_exists(path):
        return _TTS_URL_PREFIX + path.name
    lock = _generation_lock(text)
    async with lock:
        try:
            if _audio_exists(path):
                return _TTS_URL_PREFIX + path.name
            _prune_tts_cache_if_due()
            async with _generation_slots:
                if await asyncio.to_thread(_generate_audio_blocking, text, voice, path):
                    return _TTS_URL_PREFIX + path.name
            return None
        finally:
            # 生成结束即移除锁条目，防止恶意/大量不同文本导致字典无界增长；
            # 短暂并发窗口内重复生成无害（幂等 + 原子改名）。
            with _generation_locks_guard:
                _generation_locks.pop(text, None)


def is_audio_filename(name: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{24}\.mp3", str(name or "")))


# 后台预生成任务集合：防止任务被 GC 回收后生成中断。
_background_tasks: set[asyncio.Task] = set()


async def _prefetch_worker(texts: list[str], concurrency: int = 4) -> None:
    semaphore = asyncio.Semaphore(concurrency)

    async def generate_one(text: str) -> None:
        async with semaphore:
            # 幂等：已生成或已缓存的立即返回，只对缺失的音频发起请求。
            await audio_url_for_text(text)

    try:
        await asyncio.gather(*(generate_one(text) for text in texts))
    except Exception:  # 预生成失败只记录，不影响请求本身
        logger.warning("tts prefetch worker failed", exc_info=True)


def schedule_prefetch(raw_texts: list[str]) -> int:
    """后台预生成一批文本的音频（进入学习队列时调用），不阻塞请求。

    同一时刻的任务数受限，超出时直接丢弃本次请求，防止匿名流量用
    预生成接口无限创建后台任务。
    """
    global _active_prefetch_tasks
    texts = [_normalize_tts_text(text) for text in raw_texts]
    texts = list(dict.fromkeys(text for text in texts if text))
    if not texts:
        return 0
    if _active_prefetch_tasks >= _TTS_MAX_PREFETCH_TASKS:
        return 0
    _active_prefetch_tasks += 1
    task = asyncio.get_running_loop().create_task(_prefetch_worker(texts))

    def _done(done_task: asyncio.Task) -> None:
        global _active_prefetch_tasks
        _background_tasks.discard(done_task)
        _active_prefetch_tasks -= 1
        if not done_task.cancelled():
            done_task.exception()  # 取回异常避免 "Task exception was never retrieved"

    task.add_done_callback(_done)
    _background_tasks.add(task)
    return len(texts)
