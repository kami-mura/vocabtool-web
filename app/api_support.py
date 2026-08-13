from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import html
import io
import ipaddress
import json
import random
import re
import threading
import time
from collections import Counter
from pathlib import PurePosixPath
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import LargeBinary, cast, func, insert, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from . import ai as ai_mod
from . import (
    builtin_lookup,
    card_builder,
    config,
    email_verification,
    file_import,
    srs,
    vocab,
)
from .auth import (
    check_request_rate,
    create_session,
    current_user,
    delete_session,
    login_user,
    register_user,
)
from .db import SessionLocal, get_db
from .models import (
    AnkiReviewLog,
    Card,
    Corpus,
    CorpusChapter,
    CorpusWord,
    DailyNewAssignment,
    LookupCache,
    LookupHistory,
    ReadingDisplayPreference,
    ReadingVocabularyPreference,
    ReviewLog,
    ReviewPreference,
    ReviewRequest,
    SavedWord,
    SentenceRefreshState,
    StorageUsage,
    User,
    VocabularyProfile,
    WordEntry,
)

ALLOWED_CARD_TYPES = {"general", "reading", "cloze", "anki", "speaking"}
GENERATABLE_CARD_TYPES = {"general", "reading", "cloze", "speaking"}
ALLOWED_READING_FONTS = {"book", "classic", "sans", "palatino"}
_LOOKUP_CACHE_VERSION = "streamlit-simple-v8-multisense"


# ---------- 工具函数 ----------


def _require_user(db: Session, request: Request) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def _corpus_or_404(db: Session, user: User, corpus_id: int) -> Corpus:
    corpus = (
        db.query(Corpus)
        .filter(Corpus.id == corpus_id, Corpus.user_id == user.id)
        .first()
    )
    if not corpus:
        raise HTTPException(status_code=404, detail="阅读材料不存在")
    return corpus


def _utf8_size(value: str | None) -> int:
    return len((value or "").encode("utf-8"))


_SENTENCE_REFRESH_INTERVAL_SECONDS = 300
def _sentence_refresh_due(db: Session, user_id: int) -> bool:
    """旧卡修复最多每 5 分钟跑一次；状态存数据库，避免进程内字典无界增长。"""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    row = db.get(SentenceRefreshState, user_id)
    if row and row.last_run_at:
        elapsed = (now - row.last_run_at).total_seconds()
        if elapsed < _SENTENCE_REFRESH_INTERVAL_SECONDS:
            return False
        row.last_run_at = now
    else:
        db.add(SentenceRefreshState(user_id=user_id, last_run_at=now))
    try:
        db.commit()
    except IntegrityError:
        # 并发请求同时初始化该状态时，后插入的一方会撞唯一约束；回滚后改用更新。
        db.rollback()
        row = db.get(SentenceRefreshState, user_id)
        if row:
            row.last_run_at = now
            db.commit()
        else:
            db.add(SentenceRefreshState(user_id=user_id, last_run_at=now))
            db.commit()
    return True


def _today_stats(db: Session, user_id: int, now: dt.datetime) -> dict:
    """今日统计以服务端日志为准：复习/新学按不同卡片数计算。"""
    _day, start_of_day, end_of_day = _learning_day(now)
    logs = (
        db.query(ReviewLog.rating, ReviewLog.card_id, ReviewLog.is_new)
        .filter(
            ReviewLog.user_id == user_id,
            ReviewLog.reviewed_at >= start_of_day,
            ReviewLog.reviewed_at < end_of_day,
        )
        .all()
    )
    unique_cards = {card_id for _rating, card_id, _is_new in logs}
    new_cards = {card_id for _rating, card_id, is_new in logs if is_new}
    # 复习与新学按卡片去重且不重叠：今天新学的卡即使当天重复复习，也只算新学。
    review_cards = unique_cards - new_cards
    again_cards = {
        card_id for rating, card_id, _is_new in logs if rating == "again"
    }
    return {
        "studied": len(logs),
        "unique_cards": len(unique_cards),
        "reviews": len(review_cards),
        "new_learned": len(new_cards),
        "again": len(again_cards),
        "again_cards": len(again_cards),
        "again_rate": round(len(again_cards) / len(unique_cards) * 100, 1)
        if unique_cards
        else 0.0,
    }


