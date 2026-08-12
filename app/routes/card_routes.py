from __future__ import annotations

import hashlib
import json
import logging
import math
import threading

from fastapi import APIRouter, BackgroundTasks, Response

from .. import anki_exchange, speaking_needs, wordlists
from ..api_support import (
    ALLOWED_CARD_TYPES,
    GENERATABLE_CARD_TYPES,
    Card,
    Corpus,
    CorpusChapter,
    CorpusWord,
    DailyNewAssignment,
    Depends,
    HTTPException,
    IntegrityError,
    JSONResponse,
    Request,
    ReviewLog,
    ReviewRequest,
    SavedWord,
    Session,
    User,
    _card_dict,
    _corpus_or_404,
    _create_corpus,
    _decode_upload_body,
    _ensure_daily_new_assignments,
    _learning_day,
    _mark_saved_word_mid,
    _read_limited_body,
    _release_heavy_import_slot,
    _require_storage_space,
    _require_user,
    _review_preference,
    _sentence_refresh_due,
    _today_stats,
    _try_heavy_import_slot,
    _utf8_size,
    _words_with_cards,
    ai_mod,
    card_builder,
    check_request_rate,
    config,
    dt,
    file_import,
    func,
    get_db,
    html,
    or_,
    random,
    re,
    run_in_threadpool,
    srs,
    time,
    vocab,
)
from ..db import SessionLocal, is_sqlite_busy_error, reserve_sqlite_write
from ..schemas import (
    ArticleIn,
    CardsBatchDeleteIn,
    CardStudioCreateIn,
    CardTargetsIn,
    ReviewBatchIn,
    ReviewIn,
    ReviewSettingsIn,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_ARTICLE_GENERATION: dict[int, dict[str, object]] = {}
_ARTICLE_GENERATION_GUARD = threading.Lock()


def _article_generation_status(user_id: int) -> dict[str, object] | None:
    """返回当前用户文章后台任务状态；结果保留十分钟供页面恢复。"""
    with _ARTICLE_GENERATION_GUARD:
        entry = _ARTICLE_GENERATION.get(user_id)
        if entry is None:
            return None
        finished_at = float(entry.get("finished_at") or 0)
        if entry.get("state") != "generating" and time.monotonic() - finished_at > 600:
            _ARTICLE_GENERATION.pop(user_id, None)
            return None
        return {
            key: value
            for key, value in entry.items()
            if key in {"state", "total", "completed", "detail", "error"}
        }


def _start_article_generation(user_id: int, total: int) -> bool:
    with _ARTICLE_GENERATION_GUARD:
        current = _ARTICLE_GENERATION.get(user_id)
        if current and current.get("state") == "generating":
            return False
        _ARTICLE_GENERATION[user_id] = {
            "state": "generating",
            "total": max(1, total),
            "completed": 0,
            "detail": "正在准备文章…",
        }
        return True


def _update_article_generation(user_id: int, *, completed: int, detail: str) -> None:
    with _ARTICLE_GENERATION_GUARD:
        entry = _ARTICLE_GENERATION.get(user_id)
        if entry and entry.get("state") == "generating":
            entry["completed"] = completed
            entry["detail"] = detail


def _finish_article_generation(user_id: int, *, error: str = "") -> None:
    with _ARTICLE_GENERATION_GUARD:
        entry = _ARTICLE_GENERATION.get(user_id)
        if entry is None:
            return
        entry["state"] = "failed" if error else "done"
        entry["detail"] = "生成失败" if error else "生成完成"
        entry["error"] = error
        if not error:
            entry["completed"] = entry.get("total", 0)
        entry["finished_at"] = time.monotonic()


def _reserve_review_write(db: Session, user_id: int, endpoint: str):
    """SQLite 评分在读取卡片前先取得写锁；繁忙时返回可重试响应。"""
    try:
        reserve_sqlite_write(db)
    except Exception as exc:
        db.rollback()
        if not is_sqlite_busy_error(exc):
            raise
        logger.warning(
            "review database busy user_id=%s endpoint=%s", user_id, endpoint
        )
        return JSONResponse(
            status_code=503,
            content={
                "detail": "数据库繁忙，请稍后重试",
                "code": "db_busy",
            },
            headers={"Retry-After": "1"},
        )
    return None


def _delete_ai_article_tree(db: Session, corpus_id: int) -> None:
    """删除一篇 AI 文章及其章节、词表。"""
    db.query(CorpusChapter).filter(
        CorpusChapter.corpus_id == corpus_id
    ).delete()
    db.query(CorpusWord).filter(
        CorpusWord.corpus_id == corpus_id
    ).delete()
    db.query(Corpus).filter(Corpus.id == corpus_id).delete(
        synchronize_session=False
    )


def _delete_previous_ai_articles(db: Session, user_id: int) -> None:
    """生成新文章前清掉所有旧 AI 文章，只保留最新一篇（即刚生成的）。"""
    corpus_ids = [
        row[0]
        for row in db.query(Corpus.id)
        .filter(
            Corpus.user_id == user_id,
            Corpus.source_type == "ai",
        )
        .all()
    ]
    if not corpus_ids:
        return
    for corpus_id in corpus_ids:
        _delete_ai_article_tree(db, corpus_id)


def _delete_ai_articles_before(db: Session, user_id: int, cutoff) -> None:
    """只保留今天生成的 AI 文章：清掉 cutoff 之前生成的旧文章。"""
    corpus_ids = [
        row[0]
        for row in db.query(Corpus.id)
        .filter(
            Corpus.user_id == user_id,
            Corpus.source_type == "ai",
            Corpus.created_at < cutoff,
        )
        .all()
    ]
    if not corpus_ids:
        return
    for corpus_id in corpus_ids:
        _delete_ai_article_tree(db, corpus_id)


def _parse_line_list(
    text: str, *, separators: str, max_len: int, validator
) -> list[str]:
    """通用的按行/分隔符去重解析；卡片词表和口语需求共用。"""
    result: list[str] = []
    seen: set[str] = set()
    for raw in re.split(separators, text):
        value = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", raw).strip()
        value = re.sub(r"\s+", " ", value)
        if not value or len(value) > max_len or not validator(value):
            continue
        key = value
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _parse_card_target_list(text: str) -> list[str]:
    return _parse_line_list(
        text,
        separators=r"[\n,;；，]+",
        max_len=100,
        validator=lambda value: bool(re.search(r"[A-Za-z]", value)),
    )


def _parse_expression_needs(text: str) -> list[str]:
    """口语卡的目标列表：每行一个中文表达需求，不做拉丁字母过滤。"""
    return _parse_line_list(
        text,
        separators=r"[\n;；]+",
        max_len=100,
        validator=lambda value: bool(re.search(r"[\u4e00-\u9fff]", value)),
    )


@router.post("/card-studio/targets")
def card_studio_targets(
    body: CardTargetsIn, request: Request, db: Session = Depends(get_db)
):
    """按旧 Streamlit 的三类来源准备可编辑的目标词表。"""
    user = _require_user(db, request)
    if body.to_rank < body.from_rank:
        raise HTTPException(status_code=400, detail="结束排名必须大于等于起始排名")
    candidates: list[tuple[str, int | None, int]] = []
    if body.source == "corpus":
        if body.text.strip():
            # 粘贴文本与上传文件同一套提取逻辑：按词频分析后再做 NGSL 筛选。
            # 文本/文件按词头还原词形；只有一行行粘贴的词汇表保留原文。
            analyzed = vocab.analyze(body.text)
            for word, occurrences in analyzed.items():
                rank = vocab.rank_of(word)
                if (rank and body.from_rank <= rank <= body.to_rank) or (
                    rank is None and body.include_unknown
                ):
                    candidates.append((word, rank, occurrences))
            candidates.sort(
                key=lambda item: (item[1] is None, item[1] or 999_999, -item[2])
            )
        elif body.corpus_id:
            corpus = _corpus_or_404(db, user, body.corpus_id)
            rows = db.query(CorpusWord).filter(CorpusWord.corpus_id == corpus.id).all()
            for row in rows:
                rank = vocab.rank_of(row.word)
                if (rank and body.from_rank <= rank <= body.to_rank) or (
                    rank is None and body.include_unknown
                ):
                    candidates.append((row.word, rank, row.count))
            candidates.sort(
                key=lambda item: (item[1] is None, item[1] or 999_999, -item[2])
            )
        else:
            raise HTTPException(status_code=400, detail="请选择文件或粘贴文本")
    elif body.source == "wordlist":
        candidates = [(word, vocab.rank_of(word.lower()), 0) for word in _parse_card_target_list(body.text)]
    elif body.source == "builtin":
        if body.list_id not in wordlists.list_ids():
            raise HTTPException(status_code=400, detail="无效词表")
        raw = wordlists.load_wordlist(body.list_id)
        if body.ngsl_filter:
            candidates = [
                (word, rank or None, 0)
                for word, rank in raw.items()
                if rank and body.from_rank <= rank <= body.to_rank
            ]
        else:
            # 默认不做 NGSL 筛选：整个词表都是候选，未知词（rank None）也保留。
            candidates = [(word, rank or None, 0) for word, rank in raw.items()]
        # 词表内默认按 NGSL 排名升序，未知词排最后（randomize 之后会打乱）。
        candidates.sort(key=lambda item: (item[1] is None, item[1] or 999_999))
    elif body.source == "ngsl":
        candidates = [
            (word, rank, 0)
            for word, rank in vocab.load_ngsl().items()
            if body.from_rank <= rank <= body.to_rank
        ]
        candidates.sort(key=lambda item: item[1] or 999_999)
    elif body.source == "saved":
        # 只提取生词库中的 hard 词：mid=已制卡、easy=已掌握，都不应重复提取
        rows = (
            db.query(SavedWord)
            .filter(SavedWord.user_id == user.id, SavedWord.status == "hard")
            .order_by(SavedWord.updated_at.desc())
            .all()
        )
        candidates = [(row.word, vocab.rank_of(row.word), 0) for row in rows]
    else:
        raise HTTPException(status_code=400, detail="无效目标词来源")
    if body.ngsl_filter and body.source in {"wordlist", "saved"}:
        candidates = [
            item
            for item in candidates
            if item[1] is not None and body.from_rank <= item[1] <= body.to_rank
        ]
        candidates.sort(key=lambda item: item[1] or 999_999)
    if body.randomize:
        random.shuffle(candidates)
    words = [item[0] for item in candidates]
    if body.source == "ngsl":
        # NGSL 范围筛选：先排除已有同类型卡片的词，再按排名顺序
        # 取最多 count 个，保证排除后数量仍然足额。
        if body.card_type in GENERATABLE_CARD_TYPES:
            existing_by_type = _words_with_cards(
                db, user.id, words, card_type=body.card_type
            )
        else:
            existing_by_type = set()
        candidates = [
            item for item in candidates if item[0] not in existing_by_type
        ][: body.count]
    else:
        # 生词库一次全部导入；其余来源仍按目标个数截断。
        if body.source != "saved":
            candidates = candidates[: body.count]
        words = [item[0] for item in candidates]
        # 自动排除已有同类型卡片的单词，避免重复制卡。
        if body.card_type in GENERATABLE_CARD_TYPES:
            existing_by_type = _words_with_cards(
                db, user.id, words, card_type=body.card_type
            )
        else:
            existing_by_type = set()
        candidates = [
            item
            for item in candidates
            if item[0] not in existing_by_type
        ]
    words = [item[0] for item in candidates]
    card_words = _words_with_cards(db, user.id, words)
    return {
        "source": body.source,
        "count": len(candidates),
        "words": [
            {
                "word": word,
                "rank": rank,
                "occurrences": occurrences,
                "has_card": word in card_words,
            }
            for word, rank, occurrences in candidates
        ],
    }


@router.post("/card-studio/targets-file")
async def card_studio_targets_file(
    request: Request,
    filename: str,
    from_rank: int = 1,
    to_rank: int = 31_000,
    count: int = 100,
    include_unknown: bool = True,
    card_type: str = "reading",
    db: Session = Depends(get_db),
):
    """临时解析上传文件并返回目标词；原文件和正文均不写入数据库。"""
    user = _require_user(db, request)
    if not 1 <= from_rank <= to_rank <= 100_000:
        raise HTTPException(status_code=400, detail="NGSL 排名范围不正确")
    count = min(5000, max(1, count))
    if not check_request_rate(
        db,
        action="targets-file",
        identity=f"u{user.id}",
        limit=300,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="解析请求过多，请稍后再试")
    data = await _read_limited_body(request, config.MAX_UPLOAD_BYTES)
    data, filename = _decode_upload_body(request, data, filename)
    if not _try_heavy_import_slot():
        raise HTTPException(status_code=429, detail="服务器导入任务繁忙，请稍后重试")
    try:
        try:
            extractor = (
                file_import.extract_pdf_content_isolated
                if filename.lower().endswith(".pdf")
                else file_import.extract_file_content
            )
            text, source_type, _chapters = await run_in_threadpool(
                extractor,
                filename,
                data,
                config.MAX_CORPUS_CHARS,
            )
        except file_import.ImportFileError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        candidates: list[tuple[str, int | None, int]] = []
        # 上传文件按词头还原词形；只有一行行粘贴的词汇表保留原文。
        analyzed = await run_in_threadpool(vocab.analyze, text)
    finally:
        _release_heavy_import_slot()
    for word, occurrences in analyzed.items():
        rank = vocab.rank_of(word)
        if (rank and from_rank <= rank <= to_rank) or (
            rank is None and include_unknown
        ):
            candidates.append((word, rank, occurrences))
    candidates.sort(
        key=lambda item: (item[1] is None, item[1] or 999_999, -item[2])
    )
    candidates = candidates[:count]
    words = [item[0] for item in candidates]
    card_words = _words_with_cards(db, user.id, words)
    # 自动排除已有同类型卡片的单词，避免重复制卡。
    if card_type in GENERATABLE_CARD_TYPES:
        existing_by_type = _words_with_cards(
            db, user.id, words, card_type=card_type
        )
    else:
        existing_by_type = set()
    candidates = [item for item in candidates if item[0] not in existing_by_type]
    return {
        "source": "temporary_file",
        "source_type": source_type,
        "temporary": True,
        "count": len(candidates),
        "words": [
            {
                "word": word,
                "rank": rank,
                "occurrences": occurrences,
                "has_card": word in card_words,
            }
            for word, rank, occurrences in candidates
        ],
    }


@router.get("/card-studio/progress")
def card_studio_progress(request: Request, db: Session = Depends(get_db)):
    """轮询当前用户的 AI 制卡进度（已制成 X / N 张卡）。"""
    user = _require_user(db, request)
    return {"progress": ai_mod.card_generation_progress(user.id)}


@router.get("/wordlists")
def list_wordlists(request: Request, db: Session = Depends(get_db)):
    """内置词表列表，供制卡来源下拉使用。"""
    _require_user(db, request)
    counts = wordlists.wordlist_counts()
    return {
        "lists": [
            {"id": entry["id"], "name": entry["name"], "count": counts[entry["id"]]}
            for entry in wordlists.LISTS
        ]
    }


@router.get("/card-studio/needs")
def card_studio_needs(request: Request, db: Session = Depends(get_db)):
    """内置口语表达需求集：按分类返回，并标出用户已制过卡的条目。"""
    user = _require_user(db, request)
    own = {
        str(row[0] or "")
        for row in db.query(Card.front)
        .filter(Card.user_id == user.id, Card.card_type == "speaking")
        .all()
    }
    return {
        "categories": [
            {
                "id": group["id"],
                "name": group["name"],
                "needs": [
                    {
                        "id": need["id"],
                        "front": need["front"],
                        "has_card": need["front"] in own,
                    }
                    for need in group["needs"]
                ],
            }
            for group in speaking_needs.categories()
        ],
        "total": len(speaking_needs.NEEDS),
    }


def _create_speaking_cards(
    db: Session, user: User, words: list[str]
) -> dict:
    """口语卡制卡：每行一个表达需求，AI 在反面生成 3 个常用表达。"""
    fronts = _parse_expression_needs("\n".join(words))[: config.MAX_CARDS_PER_RUN]
    if not fronts:
        raise HTTPException(
            status_code=400,
            detail="没有可用于制卡的表达需求（每行一个，需包含中文）",
        )

    existing_rows = (
        db.query(Card)
        .filter(Card.user_id == user.id, Card.card_type == "speaking")
        .all()
    )
    existing_by_front = {str(row.front or "") for row in existing_rows}
    ai_fronts = [front for front in fronts if front not in existing_by_front]

    ai_errors: dict[str, str] = {}
    ai_requests = 0
    ai_timings: dict[str, float | int] = {
        "ai_wait_seconds": 0.0,
        "format_retry_count": 0,
        "db_write_seconds": 0.0,
    }
    generated: dict[str, dict] = {}
    if ai_fronts:
        # 先结束只读事务并释放连接：长 AI 调用期间不占用连接池。
        db.commit()
        generated, ai_errors, ai_requests, ai_timings = (
            ai_mod.generate_card_content_in_batches(
                db, user.id, ai_fronts, "speaking"
            )
        )

    existing_count = 0
    existing_front_list: list[str] = []
    failed: list[str] = []
    prepared_cards: list[dict[str, str]] = []
    for front in fronts:
        if front in existing_by_front:
            existing_count += 1
            existing_front_list.append(front)
            continue
        content = generated.get(front)
        if not content or not str(content.get("m") or "").strip():
            failed.append(f"{front}：{ai_errors.get(front) or 'AI 没有返回可用表达'}")
            continue
        expressions = ai_mod._parse_speaking_expressions(
            str(content.get("m") or "")
        )
        if len(expressions) < 2 or any(
            ai_mod._speaking_expression_has_blank_slot(expression)
            for expression in expressions
        ):
            failed.append(f"{front}：AI 返回的表达数量不足或包含空白占位，已跳过")
            continue
        prepared_cards.append(
            {
                "word": speaking_needs.need_id_for_front(front),
                "front": front,
                "back": " || ".join(expressions[:3]),
                "context": json.dumps(
                    {"defaults": speaking_needs.DEFAULT_CONTEXT_LABEL},
                    ensure_ascii=False,
                ),
            }
        )

    seen_words: set[str] = set()
    deduped_cards: list[dict[str, str]] = []
    for item in prepared_cards:
        if item["word"] in seen_words:
            existing_count += 1
            continue
        seen_words.add(item["word"])
        deduped_cards.append(item)
    prepared_cards = deduped_cards

    db_write_started = time.time()
    _require_storage_space(
        db,
        user.id,
        sum(
            _utf8_size(item["word"])
            + _utf8_size(item["front"])
            + _utf8_size(item["back"])
            + _utf8_size(item["context"])
            for item in prepared_cards
        ),
    )

    def _new_card(item: dict[str, str]) -> Card:
        return Card(
            user_id=user.id,
            word=item["word"],
            card_type="speaking",
            front=item["front"],
            back=item["back"],
            context=item["context"],
            state="new",
            due_at=None,
            reps=0,
            lapses=0,
            interval_days=0.0,
            ease=2.5,
        )

    created_now = len(prepared_cards)
    created_front_list = [item["front"] for item in prepared_cards]
    for item in prepared_cards:
        db.add(_new_card(item))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        created_now = 0
        created_front_list = []
        seen_again: set[str] = set()
        for start in range(0, len(prepared_cards), 500):
            chunk = prepared_cards[start : start + 500]
            rows = (
                db.query(Card.word)
                .filter(
                    Card.user_id == user.id,
                    Card.word.in_([item["word"] for item in chunk]),
                    Card.card_type == "speaking",
                )
                .all()
            )
            existing_words_now = {row[0] for row in rows}
            for item in chunk:
                if item["word"] in existing_words_now or item["word"] in seen_again:
                    existing_count += 1
                    existing_front_list.append(item["front"])
                    continue
                seen_again.add(item["word"])
                db.add(_new_card(item))
                created_front_list.append(item["front"])
                created_now += 1
        db.commit()

    total = db.query(Card).filter(Card.user_id == user.id).count()
    audio_texts: list[str] = []
    audio_seen: set[str] = set()
    for item in prepared_cards[:200]:
        for expression in item["back"].split(" || "):
            text = ai_mod._speaking_expression_audio_text(expression)
            if text and text not in audio_seen:
                audio_seen.add(text)
                audio_texts.append(text)
            if len(audio_texts) >= 200:
                break
        if len(audio_texts) >= 200:
            break

    ai_error_summary = next(
        (str(message) for message in ai_errors.values() if str(message).strip()), ""
    )
    return {
        "ok": True,
        "created": created_now,
        "created_words": created_front_list,
        "existing": existing_count,
        "existing_words": existing_front_list,
        "failed": failed,
        "error": ai_error_summary if created_now == 0 and failed else "",
        "ai_requests": ai_requests,
        "total": total,
        "audio_texts": audio_texts,
        "timings": {
            "ai_wait_seconds": float(ai_timings.get("ai_wait_seconds", 0.0)),
            "format_retry_count": int(ai_timings.get("format_retry_count", 0)),
            "db_write_seconds": round(time.time() - db_write_started, 1),
        },
    }


@router.post("/card-studio/cards")
def create_cards_from_studio(
    body: CardStudioCreateIn, request: Request, db: Session = Depends(get_db)
):
    """唯一的站内制卡入口；查词和阅读页只能把词送到这里。"""
    user = _require_user(db, request)
    if not check_request_rate(
        db,
        action="card-studio-cards",
        identity=f"u{user.id}",
        limit=1000,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="制卡请求过多，请稍后再试")
    if body.card_type not in GENERATABLE_CARD_TYPES:
        raise HTTPException(status_code=400, detail="无效卡片类型")
    try:
        if body.card_type == "speaking":
            result = _create_speaking_cards(db, user, body.words)
            ai_mod.mark_card_generation_done(
                user.id,
                {
                    "created": result.get("created", 0),
                    "existing": result.get("existing", 0),
                    "failed": result.get("failed", []),
                    "error": result.get("error", ""),
                    "total": result.get("created", 0)
                    + result.get("existing", 0)
                    + len(result.get("failed", [])),
                },
            )
            return result
    except HTTPException:
        raise
    except Exception:
        ai_mod.mark_card_generation_done(user.id, {"error": "制卡内部错误，请稍后刷新查看"})
        raise
    words = _parse_card_target_list("\n".join(body.words))[: config.MAX_CARDS_PER_RUN]
    if not words:
        raise HTTPException(status_code=400, detail="没有可用于制卡的词条")
    try:
        if body.corpus_id:
            db_write_started = time.time()
            corpus = _corpus_or_404(db, user, body.corpus_id)
            corpus_words = {
                row[0]
                for row in db.query(CorpusWord.word)
                .filter(CorpusWord.corpus_id == corpus.id)
                .all()
            }
            # 语料词表按词头小写存储；用户提交时保留其书写大小写，
            # 匹配只用于确认词在语料中，制卡身份仍以用户输入为准。
            selected = [
                word
                for word in words
                if vocab.normalize_word(word.lower()) in corpus_words
            ]
            # 语料制卡会写入完整句子、释义和复习状态。预留一个保守空间，
            # 避免 build_cards 内部提交后才发现用户已经超过总配额。
            _require_storage_space(db, user.id, len(selected) * 10_000)
            created, total, _ = card_builder.build_cards(
                db, user.id, corpus, selected, body.card_type, len(selected) or 1
            )
            card_words = _words_with_cards(db, user.id, selected)
            for word in card_words:
                _mark_saved_word_mid(db, user.id, word)
            db.commit()
            db_write_seconds = round(time.time() - db_write_started, 1)
            result = {
                "ok": True,
                "created": created,
                "existing": max(0, len(selected) - created),
                "failed": [
                    word
                    for word in words
                    if vocab.normalize_word(word.lower()) not in corpus_words
                ],
                "total": total,
                "timings": {
                    "ai_wait_seconds": 0.0,
                    "format_retry_count": 0,
                    "db_write_seconds": db_write_seconds,
                },
            }
            ai_mod.mark_card_generation_done(
                user.id,
                {
                    "created": result["created"],
                    "existing": result["existing"],
                    "failed": result["failed"],
                    "error": "",
                    "total": result["created"] + result["existing"] + len(result["failed"]),
                },
            )
            return result
    except HTTPException:
        ai_mod.mark_card_generation_done(user.id, {"error": "制卡失败，请稍后刷新查看"})
        raise
    except Exception:
        ai_mod.mark_card_generation_done(user.id, {"error": "制卡内部错误，请稍后刷新查看"})
        raise

    try:
        # 括号标注（mandarin (fruit)）只传给 AI 消歧；卡片身份/发音/高亮
        # 一律只用括号前的单词，避免库里存出带释义的“单词”。
        base_words: list[str] = []
        ai_words: list[str] = []
        for raw_word in words:
            display_word, _ = ai_mod._split_card_entry(raw_word)
            base_words.append(display_word.strip()[:100])
            ai_words.append(raw_word.strip()[:100])
        existing_rows = (
            db.query(Card)
            .filter(
                Card.user_id == user.id,
                Card.word.in_(base_words),
                Card.card_type == body.card_type,
            )
            .all()
        )
        existing_words = {row.word for row in existing_rows}
        ai_need = [
            ai_word
            for base_word, ai_word in zip(base_words, ai_words, strict=True)
            if base_word not in existing_words
        ]
    
        ai_errors: dict[str, str] = {}
        ai_requests = 0
        ai_timings: dict[str, float | int] = {
            "ai_wait_seconds": 0.0,
            "format_retry_count": 0,
            "db_write_seconds": 0.0,
        }
        generated: dict[str, dict] = {}
        if ai_need:
            # 先结束只读事务并释放连接：长 AI 调用期间不占用连接池。
            db.commit()
            generated, ai_errors, ai_requests, ai_timings = (
                ai_mod.generate_card_content_in_batches(db, user.id, ai_need, body.card_type)
            )
    
        existing_count = 0
        existing_word_list: list[str] = []
        failed: list[str] = []
        prepared_cards: list[dict[str, str]] = []
        for raw_word, base_word, ai_word in zip(words, base_words, ai_words, strict=True):
            if base_word in existing_words:
                existing_count += 1
                existing_word_list.append(base_word)
                _mark_saved_word_mid(db, user.id, base_word)
                continue
            content = generated.get(ai_word) or generated.get(base_word)
            if not content or not str(content.get("m") or "").strip():
                failed.append(f"{raw_word}：{ai_errors.get(ai_word) or '没有可用释义'}")
                continue
            meaning = str(content.get("m") or "").strip()
            display_word, _ = ai_mod._split_card_entry(raw_word)
            if body.card_type == "general":
                front = display_word
                back = meaning
                context = ""
            else:
                sentence = card_builder.complete_sentence(
                    str(content.get("e") or "").strip()
                )
                if not sentence:
                    # 例句卡正面必须是完整句子；AI 没返回例句就不建卡，避免空正面。
                    failed.append(f"{raw_word}：AI 没有返回可用的例句")
                    continue
                if not card_builder.is_complete_sentence(sentence, base_word):
                    failed.append(f"{raw_word}：AI 例句未包含目标词或合理词形")
                    continue
                if body.card_type == "cloze":
                    front = card_builder.sentence_front(sentence, base_word, cloze=True)
                    if front.count("______") != 1:
                        failed.append(f"{raw_word}：Cloze 例句必须只挖空一次目标词")
                        continue
                    back = (
                        f"{card_builder.sentence_front(sentence, base_word, cloze=False)}\n\n"
                        f"{meaning}"
                    )
                    context = sentence
                else:
                    meaning_parts = ai_mod._reading_meaning_parts(meaning)
                    back = " | ".join(meaning_parts) if meaning_parts else meaning
                    front = card_builder.sentence_front(sentence, base_word, cloze=False)
                    context = sentence
            prepared_cards.append(
                {
                    "word": base_word,
                    "front": front,
                    "back": back,
                    "context": context,
                }
            )
    
        # 同一批内可能出现重复词（大小写/注解形式不同），先按 word 去重，
        # 避免同批插入撞 uq_user_word_type 唯一键导致整个请求 500。
        seen_words: set[str] = set()
        deduped_cards: list[dict[str, str]] = []
        for item in prepared_cards:
            if item["word"] in seen_words:
                existing_count += 1
                continue
            seen_words.add(item["word"])
            deduped_cards.append(item)
        prepared_cards = deduped_cards
    
        # 整批只扫描一次个人空间；旧实现每生成一张卡就重复统计全部用户数据，
        # 卡片越多越慢。这里仍在写入前完成同样的 50 MB 配额校验。
        db_write_started = time.time()
        _require_storage_space(
            db,
            user.id,
            sum(
                _utf8_size(item["word"])
                + _utf8_size(item["front"])
                + _utf8_size(item["back"])
                + _utf8_size(item["context"])
                for item in prepared_cards
            ),
        )
    
        def _new_card(item: dict[str, str]) -> Card:
            return Card(
                user_id=user.id,
                word=item["word"],
                card_type=body.card_type,
                front=item["front"],
                back=item["back"],
                context=item["context"],
                state="new",
                due_at=None,
                reps=0,
                lapses=0,
                interval_days=0.0,
                ease=2.5,
            )
    
        created_now = len(prepared_cards)
        created_word_list = [item["word"] for item in prepared_cards]
        for item in prepared_cards:
            db.add(_new_card(item))
            _mark_saved_word_mid(db, user.id, item["word"])
        try:
            db.commit()
        except IntegrityError:
            # 长 AI 请求期间可能已由并发/重复提交建卡：回滚后逐张插入，已存在的跳过，
            # 不再返回 500（AI 额度已消耗，卡也照常入库）。
            db.rollback()
            created_now = 0
            created_word_list = []
            seen_again: set[str] = set()
            for start in range(0, len(prepared_cards), 500):
                chunk = prepared_cards[start : start + 500]
                rows = (
                    db.query(Card.word)
                    .filter(
                        Card.user_id == user.id,
                        Card.word.in_([item["word"] for item in chunk]),
                        Card.card_type == body.card_type,
                    )
                    .all()
                )
                existing_words_now = {row[0] for row in rows}
                for item in chunk:
                    if item["word"] in existing_words_now or item["word"] in seen_again:
                        existing_count += 1
                        existing_word_list.append(item["word"])
                        continue
                    seen_again.add(item["word"])
                    db.add(_new_card(item))
                    _mark_saved_word_mid(db, user.id, item["word"])
                    created_word_list.append(item["word"])
                    created_now += 1
            db.commit()
        total = db.query(Card).filter(Card.user_id == user.id).count()
        db_write_seconds = round(time.time() - db_write_started, 1)
        ai_error_summary = next(
            (str(message) for message in ai_errors.values() if str(message).strip()), ""
        )
        result = {
            "ok": True,
            "created": created_now,
            "created_words": created_word_list,
            "existing": existing_count,
            "existing_words": existing_word_list,
            "failed": failed,
            "error": ai_error_summary if created_now == 0 and failed else "",
            "ai_requests": ai_requests,
            "total": total,
            "timings": {
                "ai_wait_seconds": float(ai_timings.get("ai_wait_seconds", 0.0)),
                "format_retry_count": int(ai_timings.get("format_retry_count", 0)),
                "db_write_seconds": db_write_seconds,
            },
        }
        # 进度条目写最终结果：请求被代理/隧道切断时前端靠轮询拿到它。
        ai_mod.mark_card_generation_done(
            user.id,
            {
                "created": result["created"],
                "existing": result["existing"],
                "failed": result["failed"],
                "error": result["error"],
                "total": result["created"] + result["existing"] + len(result["failed"]),
            },
        )
    except HTTPException:
        ai_mod.mark_card_generation_done(user.id, {"error": "制卡失败，请稍后刷新查看"})
        raise
    except Exception:
        ai_mod.mark_card_generation_done(user.id, {"error": "制卡内部错误，请稍后刷新查看"})
        raise
    return result


# ---------- 卡片复习 ----------


@router.get("/cards/anki/export")
def export_cards_to_anki(request: Request, db: Session = Depends(get_db)):
    """导出当前用户全部卡片及学习进度为可由 Anki 导入的 .apkg。"""
    user = _require_user(db, request)
    try:
        package, count = anki_exchange.export_apkg(db, user.id)
        db.commit()  # 仅保存首次生成的稳定 Anki guid，不改学习状态。
    except anki_exchange.AnkiExchangeError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    filename = f"vocabflow-{dt.datetime.now().strftime('%Y%m%d')}-{count}.apkg"
    return Response(
        content=package,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-VocabFlow-Card-Count": str(count),
        },
    )