def _text_bytes(db: Session, *columns) -> int:
    """返回各列 UTF-8 字节数之和的表达式；NULL 列按 0 字节计，
    避免任一列为 NULL 时整行被 SUM 跳过。PostgreSQL 用 octet_length，
    SQLite 转 BLOB。
    """
    if db.bind.dialect.name == "postgresql":
        return sum(
            (func.coalesce(func.octet_length(column), 0) for column in columns), 0
        )
    return sum(
        (
            func.coalesce(func.length(cast(column, LargeBinary)), 0)
            for column in columns
        ),
        0,
    )


def _sum_storage_bytes(
    db: Session, *columns, extra_per_row: int = 0, filters=()
) -> int:
    query = db.query(
        func.coalesce(
            func.sum(_text_bytes(db, *columns)) + extra_per_row * func.count(), 0
        )
    )
    for column, value in filters:
        query = query.filter(column == value)
    return int(query.scalar() or 0)


def _user_storage_bytes(db: Session, user_id: int) -> int:
    total = 0
    total += _sum_storage_bytes(
        db,
        Corpus.title,
        Corpus.raw_text,
        filters=[(Corpus.user_id, user_id)],
    )
    corpus_ids = (
        db.query(Corpus.id).filter(Corpus.user_id == user_id).subquery()
    )
    total += _sum_storage_bytes(
        db,
        CorpusChapter.title,
        CorpusChapter.text,
        filters=[(CorpusChapter.corpus_id, corpus_ids.c.id)],
    )
    total += _sum_storage_bytes(
        db,
        CorpusWord.word,
        extra_per_row=8,
        filters=[(CorpusWord.corpus_id, corpus_ids.c.id)],
    )
    total += _sum_storage_bytes(
        db,
        SavedWord.word,
        extra_per_row=16,
        filters=[(SavedWord.user_id, user_id)],
    )
    total += _sum_storage_bytes(
        db,
        Card.word,
        Card.front,
        Card.back,
        Card.context,
        filters=[(Card.user_id, user_id)],
    )
    total += _sum_storage_bytes(
        db,
        LookupHistory.query,
        LookupHistory.explanation,
        LookupHistory.card_front,
        LookupHistory.card_back,
        filters=[(LookupHistory.user_id, user_id)],
    )
    total += _sum_storage_bytes(
        db,
        ReviewLog.rating,
        ReviewLog.previous_state,
        ReviewLog.previous_word_status,
        extra_per_row=64,
        filters=[(ReviewLog.user_id, user_id)],
    )
    total += _sum_storage_bytes(
        db,
        AnkiReviewLog.source_key,
        extra_per_row=72,
        filters=[(AnkiReviewLog.user_id, user_id)],
    )
    return total


_STORAGE_CACHE_TTL_SECONDS = 60
_STORAGE_QUOTA_LOCK = threading.Lock()
# 全局重型解析/导入并发上限：防止多个大文件同时解压/分词打满内存。
_HEAVY_IMPORT_SEMAPHORE = threading.BoundedSemaphore(2)


def _storage_cache_enabled(db: Session) -> bool:
    """SQLite 单写者模型下，独立连接写缓存会互相锁死；只用 PostgreSQL 缓存。"""
    return db.bind.dialect.name != "sqlite"


def _set_storage_usage(db: Session, user_id: int, used_bytes: int) -> None:
    """把全量统计结果写入缓存表（独立会话提交，不影响调用方事务）。"""
    if not _storage_cache_enabled(db):
        return
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    cache_db = SessionLocal()
    try:
        updated = cache_db.execute(
            update(StorageUsage)
            .where(StorageUsage.user_id == user_id)
            .values(used_bytes=used_bytes, updated_at=now)
        )
        if updated.rowcount:
            cache_db.commit()
            return
        try:
            cache_db.execute(
                insert(StorageUsage).values(
                    user_id=user_id,
                    used_bytes=used_bytes,
                    updated_at=now,
                )
            )
            cache_db.commit()
        except IntegrityError:
            cache_db.rollback()
            cache_db.execute(
                update(StorageUsage)
                .where(StorageUsage.user_id == user_id)
                .values(used_bytes=used_bytes, updated_at=now)
            )
            cache_db.commit()
    finally:
        cache_db.close()


def _cached_storage_bytes(db: Session, user_id: int) -> tuple[int, bool]:
    """返回 (缓存中的已用字节数, 缓存是否新鲜)。

    缓存缺失/过期时全量重算并回写，避免每次写入都扫描用户全部数据。
    """
    if not _storage_cache_enabled(db):
        return _user_storage_bytes(db, user_id), False
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    cache_db = SessionLocal()
    try:
        row = cache_db.get(StorageUsage, user_id)
        if (
            row
            and row.updated_at
            and (now - row.updated_at).total_seconds() < _STORAGE_CACHE_TTL_SECONDS
        ):
            return int(row.used_bytes or 0), True
    finally:
        cache_db.close()
    used = _user_storage_bytes(db, user_id)
    _set_storage_usage(db, user_id, used)
    return used, False


def _reserve_storage_usage(
    db: Session, user_id: int, additional_bytes: int
) -> None:
    """原子地把本次预计新增计入缓存（独立会话提交，不影响调用方事务）。"""
    if additional_bytes <= 0 or not _storage_cache_enabled(db):
        return
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    cache_db = SessionLocal()
    try:
        updated = cache_db.execute(
            update(StorageUsage)
            .where(StorageUsage.user_id == user_id)
            .values(
                used_bytes=StorageUsage.used_bytes + additional_bytes,
                updated_at=now,
            )
        )
        if updated.rowcount:
            cache_db.commit()
            return
        try:
            cache_db.execute(
                insert(StorageUsage).values(
                    user_id=user_id,
                    used_bytes=additional_bytes,
                    updated_at=now,
                )
            )
            cache_db.commit()
        except IntegrityError:
            cache_db.rollback()
            cache_db.execute(
                update(StorageUsage)
                .where(StorageUsage.user_id == user_id)
                .values(
                    used_bytes=StorageUsage.used_bytes + additional_bytes,
                    updated_at=now,
                )
            )
            cache_db.commit()
    finally:
        cache_db.close()


def _require_storage_space(db: Session, user_id: int, additional_bytes: int) -> None:
    # 单进程部署下串行化「读取缓存/全量重算 + 预占」，避免并发导入互相覆盖
    # 缓存或同时通过配额检查；多 worker 部署时应改用 DB advisory lock。
    with _STORAGE_QUOTA_LOCK:
        additional = max(0, additional_bytes)
        limit = config.USER_STORAGE_QUOTA_BYTES
        used, cache_fresh = _cached_storage_bytes(db, user_id)
        if used + additional > limit and cache_fresh:
            # 缓存可能因删除/回滚而失真：超限时重算一次精确值确认。
            used = _user_storage_bytes(db, user_id)
            _set_storage_usage(db, user_id, used)
        if used + additional > limit:
            used_mb = used / 1024 / 1024
            limit_mb = limit / 1024 / 1024
            raise HTTPException(
                status_code=413,
                detail=f"个人存储空间不足：已使用 {used_mb:.1f} MB，上限 {limit_mb:.0f} MB",
            )
        _reserve_storage_usage(db, user_id, additional)


def _try_heavy_import_slot() -> bool:
    """尝试占用一个全局导入/解析槽；占用失败时调用方应返回 429。"""
    return _HEAVY_IMPORT_SEMAPHORE.acquire(blocking=False)


def _release_heavy_import_slot() -> None:
    _HEAVY_IMPORT_SEMAPHORE.release()


async def _read_limited_body(request: Request, limit: int, label: str = "文件") -> bytes:
    """流式读取上传内容，超过上限立即停止，避免先把超大请求完整放入内存。"""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared = int(content_length)
        except ValueError:
            raise HTTPException(status_code=400, detail="文件大小格式不正确") from None
        if declared < 0:
            raise HTTPException(status_code=400, detail="文件大小格式不正确")
        if declared > limit:
            raise HTTPException(status_code=413, detail=f"{label}过大")
    data = bytearray()
    try:
        async with asyncio.timeout(config.UPLOAD_BODY_TIMEOUT_SECONDS):
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > limit:
                    raise HTTPException(status_code=413, detail=f"{label}过大")
    except TimeoutError as exc:
        raise HTTPException(status_code=408, detail=f"{label}上传超时，请重试") from exc
    except ClientDisconnect as exc:
        raise HTTPException(status_code=400, detail="上传中断，请重试") from exc
    return bytes(data)