@router.post("/cards/anki/import")
async def import_cards_from_anki(
    request: Request,
    filename: str,
    db: Session = Depends(get_db),
):
    """事务性合并 Anki 包；不删除卡片，较新的站内进度不会被覆盖。"""
    user = _require_user(db, request)
    if not filename.lower().endswith(".apkg"):
        raise HTTPException(status_code=400, detail="请选择 .apkg 文件")
    if not check_request_rate(
        db,
        action="anki-import",
        identity=f"u{user.id}",
        limit=20,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="Anki 导入过于频繁，请稍后再试")
    data = await _read_limited_body(request, config.MAX_APKG_UPLOAD_BYTES)
    data, filename = _decode_upload_body(request, data, filename)
    if not _try_heavy_import_slot():
        raise HTTPException(status_code=429, detail="服务器导入任务繁忙，请稍后重试")
    try:
        parsed = await run_in_threadpool(
            anki_exchange.parse_apkg, data, config.MAX_APKG_CARDS
        )
        estimated_bytes = sum(
            _utf8_size(str(item.get(key, "")))
            for item in parsed.get("cards", [])
            for key in ("word", "front", "back", "context")
        ) + int(parsed.get("review_count", 0) or 0) * 112
        _require_storage_space(db, user.id, estimated_bytes)
        result = anki_exchange.import_parsed(db, user.id, parsed)
        db.commit()
        return {"ok": True, **result}
    except anki_exchange.AnkiExchangeError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Anki 卡片与现有数据冲突，未导入任何内容",
        ) from exc
    finally:
        _release_heavy_import_slot()