def _gunzip_limited(data: bytes, limit: int) -> bytes:
    """流式解压 gzip，输出超过 limit 立即中止，防止压缩炸弹占满内存。"""
    output = bytearray()
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as gzip_file:
        while True:
            remaining = limit + 1 - len(output)
            if remaining <= 0:
                raise HTTPException(status_code=413, detail="文件解压后超过大小上限")
            chunk = gzip_file.read(remaining)
            if not chunk:
                break
            output.extend(chunk)
    return bytes(output)


def _decode_upload_body(
    request: Request | None,
    data: bytes,
    filename: str,
    upload_encoding: str | None = None,
    decompress_limit: int | None = None,
) -> tuple[bytes, str]:
    """浏览器端 gzip 压缩上传时解压，并去掉 .gz 后缀。

    upload_encoding 显式传入时（线程池 worker 内无 Request 对象），
    不再读取请求头。
    """
    if upload_encoding is None:
        upload_encoding = (
            request.headers.get("x-upload-encoding", "") if request else ""
        )
    is_gzip = upload_encoding.lower() == "gzip"
    limit = config.MAX_UPLOAD_BYTES if decompress_limit is None else decompress_limit
    if is_gzip or filename.lower().endswith(".gz"):
        try:
            data = _gunzip_limited(data, limit)
        except (OSError, EOFError):
            if data[:2] == b"\x1f\x8b":
                raise HTTPException(status_code=400, detail="压缩文件无法解压") from None
            # 浏览器标记了 gzip 但内容实际未压缩：按原始内容继续处理。
        if len(data) > limit:
            raise HTTPException(status_code=413, detail="文件解压后超过大小上限")
        if filename.lower().endswith(".gz"):
            filename = filename[:-3]
    return data, filename


def _words_with_cards(
    db: Session, user_id: int, words: list[str], *, card_type: str | None = None
) -> set[str]:
    """返回已经在内置 Anki 中拥有至少一张卡片的词；可限定卡片类型。"""
    result: set[str] = set()
    unique_words = list(set(words))
    for index in range(0, len(unique_words), 500):
        chunk = unique_words[index : index + 500]
        query = db.query(Card.word).filter(
            Card.user_id == user_id, Card.word.in_(chunk)
        )
        if card_type:
            query = query.filter(Card.card_type == card_type)
        result.update(row[0] for row in query.distinct().all())
    return result


def _mark_saved_word_mid(db: Session, user_id: int, word: str) -> int:
    """制卡成功后把词保留在词库并标记为 mid；卡片和复习记录不受影响。"""
    row = (
        db.query(SavedWord)
        .filter(SavedWord.user_id == user_id, SavedWord.word == word)
        .first()
    )
    if row:
        row.status = "mid"
        row.updated_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        return 0
    db.add(
        SavedWord(
            user_id=user_id,
            word=word,
            status="mid",
        )
    )
    return 1


def _create_corpus(
    db: Session,
    user: User,
    title: str,
    text: str,
    source_type: str = "paste",
    chapters: list[dict] | None = None,
) -> Corpus:
    clean_title = title.strip()[:200]
    corpus = Corpus(
        user_id=user.id,
        title=clean_title,
        raw_text=text,
        source_type=source_type,
        status="ready",
    )
    db.add(corpus)
    db.flush()
    _populate_corpus(db, corpus, text, chapters)
    db.commit()
    db.refresh(corpus)
    return corpus


def _populate_corpus(
    db: Session,
    corpus: Corpus,
    text: str,
    chapters: list[dict] | None = None,
) -> None:
    """把解析后的正文、章节和词频写入已存在的语料（不提交事务）。"""
    clean_title = corpus.title.strip()[:200]
    counts = vocab.analyze(text)
    chapter_bytes = sum(
        len(str(chapter.get("text") or "").encode("utf-8"))
        + len(str(chapter.get("title") or "").encode("utf-8"))
        for chapter in (chapters or [])
    )
    word_index_bytes = sum(_utf8_size(word) + 8 for word in counts)
    _require_storage_space(
        db,
        corpus.user_id,
        _utf8_size(clean_title)
        + _utf8_size(text)
        + chapter_bytes
        + word_index_bytes
        + 64,
    )
    corpus.raw_text = text
    if chapters:
        chapter_rows = [
            {
                "corpus_id": corpus.id,
                "position": index,
                "title": str(chapter.get("title") or f"第 {index + 1} 章")[:300],
                "text": str(chapter.get("text") or ""),
                "word_count": len(vocab.tokenize(str(chapter.get("text") or ""))),
            }
            for index, chapter in enumerate(chapters)
            if str(chapter.get("text") or "").strip()
        ]
        for start in range(0, len(chapter_rows), 1000):
            db.execute(insert(CorpusChapter), chapter_rows[start : start + 1000])
    word_rows = [
        {"corpus_id": corpus.id, "word": word, "count": count}
        for word, count in counts.items()
    ]
    for start in range(0, len(word_rows), 1000):
        db.execute(insert(CorpusWord), word_rows[start : start + 1000])