@router.get("/cards/settings")
def get_review_settings(request: Request, db: Session = Depends(get_db)):
    user = _require_user(db, request)
    preference = _review_preference(db, user)
    return {"new_cards_per_day": preference.new_cards_per_day}


@router.put("/cards/settings")
def update_review_settings(
    body: ReviewSettingsIn, request: Request, db: Session = Depends(get_db)
):
    user = _require_user(db, request)
    preference = _review_preference(db, user)
    preference.new_cards_per_day = body.new_cards_per_day
    preference.updated_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"ok": True, "new_cards_per_day": preference.new_cards_per_day}


@router.get("/cards/browse")
def browse_cards(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    state: str = "all",
    card_type: str = "all",
    sort: str = "time",
    limit: int = 50,
    offset: int = 0,
    ids_only: bool = False,
):
    user = _require_user(db, request)
    if _sentence_refresh_due(db, user.id):
        card_builder.refresh_sentence_cards(db, user.id)
    # ids_only 用于“全选所有卡片”：一次最多取 2000 个 id（批量删除上限）。
    limit = min(2000 if ids_only else 100, max(1, limit))
    offset = max(0, offset)
    query = db.query(Card).filter(Card.user_id == user.id)
    if state != "all":
        if state == "buried":
            query = query.filter(Card.buried.is_(True))
        elif state in {"new", "scheduled"}:
            query = query.filter(
                Card.due_at.is_(None) if state == "new" else Card.due_at.is_not(None)
            )
        else:
            raise HTTPException(status_code=400, detail="无效卡片状态")
    if card_type != "all":
        if card_type not in ALLOWED_CARD_TYPES:
            raise HTTPException(status_code=400, detail="无效卡片类型")
        query = query.filter(Card.card_type == card_type)
    term = q.strip()
    if term:
        like = f"%{term}%"
        query = query.filter(
            or_(Card.word.ilike(like), Card.front.ilike(like), Card.back.ilike(like))
        )
    total = query.count()
    if sort == "ngsl":
        # NGSL 排序必须是全局的：先取全部匹配卡片的 (id, word) 按排名升序
        # （小的排前面，不在词表的排最后），再切片当前页；否则只在本页内
        # 排序会导致翻页后排名乱序。
        def _ngsl_key(row: tuple[int, str]) -> tuple:
            rank = vocab.rank_of(str(row[1] or "").split(" [", 1)[0])
            return (rank is None, rank or 999_999, str(row[1] or "").lower(), row[0])

        items = query.with_entities(Card.id, Card.word).all()
        items.sort(key=_ngsl_key)
        page_ids = [row[0] for row in items[offset : offset + limit]]
        if ids_only:
            return {"ids": page_ids, "total": total, "limit": limit, "offset": offset}
        if page_ids:
            by_id = {
                card.id: card
                for card in db.query(Card).filter(Card.id.in_(page_ids)).all()
            }
            rows = [by_id[i] for i in page_ids if i in by_id]
        else:
            rows = []
        ranks = {
            card.id: vocab.rank_of(str(card.word or "").split(" [", 1)[0])
            for card in rows
        }
        return {
            "cards": [
                {**_card_dict(card), "ngsl_rank": ranks[card.id]} for card in rows
            ],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    if sort == "alpha":
        sort_key = (Card.word.asc(), Card.id.asc())
    else:
        sort_key = (Card.created_at.desc(), Card.id.desc())
    if ids_only:
        ids = [
            row[0]
            for row in query.with_entities(Card.id)
            .order_by(*sort_key)
            .offset(offset)
            .limit(limit)
            .all()
        ]
        return {"ids": ids, "total": total, "limit": limit, "offset": offset}
    rows = query.order_by(*sort_key).offset(offset).limit(limit).all()
    return {
        "cards": [_card_dict(card) for card in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _scoped_action_key(user_id: int, action_id: str) -> str:
    """把客户端的评分幂等键按用户隔离：不同用户的相同键不再冲突，
    也无法探测其他用户是否用过某个键。"""
    return hashlib.sha256(f"{user_id}:{action_id}".encode()).hexdigest()


def _review_log_bytes(rating: str, previous_state: str, previous_word_status: str) -> int:
    """估算一条复习记录占用的存储字节数（字段 + 行开销）。"""
    return (
        _utf8_size(rating)
        + _utf8_size(previous_state)
        + _utf8_size(previous_word_status)
        + 96
    )


@router.post("/cards/{card_id}/bury")
def bury_card(card_id: int, request: Request, db: Session = Depends(get_db)):
    """掩埋卡片：今天起不再进入学习队列；可在卡片浏览器里恢复。"""
    user = _require_user(db, request)
    card = (
        db.query(Card)
        .filter(Card.id == card_id, Card.user_id == user.id)
        .first()
    )
    if not card:
        raise HTTPException(status_code=404, detail="卡片不存在")
    card.buried = True
    db.commit()
    return {"ok": True, "card": _card_dict(card)}


@router.post("/cards/{card_id}/unbury")
def unbury_card(card_id: int, request: Request, db: Session = Depends(get_db)):
    """恢复被掩埋的卡片，重新进入学习队列。"""
    user = _require_user(db, request)
    card = (
        db.query(Card)
        .filter(Card.id == card_id, Card.user_id == user.id)
        .first()
    )
    if not card:
        raise HTTPException(status_code=404, detail="卡片不存在")
    card.buried = False
    db.commit()
    return {"ok": True, "card": _card_dict(card)}


@router.delete("/cards/{card_id}")
def delete_card(card_id: int, request: Request, db: Session = Depends(get_db)):
    """删除单张学习卡片及其复习记录。"""
    user = _require_user(db, request)
    card = (
        db.query(Card)
        .filter(Card.id == card_id, Card.user_id == user.id)
        .first()
    )
    if not card:
        raise HTTPException(status_code=404, detail="卡片不存在")
    word = card.word
    db.query(ReviewLog).filter(
        ReviewLog.user_id == user.id, ReviewLog.card_id == card.id
    ).delete(synchronize_session=False)
    db.query(ReviewRequest).filter(
        ReviewRequest.user_id == user.id, ReviewRequest.card_id == card.id
    ).delete(synchronize_session=False)
    db.query(DailyNewAssignment).filter(
        DailyNewAssignment.user_id == user.id,
        DailyNewAssignment.card_id == card.id,
    ).delete(synchronize_session=False)
    db.delete(card)
    db.flush()
    # 删卡后该词已无任何单词卡：mid（已制卡）派生状态失效，恢复为 hard。
    if card.card_type != "speaking":
        still_has_card = (
            db.query(Card.id)
            .filter(
                Card.user_id == user.id,
                Card.word == word,
                Card.card_type != "speaking",
            )
            .first()
        )
        if not still_has_card:
            saved_row = (
                db.query(SavedWord)
                .filter(SavedWord.user_id == user.id, SavedWord.word == word)
                .first()
            )
            if saved_row and saved_row.status == "mid":
                saved_row.status = "hard"
                saved_row.updated_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"ok": True, "word": word, "card_type": card.card_type}


@router.post("/cards/unbury-batch")
def unbury_cards_batch(
    body: CardsBatchDeleteIn, request: Request, db: Session = Depends(get_db)
):
    """批量恢复“不想学”的卡片，重新进入学习队列。"""
    user = _require_user(db, request)
    card_ids = sorted(set(body.card_ids))
    restored = 0
    if card_ids:
        restored = (
            db.query(Card)
            .filter(
                Card.user_id == user.id,
                Card.id.in_(card_ids),
                Card.buried.is_(True),
            )
            .update({"buried": False}, synchronize_session=False)
        )
        db.commit()
    return {"ok": True, "restored": int(restored)}


@router.post("/cards/delete-batch")
def delete_cards_batch(
    body: CardsBatchDeleteIn, request: Request, db: Session = Depends(get_db)
):
    """按卡片 ID 或目标词批量删除卡片。"""
    user = _require_user(db, request)
    deleted = 0

    card_ids = sorted(set(body.card_ids))
    if card_ids:
        cards = (
            db.query(Card)
            .filter(Card.user_id == user.id, Card.id.in_(card_ids))
            .all()
        )
        if cards:
            ids = [card.id for card in cards]
            db.query(ReviewLog).filter(
                ReviewLog.user_id == user.id, ReviewLog.card_id.in_(ids)
            ).delete(synchronize_session=False)
            db.query(ReviewRequest).filter(
                ReviewRequest.user_id == user.id, ReviewRequest.card_id.in_(ids)
            ).delete(synchronize_session=False)
            db.query(DailyNewAssignment).filter(
                DailyNewAssignment.user_id == user.id,
                DailyNewAssignment.card_id.in_(ids),
            ).delete(synchronize_session=False)
            deleted += (
                db.query(Card)
                .filter(Card.user_id == user.id, Card.id.in_(ids))
                .delete(synchronize_session=False)
            )

    words: list[str] = []
    seen: set[str] = set()
    for raw in body.words:
        word = vocab.user_word_identity(str(raw or "").strip())
        if word and word not in seen:
            seen.add(word)
            words.append(word)
    if words:
        word_set = set(words)
        matched_by_word: dict[str, list[int]] = {word: [] for word in words}
        user_card_rows = (
            db.query(Card.id, Card.word)
            .filter(Card.user_id == user.id)
            .all()
        )
        for card_id, card_word in user_card_rows:
            card_word = str(card_word or "")
            base = card_word.split(" [", 1)[0]
            norm = vocab.user_word_identity(base)
            for key in (card_word, base, norm):
                if key in word_set:
                    matched_by_word[key].append(card_id)
                    break
        deleted_word_ids: set[int] = set()
        for word in words:
            word_card_ids = [
                item
                for item in matched_by_word.get(word, [])
                if item not in deleted_word_ids
            ]
            if not word_card_ids:
                continue
            deleted_word_ids.update(word_card_ids)
            db.query(ReviewLog).filter(
                ReviewLog.user_id == user.id,
                ReviewLog.card_id.in_(word_card_ids),
            ).delete(synchronize_session=False)
            db.query(ReviewRequest).filter(
                ReviewRequest.user_id == user.id,
                ReviewRequest.card_id.in_(word_card_ids),
            ).delete(synchronize_session=False)
            db.query(DailyNewAssignment).filter(
                DailyNewAssignment.user_id == user.id,
                DailyNewAssignment.card_id.in_(word_card_ids),
            ).delete(synchronize_session=False)
            deleted += (
                db.query(Card)
                .filter(Card.user_id == user.id, Card.id.in_(word_card_ids))
                .delete(synchronize_session=False)
            )

    if not card_ids and not words:
        raise HTTPException(status_code=400, detail="没有可删除的单词")

    # 删卡后该词已无任何单词卡：mid（已制卡）派生状态失效，恢复为 hard。
    for word in words:
        if db.query(Card.id).filter(
            Card.user_id == user.id,
            Card.word == word,
            Card.card_type != "speaking",
        ).first():
            continue
        row = (
            db.query(SavedWord)
            .filter(SavedWord.user_id == user.id, SavedWord.word == word)
            .first()
        )
        if row and row.status == "mid":
            row.status = "hard"
            row.updated_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if card_ids:
        remaining_words = {
            str(row[0] or "").split(" [", 1)[0]
            for row in db.query(Card.word)
            .filter(
                Card.user_id == user.id,
                Card.card_type != "speaking",
            )
            .all()
        }
        for row in (
            db.query(SavedWord)
            .filter(
                SavedWord.user_id == user.id,
                SavedWord.status == "mid",
                SavedWord.word.notin_(remaining_words),
            )
            .all()
        ):
            row.status = "hard"
            row.updated_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    db.commit()
    return {"ok": True, "deleted": deleted, "words": len(words)}


@router.get("/cards")
def daily_cards(
    request: Request,
    db: Session = Depends(get_db),
    card_type: str = "all",
    extra_new: int = 0,
    practice_limit: int = 0,
):
    user = _require_user(db, request)
    if practice_limit:
        raise HTTPException(status_code=410, detail="不能提前复习未到期卡片")
    preference = _review_preference(db, user)
    if _sentence_refresh_due(db, user.id):
        card_builder.refresh_sentence_cards(db, user.id)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    today_stats = _today_stats(db, user.id, now)
    can_undo = (
        db.query(ReviewLog.id)
        .filter(
            ReviewLog.user_id == user.id,
            ReviewLog.reviewed_at >= now - dt.timedelta(minutes=15),
        )
        .first()
        is not None
    )
    # 掩埋的卡不计入任何学习队列，也不参与每日分配。
    global_base = db.query(Card).filter(
        Card.user_id == user.id, Card.buried.is_(False)
    )
    base = global_base
    if card_type != "all":
        if card_type not in ALLOWED_CARD_TYPES:
            raise HTTPException(status_code=400, detail="无效卡片类型")
        base = base.filter(Card.card_type == card_type)

    learning_day, _start_of_day, end_of_day = _learning_day(now)
    # 复习卡按日期进入当天任务；学习步骤中的卡必须等到精确到期时间，
    # 否则未来的卡片会进队列却在评分时被 409 拒绝。
    due_now_clause = Card.due_at <= now
    regular_due_query = base.filter(
        Card.due_at.is_not(None),
        Card.due_at < end_of_day,
        due_now_clause,
        Card.state != "learning",
    )
    due = (
        regular_due_query
        .order_by(Card.due_at, Card.id)
        .all()
    )

    global_regular_due = global_base.filter(
        Card.due_at.is_not(None),
        Card.due_at < end_of_day,
        due_now_clause,
        Card.state != "learning",
    )
    regular_due_total = global_regular_due.count()
    # FSRS 学习/重学步骤为 0 秒，所有卡片严格按到期时间进出队列：
    # 未到期的卡片（包括选“重来”后明天才复习的卡）不进入当前队列。
    pending_cards = (
        db.query(Card)
        .filter(
            Card.user_id == user.id,
            Card.state == "learning",
            Card.buried.is_(False),
            Card.due_at <= now,
        )
        .order_by(Card.due_at, Card.id)
        .all()
    )
    if card_type != "all":
        pending_cards = [
            card for card in pending_cards if card.card_type == card_type
        ]
    repeat_pending_total = len(pending_cards)

    assignment_ids = _ensure_daily_new_assignments(
        db, user, learning_day, preference.new_cards_per_day
    )
    extra_assignment_ids = [
        row.card_id
        for row in db.query(DailyNewAssignment).filter(
            DailyNewAssignment.user_id == user.id,
            DailyNewAssignment.day == learning_day,
            DailyNewAssignment.is_extra.is_(True),
        ).all()
    ]
    all_new_assignment_ids = list(
        dict.fromkeys(assignment_ids + extra_assignment_ids)
    )
    assigned_new_query = global_base.filter(
        Card.id.in_(all_new_assignment_ids), Card.due_at.is_(None)
    ) if all_new_assignment_ids else None
    assigned_new_cards = (
        assigned_new_query.order_by(Card.id).all() if all_new_assignment_ids else []
    )
    required_new_remaining = len(assigned_new_cards)
    can_extra_new = (
        regular_due_total == 0
        and required_new_remaining == 0
        and repeat_pending_total == 0
    )
    is_extra_request = extra_new > 0
    if is_extra_request and not can_extra_new:
        raise HTTPException(
            status_code=409,
            detail="完成今日复习和新学任务后，才能继续学习更多新卡",
        )
    if is_extra_request:
        extra_query = global_base.filter(Card.due_at.is_(None))
        if all_new_assignment_ids:
            extra_query = extra_query.filter(
                Card.id.notin_(all_new_assignment_ids)
            )
        extra_available_all = extra_query.count()
        if card_type != "all":
            extra_query = extra_query.filter(Card.card_type == card_type)
        extra_available = extra_query.count()
        new_cards = (
            extra_query.order_by(func.random())
            .limit(min(100, max(0, extra_new)))
            .all()
        )
        if new_cards:
            db.add_all(
                [
                    DailyNewAssignment(
                        user_id=user.id,
                        day=learning_day,
                        card_id=card.id,
                        is_extra=True,
                    )
                    for card in new_cards
                ]
            )
            try:
                db.commit()
            except IntegrityError:
                # 另一标签页已加学同一批卡时，保留已存在的分配，重复返回不影响队列。
                db.rollback()
    else:
        extra_available = 0
        extra_available_all = 0
        new_cards = assigned_new_cards
    if card_type != "all":
        new_cards = [card for card in new_cards if card.card_type == card_type]
    due_items = [{**_card_dict(card), "queue_kind": "due"} for card in due]
    new_items = [{**_card_dict(card), "queue_kind": "new"} for card in new_cards]
    repeat_items = [
        {
            **_card_dict(card, session_repeat=True, session_correct_streak=0),
            "queue_kind": "again",
        }
        for card in pending_cards
    ]
    # 队列固定顺序：重学卡 → 到期复习 → 今日新学；
    # 旧卡没学完之前不会先出现新卡，刷新也不乱序。
    unified_queue = [*repeat_items, *due_items, *new_items]
    return {
        "queue": unified_queue,
        "total_cards": db.query(Card).filter(Card.user_id == user.id).count(),
        "due": [_card_dict(c) for c in due],
        "new": [_card_dict(c) for c in new_cards],
        "again": [item for item in repeat_items],
        "practice": [],
        "remaining_counts": {
            "due": regular_due_total,
            "new": required_new_remaining,
            "again": repeat_pending_total,
        },
        "due_total": regular_due_total,
        "hard_pending_total": repeat_pending_total,
        "again_pending_total": repeat_pending_total,
        "repeat_pending_total": repeat_pending_total,
        "again_next_due_at": None,
        "new_remaining": required_new_remaining,
        "new_cards_per_day": preference.new_cards_per_day,
        "can_extra_new": can_extra_new,
        "extra_new": is_extra_request,
        "extra_available": extra_available,
        "extra_available_all": extra_available_all,
        "learning_day": learning_day,
        "today_stats": today_stats,
        "can_undo": can_undo,
    }


@router.post("/cards/reviews/undo")
def undo_last_review(request: Request, db: Session = Depends(get_db)):
    """撤回当前用户最近一次真实评分，并恢复完整调度状态。"""
    user = _require_user(db, request)
    log = (
        db.query(ReviewLog)
        .filter(ReviewLog.user_id == user.id)
        .order_by(ReviewLog.reviewed_at.desc(), ReviewLog.id.desc())
        .first()
    )
    if not log:
        raise HTTPException(status_code=404, detail="没有可以撤回的评分")
    card = (
        db.query(Card)
        .filter(Card.id == log.card_id, Card.user_id == user.id)
        .first()
    )
    if not card:
        raise HTTPException(status_code=404, detail="原卡片已不存在")
    card.state = log.previous_state or ("new" if log.is_new else "review")
    card.due_at = log.previous_due_at
    card.interval_days = float(log.interval_days or 0)
    card.ease = float(log.ease or 2.5)
    card.learning_step = int(log.previous_learning_step or 0)
    card.fsrs_state = log.previous_fsrs_state
    card.session_reduce_day = ""
    card.session_reduce_used = 0
    card.reps = int(log.previous_reps or 0)
    card.lapses = int(log.previous_lapses or 0)
    # 快照 previous_* 是评分前的完整状态（已含同会话上一次评分的效果），
    # 直接恢复即可；绝不能把 previous_session_rating 再 apply 一遍，否则
    # reps/lapses 会被重复计数。恢复后若卡片在学习步骤中（state=learning），
    # 重新进入会话重学队列，由前端按 session_repeat 展示。
    db.delete(log)
    db.commit()
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    can_undo = (
        db.query(ReviewLog.id)
        .filter(
            ReviewLog.user_id == user.id,
            ReviewLog.reviewed_at >= now - dt.timedelta(minutes=15),
        )
        .first()
        is not None
    )
    return {
        "ok": True,
        "can_undo": can_undo,
        "card": _card_dict(
            card,
            session_repeat=card.state == "learning",
            session_correct_streak=0,
        ),
    }


@router.post("/cards/{card_id}/review")
def review_card(
    card_id: int, body: ReviewIn, request: Request, db: Session = Depends(get_db)
):
    user = _require_user(db, request)
    busy_response = _reserve_review_write(db, user.id, "single-rate-limit")
    if busy_response:
        return busy_response
    if not check_request_rate(
        db,
        action="review-card",
        identity=f"u{user.id}",
        limit=5000,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="评分请求过多，请稍后再试")
    busy_response = _reserve_review_write(db, user.id, "single-review")
    if busy_response:
        return busy_response
    card = (
        db.query(Card)
        .filter(Card.id == card_id, Card.user_id == user.id)
        .with_for_update()
        .first()
    )
    if not card:
        raise HTTPException(status_code=404, detail="卡片不存在")
    rating = body.rating.strip().lower()
    if rating not in srs.RATINGS:
        raise HTTPException(status_code=400, detail="无效评分")
    if body.practice:
        raise HTTPException(status_code=410, detail="不能提前复习未到期卡片")
    action_id = body.action_id.strip()
    if action_id and body.expected_revision is None:
        raise HTTPException(status_code=400, detail="评分请求缺少卡片版本，请刷新重试")
    action_key = _scoped_action_key(user.id, action_id) if action_id else ""
    review_request = None
    if action_id:
        existing_request = db.get(ReviewRequest, action_key)
        if existing_request:
            if existing_request.user_id != user.id or existing_request.card_id != card.id:
                raise HTTPException(status_code=409, detail="评分动作标识冲突")
            (
                db.get(ReviewLog, existing_request.review_log_id)
                if existing_request.review_log_id
                else None
            )
            return {
                "ok": True,
                "idempotent": True,
                "card": _card_dict(
                    card,
                    session_repeat=card.state == "learning",
                    session_correct_streak=0,
                ),
                "today_stats": _today_stats(db, user.id, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)),
            }
        review_request = ReviewRequest(
            action_id=action_key,
            user_id=user.id,
            card_id=card.id,
        )
        db.add(review_request)
        try:
            db.flush()
        except IntegrityError:
            # 并发的网络重试可能同时通过前面的查询；唯一键只允许一个成功。
            db.rollback()
            existing_request = db.get(ReviewRequest, action_key)
            card = db.query(Card).filter(
                Card.id == card_id, Card.user_id == user.id
            ).first()
            if not existing_request or not card:
                raise HTTPException(status_code=409, detail="评分动作冲突，请刷新重试") from None
            (
                db.get(ReviewLog, existing_request.review_log_id)
                if existing_request.review_log_id
                else None
            )
            return {
                "ok": True,
                "idempotent": True,
                "card": _card_dict(
                    card,
                    session_repeat=card.state == "learning",
                    session_correct_streak=0,
                ),
                "today_stats": _today_stats(db, user.id, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)),
            }
    if body.expected_revision is not None:
        claimed = (
            db.query(Card)
            .filter(
                Card.id == card.id,
                Card.user_id == user.id,
                Card.revision == body.expected_revision,
            )
            .update(
                {"revision": Card.revision + 1},
                synchronize_session=False,
            )
        )
        if claimed != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail="卡片已在其他页面评分，请刷新")
        db.refresh(card)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if card.due_at and card.due_at > now:
        raise HTTPException(status_code=409, detail="这张卡片尚未到期，不能提前复习")
    was_new = card.due_at is None
    old_interval = card.interval_days
    old_ease = card.ease
    previous_state = card.state
    previous_due_at = card.due_at
    previous_reps = card.reps
    previous_lapses = card.lapses
    previous_learning_step = int(card.learning_step or 0)
    previous_fsrs_state = card.fsrs_state
    card, fsrs_log_json = srs.apply_rating_with_log(card, rating, now=now)
    session_pending = card.state == "learning"
    session_correct_streak = 0
    _require_storage_space(
        db,
        user.id,
        _review_log_bytes(rating, previous_state, ""),
    )
    review_log = ReviewLog(
        user_id=user.id,
        card_id=card.id,
        rating=rating,
        is_new=was_new,
        interval_days=old_interval,
        ease=old_ease,
        previous_state=previous_state,
        previous_due_at=previous_due_at,
        previous_reps=previous_reps,
        previous_lapses=previous_lapses,
        previous_word_status="",
        session_pending=session_pending,
        session_correct_streak=session_correct_streak,
        previous_session_pending=previous_learning_step > 0,
        previous_session_correct_streak=max(0, previous_learning_step - 1),
        previous_session_rating="again" if previous_learning_step > 0 else "",
        previous_learning_step=previous_learning_step,
        previous_fsrs_state=previous_fsrs_state,
        fsrs_review_log=fsrs_log_json,
    )
    db.add(review_log)
    if review_request:
        db.flush()
        review_request.review_log_id = review_log.id
    db.commit()
    return {
        "ok": True,
        "card": _card_dict(
            card,
            session_repeat=session_pending,
            session_correct_streak=session_correct_streak,
        ),
        "today_stats": _today_stats(db, user.id, now),
    }


@router.post("/cards/reviews/batch")
def review_cards_batch(
    body: ReviewBatchIn, request: Request, db: Session = Depends(get_db)
):
    """一次提交整段学习会话的评分；单条失败只回滚该条，不丢失其他评分。"""
    user = _require_user(db, request)
    busy_response = _reserve_review_write(db, user.id, "batch-rate-limit")
    if busy_response:
        return busy_response
    if not check_request_rate(
        db,
        action="review-batch",
        identity=f"u{user.id}",
        limit=10000,
        window_minutes=60,
        need=len(body.ratings),
    ):
        raise HTTPException(status_code=429, detail="评分请求过多，请稍后再试")
    busy_response = _reserve_review_write(db, user.id, "batch-review")
    if busy_response:
        return busy_response
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    results: list[dict] = []
    errors: list[dict] = []
    for item in body.ratings:
        action_id = item.action_id.strip()
        try:
            with db.begin_nested():
                card = (
                    db.query(Card)
                    .filter(Card.id == item.card_id, Card.user_id == user.id)
                    .with_for_update()
                    .first()
                )
                if not card:
                    raise HTTPException(status_code=404, detail="卡片不存在")
                rating = item.rating.strip().lower()
                if rating not in srs.RATINGS:
                    raise HTTPException(status_code=400, detail="无效评分")
                if action_id:
                    if item.expected_revision is None:
                        raise HTTPException(
                            status_code=400,
                            detail="评分请求缺少卡片版本，请刷新重试",
                        )
                    action_key = _scoped_action_key(user.id, action_id)
                    existing_request = db.get(ReviewRequest, action_key)
                    if existing_request:
                        if (
                            existing_request.user_id != user.id
                            or existing_request.card_id != card.id
                        ):
                            raise HTTPException(
                                status_code=409, detail="评分动作标识冲突"
                            )
                        (
                            db.get(ReviewLog, existing_request.review_log_id)
                            if existing_request.review_log_id
                            else None
                        )
                        results.append(
                            {
                                "ok": True,
                                "idempotent": True,
                                "card": _card_dict(
                                    card,
                                    session_repeat=card.state == "learning",
                                    session_correct_streak=0,
                                ),
                            }
                        )
                        continue
                if item.expected_revision is not None:
                    claimed = (
                        db.query(Card)
                        .filter(
                            Card.id == card.id,
                            Card.user_id == user.id,
                            Card.revision == item.expected_revision,
                        )
                        .update(
                            {"revision": Card.revision + 1},
                            synchronize_session=False,
                        )
                    )
                    if claimed != 1:
                        raise HTTPException(
                            status_code=409,
                            detail="卡片已在其他页面评分，请刷新",
                        )
                    db.refresh(card)
                if card.due_at and card.due_at > now:
                    raise HTTPException(
                        status_code=409, detail="这张卡片尚未到期，不能提前复习"
                    )
                was_new = card.due_at is None
                old_interval = card.interval_days
                old_ease = card.ease
                previous_state = card.state
                previous_due_at = card.due_at
                previous_reps = card.reps
                previous_lapses = card.lapses
                previous_learning_step = int(card.learning_step or 0)
                previous_fsrs_state = card.fsrs_state
                card, fsrs_log_json = srs.apply_rating_with_log(
                    card, rating, now=now
                )
                session_pending = card.state == "learning"
                session_correct_streak = 0
                _require_storage_space(
                    db,
                    user.id,
                    _review_log_bytes(rating, previous_state, ""),
                )
                review_log = ReviewLog(
                    user_id=user.id,
                    card_id=card.id,
                    rating=rating,
                    is_new=was_new,
                    interval_days=old_interval,
                    ease=old_ease,
                    previous_state=previous_state,
                    previous_due_at=previous_due_at,
                    previous_reps=previous_reps,
                    previous_lapses=previous_lapses,
                    previous_word_status="",
                    session_pending=session_pending,
                    session_correct_streak=session_correct_streak,
                    previous_session_pending=previous_learning_step > 0,
                    previous_session_correct_streak=max(0, previous_learning_step - 1),
                    previous_session_rating="again" if previous_learning_step > 0 else "",
                    previous_learning_step=previous_learning_step,
                    previous_fsrs_state=previous_fsrs_state,
                    fsrs_review_log=fsrs_log_json,
                )
                db.add(review_log)
                review_request = None
                if action_id:
                    review_request = ReviewRequest(
                        action_id=_scoped_action_key(user.id, action_id),
                        user_id=user.id,
                        card_id=card.id,
                    )
                    db.add(review_request)
                db.flush()
                if review_request:
                    review_request.review_log_id = review_log.id
                results.append(
                    {
                        "ok": True,
                        "card": _card_dict(
                            card,
                            session_repeat=session_pending,
                            session_correct_streak=session_correct_streak,
                        ),
                    }
                )
        except IntegrityError:
            # 并发的网络重试可能同时插入同一 action_id；唯一键只允许一个成功。
            # 外层 savepoint 已回滚，这里改读对方已提交的幂等记录，不丢整批。
            existing_request = None
            if action_id:
                action_key = _scoped_action_key(user.id, action_id)
                existing_request = db.get(ReviewRequest, action_key)
            card = (
                db.query(Card)
                .filter(Card.id == item.card_id, Card.user_id == user.id)
                .first()
            )
            if not existing_request or not card:
                errors.append(
                    {
                        "card_id": item.card_id,
                        "status": 409,
                        "detail": "评分动作冲突，请刷新重试",
                    }
                )
                continue
            if (
                existing_request.user_id != user.id
                or existing_request.card_id != card.id
            ):
                errors.append(
                    {
                        "card_id": item.card_id,
                        "status": 409,
                        "detail": "评分动作标识冲突",
                    }
                )
                continue
            results.append(
                {
                    "ok": True,
                    "idempotent": True,
                    "card": _card_dict(
                        card,
                        session_repeat=card.state == "learning",
                        session_correct_streak=0,
                    ),
                }
            )
        except HTTPException as exc:
            errors.append(
                {
                    "card_id": item.card_id,
                    "status": exc.status_code,
                    "detail": exc.detail,
                }
            )
    db.commit()
    return {
        "ok": True,
        "cards": results,
        "errors": errors,
        "today_stats": _today_stats(db, user.id, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)),
    }