def _get_or_create_user_row(
    db: Session, model, user_id: int, **defaults
):
    """并发安全地读取或创建以 user_id 为主键的单行档案/偏好。"""
    for _ in range(2):
        row = db.get(model, user_id)
        if row is not None:
            return row
        row = model(user_id=user_id, **defaults)
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
            db.commit()
            db.refresh(row)
            return row
        except IntegrityError:
            # 另一请求可能刚插入同一行；回滚保存点后重读一次。
            db.expire_all()
            time.sleep(0.05)
    raise RuntimeError("并发创建用户配置失败，请重试")


def _vocabulary_profile(db: Session, user: User) -> VocabularyProfile:
    profile = db.get(VocabularyProfile, user.id)
    if profile:
        return profile

    return _get_or_create_user_row(
        db, VocabularyProfile, user.id, ngsl_known_rank=config.DEFAULT_KNOWN_RANK
    )


def _reading_preference(db: Session, user: User) -> ReadingVocabularyPreference:
    """读取用户的阅读 Hard 窗口；旧用户首次访问时使用默认 1000。"""
    preference = db.get(ReadingVocabularyPreference, user.id)
    if preference:
        if preference.hard_window_size != 1000:
            preference.hard_window_size = 1000
            preference.updated_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
            db.commit()
        return preference
    return _get_or_create_user_row(
        db, ReadingVocabularyPreference, user.id, hard_window_size=1000
    )


def _reading_display_preference(db: Session, user: User) -> ReadingDisplayPreference:
    preference = db.get(ReadingDisplayPreference, user.id)
    if preference:
        return preference
    return _get_or_create_user_row(
        db,
        ReadingDisplayPreference,
        user.id,
        font_family="book",
        font_size=17,
        page_margin=36,
    )


def _review_preference(db: Session, user: User) -> ReviewPreference:
    """读取每个用户自己的每日新卡上限。"""
    preference = db.get(ReviewPreference, user.id)
    if preference:
        return preference
    return _get_or_create_user_row(
        db,
        ReviewPreference,
        user.id,
        new_cards_per_day=min(200, max(0, config.NEW_CARDS_PER_DAY)),
    )


def _learning_day(now: dt.datetime | None = None) -> tuple[str, dt.datetime, dt.datetime]:
    """按站点时区的零点划分每日任务，数据库边界仍使用 UTC 裸时间。"""
    utc_now = (now or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)).replace(tzinfo=dt.timezone.utc)
    try:
        timezone = ZoneInfo(config.APP_TIMEZONE)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("Asia/Shanghai")
    local_now = utc_now.astimezone(timezone)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + dt.timedelta(days=1)
    start_utc = local_start.astimezone(dt.timezone.utc).replace(tzinfo=None)
    end_utc = local_end.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return local_start.date().isoformat(), start_utc, end_utc