def _save_generated_articles(
    db: Session, user: User, day: str, generated: list[dict]
) -> tuple[list[dict], Corpus, str]:
    """在所有章节均生成成功后，原子替换当天旧 AI 文章。"""
    chapters = []
    book_text_parts = []
    for result in generated:
        paragraphs = result.get("paragraphs") or []
        plain_paragraphs = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", p))).strip()
            for p in paragraphs
        ]
        plain_paragraphs = [paragraph for paragraph in plain_paragraphs if paragraph]
        chapter_title = str(result.get("title") or "").strip() or "今日文章"
        chapter_text = "\n\n".join(plain_paragraphs)
        chapters.append({"title": chapter_title, "text": chapter_text})
        book_text_parts.append(chapter_text)

    month, day_of_month = day.split("-")[1], day.split("-")[2]
    book_title = f"{int(month)}.{int(day_of_month)}-{chapters[0]['title'].lower()}"
    _delete_previous_ai_articles(db, user.id)
    corpus = _create_corpus(
        db,
        user,
        book_title,
        "\n\n".join(book_text_parts),
        source_type="ai",
        chapters=chapters,
    )
    for result in generated:
        for word in {*result.get("new_words", []), *result.get("review_words", [])}:
            if not word:
                continue
            row = (
                db.query(CorpusWord)
                .filter(CorpusWord.corpus_id == corpus.id, CorpusWord.word == word)
                .first()
            )
            if row:
                row.is_target = True
            else:
                db.add(
                    CorpusWord(
                        corpus_id=corpus.id,
                        word=word,
                        count=0,
                        is_target=True,
                    )
                )
    db.commit()

    articles = []
    for result in generated:
        seen_targets = set()
        target_words = []
        for word in [*result.get("new_words", []), *result.get("review_words", [])]:
            key = vocab.user_word_identity(word) if word else ""
            if word and key not in seen_targets:
                seen_targets.add(key)
                target_words.append(word)
        articles.append(
            {
                "title": result.get("title") or "",
                "paragraphs": result.get("paragraphs") or [],
                "word_count": result.get("word_count") or 0,
                "new_words": result.get("new_words") or [],
                "review_words": result.get("review_words") or [],
                "target_words": target_words,
                "target_count": len(target_words),
            }
        )
    return articles, corpus, book_title