def _ensure_daily_new_assignments(
    db: Session,
    user: User,
    day: str,
    count: int,
) -> list[int]:
    """每天固定随机抽卡；同一天刷新页面不会换词。加学卡不占每日配额。"""
    assignments = (
        db.query(DailyNewAssignment)
        .filter(
            DailyNewAssignment.user_id == user.id,
            DailyNewAssignment.day == day,
            DailyNewAssignment.is_extra.is_(False),
        )
        .order_by(DailyNewAssignment.id)
        .all()
    )
    if len(assignments) > count:
        extra_ids = [row.id for row in assignments[count:]]
        db.query(DailyNewAssignment).filter(
            DailyNewAssignment.id.in_(extra_ids)
        ).delete(synchronize_session=False)
        db.commit()
        assignments = assignments[:count]
    assigned_ids = [row.card_id for row in assignments]
    missing = max(0, count - len(assigned_ids))
    if missing:
        all_day_ids = [
            row[0]
            for row in db.query(DailyNewAssignment.card_id).filter(
                DailyNewAssignment.user_id == user.id,
                DailyNewAssignment.day == day,
            ).all()
        ]
        candidate_query = db.query(Card.id).filter(
            Card.user_id == user.id,
            Card.due_at.is_(None),
            Card.buried.is_(False),
        )
        if all_day_ids:
            candidate_query = candidate_query.filter(Card.id.notin_(all_day_ids))
        candidate_ids = [
            row[0]
            for row in candidate_query.order_by(func.random()).limit(missing).all()
        ]
        db.add_all(
            [
                DailyNewAssignment(
                    user_id=user.id,
                    day=day,
                    card_id=card_id,
                    is_extra=False,
                )
                for card_id in candidate_ids
            ]
        )
        db.query(DailyNewAssignment).filter(
            DailyNewAssignment.user_id == user.id,
            DailyNewAssignment.day < (
                dt.date.fromisoformat(day) - dt.timedelta(days=60)
            ).isoformat(),
        ).delete(synchronize_session=False)
        try:
            db.commit()
            assigned_ids.extend(candidate_ids)
        except IntegrityError:
            # 两个标签页同时首次打开时，唯一约束保留同一天的同一份任务。
            db.rollback()
            assigned_ids = [
                row[0]
                for row in db.query(DailyNewAssignment.card_id).filter(
                    DailyNewAssignment.user_id == user.id,
                    DailyNewAssignment.day == day,
                    DailyNewAssignment.is_extra.is_(False),
                ).all()
            ]
    final_rows = (
        db.query(DailyNewAssignment)
        .filter(
            DailyNewAssignment.user_id == user.id,
            DailyNewAssignment.day == day,
            DailyNewAssignment.is_extra.is_(False),
        )
        .order_by(DailyNewAssignment.id)
        .all()
    )
    if len(final_rows) > count:
        db.query(DailyNewAssignment).filter(
            DailyNewAssignment.id.in_([row.id for row in final_rows[count:]])
        ).delete(synchronize_session=False)
        db.commit()
        final_rows = final_rows[:count]
    return [row.card_id for row in final_rows]


def _card_dict(
    card: Card,
    *,
    session_repeat: bool = False,
    session_correct_streak: int = 0,
) -> dict:
    deck = (
        _json_context_field(card.context, "deck")
        if card.card_type == "anki"
        else ""
    )
    defaults = (
        _json_context_field(card.context, "defaults")
        if card.card_type == "speaking"
        else ""
    )
    # 口语卡在数据库里用稳定哈希作 word（避免与正面中文重复/超长冲突），
    # 对外展示时统一用正面表达需求，避免用户看到无意义 ID。
    display_word = card.front if card.card_type == "speaking" else card.word
    return {
        "id": card.id,
        "word": display_word,
        "card_type": card.card_type,
        "front": card.front,
        "back": card.back,
        "context": card.context or "",
        "buried": bool(card.buried),
        "revision": int(card.revision or 0),
        "state": "new" if card.due_at is None else "scheduled",
        "is_learning": str(getattr(card, "state", "") or "") == "learning",
        "learning_step": int(getattr(card, "learning_step", 0) or 0),
        "interval_days": card.interval_days,
        "ease": card.ease,
        "reps": card.reps,
        "session_reduce_day": str(getattr(card, "session_reduce_day", "") or ""),
        "session_reduce_used": int(getattr(card, "session_reduce_used", 0) or 0),
        "due_at": card.due_at.isoformat() if card.due_at else None,
        "next_review_date": (
            card.due_at.replace(tzinfo=dt.timezone.utc)
            .astimezone(srs._site_timezone())
            .date()
            .isoformat()
            if card.due_at
            else None
        ),
        "deck": deck,
        "defaults": defaults,
        "session_repeat": session_repeat,
        "session_correct_streak": max(0, session_correct_streak),
        "session_required_correct": 2,
        "rating_previews": srs.rating_previews(card, session_repeat=session_repeat),
    }


def _json_context_field(context: str, key: str) -> str:
    """从卡片 context 的 JSON 里读取一个字符串字段，异常时返回空串。"""
    if not context:
        return ""
    try:
        value = json.loads(context).get(key)
    except (TypeError, ValueError, AttributeError):
        return ""
    return str(value or "")


def _cookie_secure(request: Request) -> bool:
    """COOKIE_SECURE=true 时强制 Secure；否则仅在 HTTPS 请求下启用。"""
    return bool(config.COOKIE_SECURE) or request.url.scheme == "https"


def _set_auth_cookie(response: Response, token: str, request: Request) -> None:
    response.set_cookie(
        key=config.COOKIE_NAME,
        value=token,
        max_age=config.SESSION_TTL_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        path="/",
    )


def _is_trusted_proxy_peer(host: str) -> bool:
    """只信任本机/内网回源或显式配置的可信代理 IP，防止伪造代理头。

    默认信任 loopback/私网地址；设置 TRUSTED_PROXY_IPS 后收紧为
    白名单（具体 IP 或 token：loopback / private），可防止同内网
    其他主机/容器伪造转发头轮换身份绕过按 IP 限流。
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    configured = [
        item.strip()
        for item in config.TRUSTED_PROXY_IPS.split(",")
        if item.strip()
    ]
    if not configured:
        return ip.is_loopback or ip.is_private
    for item in configured:
        if item == "loopback" and ip.is_loopback:
            return True
        if item == "private" and ip.is_private:
            return True
        try:
            if ip == ipaddress.ip_address(item):
                return True
        except ValueError:
            continue
    return False


def _anonymous_request_identity(request: Request) -> str:
    """Cloudflare Tunnel 会覆盖该头；仅当直接连接来自可信代理时信任，
    否则任何人都能伪造转发头绕过限流。

    反向代理（Caddy/Nginx）部署时使用 X-Forwarded-For 的原始客户端地址，
    代理会把真实 IP 追加到列表末尾，因此只取最后一项。
    """
    peer = request.client.host if request.client else ""
    if _is_trusted_proxy_peer(peer):
        forwarded = request.headers.get("cf-connecting-ip", "").strip()
        if forwarded:
            return forwarded[:64]
        forwarded = request.headers.get("x-forwarded-for", "").strip()
        if forwarded:
            return forwarded.split(",")[-1].strip()[:64]
    return (peer or "unknown")[:64]




__all__ = [
    "ALLOWED_CARD_TYPES",
    "ALLOWED_READING_FONTS",
    "GENERATABLE_CARD_TYPES",
    "_LOOKUP_CACHE_VERSION",
    "Card",
    "ClientDisconnect",
    "Counter",
    "Corpus",
    "CorpusChapter",
    "CorpusWord",
    "DailyNewAssignment",
    "Depends",
    "Dict",
    "HTTPException",
    "IntegrityError",
    "JSONResponse",
    "List",
    "LookupCache",
    "LookupHistory",
    "Optional",
    "PurePosixPath",
    "ReadingDisplayPreference",
    "ReadingVocabularyPreference",
    "Request",
    "Response",
    "ReviewLog",
    "ReviewPreference",
    "ReviewRequest",
    "SentenceRefreshState",
    "Session",
    "SessionLocal",
    "User",
    "VocabularyProfile",
    "WordEntry",
    "SavedWord",
    "ZoneInfo",
    "ZoneInfoNotFoundError",
    "_anonymous_request_identity",
    "_card_dict",
    "_cookie_secure",
    "_corpus_or_404",
    "_create_corpus",
    "_decode_upload_body",
    "_ensure_daily_new_assignments",
    "_learning_day",
    "_populate_corpus",
    "_read_limited_body",
    "_release_heavy_import_slot",
    "_reading_display_preference",
    "_reading_preference",
    "_require_storage_space",
    "_require_user",
    "_review_preference",
    "_sentence_refresh_due",
    "_set_auth_cookie",
    "_mark_saved_word_mid",
    "_sum_storage_bytes",
    "_text_bytes",
    "_today_stats",
    "_try_heavy_import_slot",
    "_user_storage_bytes",
    "_utf8_size",
    "_vocabulary_profile",
    "_words_with_cards",
    "ai_mod",
    "builtin_lookup",
    "card_builder",
    "check_request_rate",
    "config",
    "create_session",
    "current_user",
    "delete_session",
    "dt",
    "email_verification",
    "file_import",
    "func",
    "get_db",
    "gzip",
    "html",
    "insert",
    "json",
    "login_user",
    "or_",
    "random",
    "re",
    "register_user",
    "run_in_threadpool",
    "srs",
    "time",
    "vocab",
]