def _article_word_groups(words: list[str], maximum: int = 12) -> list[list[str]]:
    """均匀拆分今日新词，避免最后一篇只剩极少目标词。"""
    cleaned = [str(word).strip() for word in words if str(word).strip()]
    if not cleaned:
        return []
    group_count = max(1, math.ceil(len(cleaned) / max(1, maximum)))
    base_size, larger_groups = divmod(len(cleaned), group_count)
    groups: list[list[str]] = []
    offset = 0
    for index in range(group_count):
        size = base_size + (1 if index < larger_groups else 0)
        groups.append(cleaned[offset : offset + size])
        offset += size
    return groups


def _generate_study_articles_in_background(
    user_id: int,
    day: str,
    word_groups: list[list[str]],
) -> None:
    """在后台逐篇生成今天新学词的今日短文；全部成功后再原子保存。"""
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None:
            raise RuntimeError("登录已失效，请刷新后重试")
        generated: list[dict] = []
        total = len(word_groups)
        for index, words in enumerate(word_groups, start=1):
            _update_article_generation(
                user_id,
                completed=index - 1,
                detail=f"AI 正在生成第 {index}/{total} 篇…",
            )
            result, error = ai_mod.generate_article(
                db, user_id, words, [], thinking=True, effort="max"
            )
            if error:
                raise RuntimeError(f"第 {index} 篇生成失败：{error}")
            generated.append(result)
            _update_article_generation(
                user_id,
                completed=index,
                detail=f"已完成 {index}/{total} 篇",
            )
        _save_generated_articles(db, user, day, generated)
        _finish_article_generation(user_id)
        logger.info(
            "AI article background job completed: user_id=%s chapters=%s",
            user_id,
            len(generated),
        )
    except Exception as exc:
        db.rollback()
        message = str(exc) or "文章生成失败，请重试"
        logger.warning(
            "AI article background job failed: user_id=%s error=%s",
            user_id,
            message,
        )
        _finish_article_generation(user_id, error=message)
    finally:
        db.close()


@router.post("/cards/article")
def generate_study_article(
    body: ArticleIn,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """按所选范围生成今日短文；长任务在响应后后台执行。

    可选择今天新学的单词，或今天所有点过“不认识”的单词。目标词
    全部覆盖并均匀拆分为每篇最多 12 个，使用 DeepSeek 思考模式 `max`。

    单词来源 = 今天 ReviewLog 中的卡片；新学 = 今天首次学习的卡片
    （is_new=True，无论评分通过与否）；不认识 = 今天至少有一条
    rating=again 的卡片，包含新卡和复习卡。同一卡片自动去重。
    """
    user = _require_user(db, request)
    if body.source not in {"new", "again"}:
        raise HTTPException(status_code=400, detail="无效单词范围")
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    _day, start_of_day, end_of_day = _learning_day(now)

    today_logs = (
        db.query(ReviewLog.card_id, ReviewLog.is_new, ReviewLog.rating)
        .filter(
            ReviewLog.user_id == user.id,
            ReviewLog.reviewed_at >= start_of_day,
            ReviewLog.reviewed_at < end_of_day,
        )
        .all()
    )
    new_card_ids = {
        card_id for card_id, is_new, _rating in today_logs if is_new
    }
    again_card_ids = {
        card_id for card_id, _is_new, rating in today_logs if rating == "again"
    }
    def _card_words(card_ids: set[int]) -> list[str]:
        """按卡片取单词并去重；Anki 卡去掉词头后的 [提示] 后缀。"""
        words: list[str] = []
        seen: set[str] = set()
        if not card_ids:
            return words
        rows = (
            db.query(Card.word)
            .filter(Card.user_id == user.id, Card.id.in_(card_ids))
            .all()
        )
        for (raw_word,) in rows:
            word = str(raw_word or "").split(" [", 1)[0].strip()
            # 制卡入库时已去掉括号标注；这里再兜底一次，兼容旧数据。
            word = ai_mod._split_card_entry(word)[0].strip()
            # 严格区分大小写：March 与 march 是两个目标词。
            key = vocab.user_word_identity(word) if word else ""
            if key and key not in seen:
                seen.add(key)
                words.append(word)
        return words

    selected_words = _card_words(
        new_card_ids if body.source == "new" else again_card_ids
    )
    if not selected_words:
        detail = (
            "今天还没有新学的单词，请先学习今天的新卡后再生成今日短文"
            if body.source == "new"
            else "今天还没有点过不认识的单词"
        )
        raise HTTPException(
            status_code=400,
            detail=detail,
        )
    word_groups = _article_word_groups(
        selected_words, ai_mod.AI_ARTICLE_TARGET_LIMIT
    )
    if not _start_article_generation(user.id, len(word_groups)):
        raise HTTPException(status_code=409, detail="今日短文正在生成，请稍候")
    background_tasks.add_task(
        _generate_study_articles_in_background,
        user.id,
        _day,
        word_groups,
    )
    return {
        "ok": True,
        "state": "generating",
        "total": len(word_groups),
        "detail": f"AI 正在生成今日短文，共 {len(word_groups)} 篇",
    }


def _ai_article_learning_words(
    db: Session, user_id: int, corpus_id: int, chapter_text: str
) -> set[str]:
    """文章高亮词：只保留本章正文里实际出现过的目标词。"""
    target_rows = (
        db.query(CorpusWord.word)
        .filter(
            CorpusWord.corpus_id == corpus_id,
            CorpusWord.is_target == True,  # noqa: E712
        )
        .all()
    )
    if target_rows:
        candidates = {str(row[0] or "") for row in target_rows}
    else:
        # 升级前生成的旧文章没有目标词标记：退回按卡片词汇取词。
        candidates = {
            str(row[0] or "")
            for row in db.query(Card.word)
            .filter(Card.user_id == user_id)
            .all()
        }
    learning_words: set[str] = set()
    for word in candidates:
        if not word:
            continue
        pattern = ai_mod.target_surface_pattern(word)
        if pattern and pattern.search(html.unescape(str(chapter_text or ""))):
            learning_words.add(word)
    return learning_words


def _ai_article_display_paragraphs(db, user_id, chapter):
    """把 AI 文章章节正文转成带目标词高亮的 HTML 段落。

    保存的正文是纯文本，旧文章可能残留 HTML 实体（如 &#x27;），
    这里先反转义再按当前学习状态重新高亮，保证预览/阅读一致。
    """
    learning_words = _ai_article_learning_words(
        db, user_id, chapter.corpus_id, chapter.text
    )
    target_items = ai_mod._article_highlight_items([], sorted(learning_words))
    return [
        ai_mod._highlight_article_paragraph(html.unescape(p.strip()), target_items)
        for p in chapter.text.split("\n\n")
        if p.strip()
    ]


def _ai_article_target_words(
    db, user_id: int, corpus_id: int, chapter_text: str
) -> list[str]:
    """文章中高亮的目标单词（今天学习、状态为 learning 的词），
    按内置词库（NGSL）排名排序。"""
    learning_words = _ai_article_learning_words(db, user_id, corpus_id, chapter_text)
    return sorted(
        learning_words,
        key=lambda word: (
            vocab.rank_of(word) if vocab.rank_of(word) is not None else 10**9,
            word.lower(),
        ),
    )


@router.get("/cards/article/latest")
def latest_study_article(request: Request, db: Session = Depends(get_db)):
    """返回最新一组 AI 生成的文章（含章节正文），用于记忆页折叠预览。"""
    user = _require_user(db, request)
    generation = _article_generation_status(user.id)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    _day, start_of_day, end_of_day = _learning_day(now)
    corpus = (
        db.query(Corpus)
        .filter(Corpus.user_id == user.id, Corpus.source_type == "ai")
        .order_by(Corpus.id.desc())
        .first()
    )
    if not corpus:
        return {"ok": True, "article": None, "generation": generation}
    # 只保留今天生成的 AI 文章：访问时顺带清掉今天之前生成的旧文章。
    _delete_ai_articles_before(db, user.id, start_of_day)
    db.commit()
    if corpus.created_at and corpus.created_at < start_of_day:
        return {"ok": True, "article": None, "generation": generation}
    chapters = (
        db.query(CorpusChapter)
        .filter(CorpusChapter.corpus_id == corpus.id)
        .order_by(CorpusChapter.position)
        .all()
    )
    chapter_payloads = [
        {
            "article_title": chapter.title or "",
            "paragraphs": _ai_article_display_paragraphs(db, user.id, chapter),
            "target_words": _ai_article_target_words(
                db, user.id, corpus.id, chapter.text or ""
            ),
            "word_count": len(html.unescape(chapter.text or "").split()),
        }
        for chapter in chapters
    ]
    first = chapter_payloads[0] if chapter_payloads else {}
    return {
        "ok": True,
        "generation": generation,
        "article": {
            "corpus_id": corpus.id,
            "book_title": corpus.title,
            "article_title": first.get("article_title", ""),
            "paragraphs": first.get("paragraphs", []),
            "target_words": first.get("target_words", []),
            "word_count": first.get("word_count", 0),
            "chapters": chapter_payloads,
            "created_at": corpus.created_at.isoformat() if corpus.created_at else None,
        },
    }


@router.get("/corpora/{corpus_id}/reading")
def read_ai_article(corpus_id: int, request: Request, db: Session = Depends(get_db)):
    """读取一组 AI 文章：把保存时标记为学习状态的目标词高亮后返回。"""
    user = _require_user(db, request)
    corpus = _corpus_or_404(db, user, corpus_id)
    if corpus.source_type != "ai":
        raise HTTPException(status_code=404, detail="文章不存在")
    # 只保留今天生成的 AI 文章：访问时顺带清掉今天之前生成的旧文章。
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    _day, start_of_day, end_of_day = _learning_day(now)
    _delete_ai_articles_before(db, user.id, start_of_day)
    db.commit()
    if corpus.created_at and corpus.created_at < start_of_day:
        raise HTTPException(status_code=404, detail="文章不存在")
    chapters = (
        db.query(CorpusChapter)
        .filter(CorpusChapter.corpus_id == corpus.id)
        .order_by(CorpusChapter.position)
        .all()
    )
    if not chapters:
        raise HTTPException(status_code=404, detail="文章内容不存在")
    chapter_payloads = [
        {
            "article_title": chapter.title or "",
            "paragraphs": _ai_article_display_paragraphs(db, user.id, chapter),
            "target_words": _ai_article_target_words(
                db, user.id, corpus.id, chapter.text or ""
            ),
            "word_count": len(html.unescape(chapter.text or "").split()),
        }
        for chapter in chapters
    ]
    first = chapter_payloads[0]
    return {
        "ok": True,
        "article": {
            "corpus_id": corpus.id,
            "book_title": corpus.title,
            "article_title": first["article_title"],
            "paragraphs": first["paragraphs"],
            "target_words": first["target_words"],
            "word_count": first["word_count"],
            "chapters": chapter_payloads,
            "created_at": corpus.created_at.isoformat() if corpus.created_at else None,
        },
    }
