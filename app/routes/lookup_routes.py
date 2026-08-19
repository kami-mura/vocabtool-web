from __future__ import annotations

import hashlib

from fastapi import APIRouter
from sqlalchemy import insert, update
from sqlalchemy.exc import IntegrityError

from ..api_support import (
    _LOOKUP_CACHE_VERSION,
    Card,
    Depends,
    HTTPException,
    LookupCache,
    LookupHistory,
    Request,
    SavedWord,
    Session,
    WordEntry,
    _anonymous_request_identity,
    _require_storage_space,
    _require_user,
    _user_ai_credential,
    _utf8_size,
    ai_mod,
    builtin_lookup,
    card_builder,
    check_request_rate,
    current_user,
    dt,
    get_db,
    re,
    time,
    vocab,
)
from ..models import GuestLookupQuota
from ..schemas import LookupIn, QuestionIn, QuickLookupIn

router = APIRouter()

# ---------- 查询记录 / AI 释义 ----------


def _lookup_type(text: str) -> str:
    if re.fullmatch(r"[\u4e00-\u9fff、/·\- ]+", text):
        return "gloss"
    if re.fullmatch(r"[A-Za-z]+(?:['-][A-Za-z]+)*", text):
        return "word"
    return "phrase"


def _strip_pos_suffix(text: str) -> str:
    """剥离括号词性标注：run (v.) / take off (phrasal verb) -> 词头；不匹配则原样返回。"""
    m = re.fullmatch(
        r"((?:[A-Za-z]+['-]?[A-Za-z]*)(?: [A-Za-z]+['-]?[A-Za-z]*){0,3})"
        r"\s*\(\s*[A-Za-z .]{1,16}\s*\)",
        text.strip(),
    )
    return m.group(1) if m else text


def _pos_suffix(text: str) -> str:
    """返回括号词性后缀（含前导空格），无词性标注时返回空串。"""
    m = re.fullmatch(
        r"(?:[A-Za-z]+['-]?[A-Za-z]*)(?: [A-Za-z]+['-]?[A-Za-z]*){0,3}"
        r"(\s*\(\s*[A-Za-z .]{1,16}\s*\))",
        text.strip(),
    )
    return m.group(1) if m else ""


def _lookup_cache_storage_key(cache_key: str) -> str:
    """LookupCache.query 上限 80 字符；超长键用 sha256 十六进制摘要作存储键。"""
    if len(cache_key) <= 80:
        return cache_key
    return hashlib.sha256(cache_key.encode()).hexdigest()


def _validate_lookup_query(raw_text: str) -> str:
    """查词不做单词清理：只做基本规范化与长度上限，内容交给 AI 处理。

    历史版本会拒绝句子/标点/引导词，现在完全放开（限流与配额仍在）。
    """
    query = " ".join(raw_text.strip().split())
    if not query:
        raise HTTPException(status_code=400, detail="请输入要查询的内容")
    if len(query) > 200:
        raise HTTPException(status_code=400, detail="查询内容过长")
    return query


def _extract_headword(explanation: str, fallback: str) -> str:
    """从释义文本第一行提取词头（去掉发音部分）。

    AI 对拼写错误的词会返回正确词的释义，如：
    "environemnt" -> "environment /ɪnˈvaɪrənmənt/\n1. 环境 | ..."
    第一行的斜杠前就是 AI 认为的正确词头。
    """
    first_line = next(
        (line.strip() for line in (explanation or "").splitlines() if line.strip()), ""
    )
    head = first_line.split("/")[0].strip().split()[0] if first_line else ""
    if not head or not re.match(r"^[A-Za-z]", head):
        return fallback
    return head


def _lookup_dict(
    row: LookupHistory, db: Session | None = None, user_id: int | None = None
) -> dict:
    payload = {
        "id": row.id,
        "query": row.query,
        "query_type": row.query_type,
        "mode": row.mode or "normal",
        "ngsl_rank": (
            vocab.rank_of(vocab.user_word_identity(_strip_pos_suffix(row.query)))
            if row.query_type == "word"
            else None
        ),
        "explanation": row.explanation or "",
        "card_front": row.card_front or "",
        "card_back": row.card_back or "",
        "card_id": row.card_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if db is not None and user_id is not None and row.query_type in {"word", "phrase"}:
        word = _strip_pos_suffix(str(row.query or "").strip())
        payload["has_card"] = db.query(Card.id).filter(
            Card.user_id == user_id,
            Card.word == word,
        ).first() is not None
        saved_row = db.query(SavedWord).filter(
            SavedWord.user_id == user_id,
            SavedWord.word == word,
        ).first()
        payload["word_status"] = (
            "mid" if payload["has_card"] else (saved_row.status if saved_row else None)
        )
        payload["easy"] = bool(
            not payload["has_card"] and saved_row and saved_row.status == "easy"
        )
        payload["saved"] = bool(not payload["has_card"] and saved_row is not None)
    else:
        payload["has_card"] = False
        payload["saved"] = False
        payload["easy"] = False
        payload["word_status"] = None
    return payload


def _valid_lookup_mode(mode: str) -> str:
    return mode if mode in ("normal", "quick", "qa") else "normal"


GUEST_LOOKUP_LIMIT = 20


def _guest_lookup_key(request: Request) -> str:
    identity = _anonymous_request_identity(request)
    return hashlib.sha256(f"guest-lookup:{identity}".encode()).hexdigest()


def _reserve_guest_lookup(db: Session, request: Request) -> int:
    """为未登录用户原子扣减一次体验额度，返回剩余次数。"""
    key = _guest_lookup_key(request)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    def _try_increment() -> bool:
        updated = db.execute(
            update(GuestLookupQuota)
            .where(
                GuestLookupQuota.key == key,
                GuestLookupQuota.count < GUEST_LOOKUP_LIMIT,
            )
            .values(count=GuestLookupQuota.count + 1, updated_at=now)
        )
        return bool(updated.rowcount)

    if _try_increment():
        db.commit()
        count = db.query(GuestLookupQuota.count).filter(GuestLookupQuota.key == key).scalar()
        return GUEST_LOOKUP_LIMIT - int(count)
    try:
        db.execute(
            insert(GuestLookupQuota).values(key=key, count=1, created_at=now, updated_at=now)
        )
        db.commit()
        return GUEST_LOOKUP_LIMIT - 1
    except IntegrityError:
        db.rollback()
        if _try_increment():
            db.commit()
            count = (
                db.query(GuestLookupQuota.count)
                .filter(GuestLookupQuota.key == key)
                .scalar()
            )
            return GUEST_LOOKUP_LIMIT - int(count)
        row = db.query(GuestLookupQuota.count).filter(GuestLookupQuota.key == key).first()
        used = int(row[0]) if row else 0
        if used < GUEST_LOOKUP_LIMIT:
            raise HTTPException(
                status_code=429, detail="查询请求过多，请稍后再试"
            ) from None
        raise HTTPException(
            status_code=429,
            detail=f"{GUEST_LOOKUP_LIMIT} 次体验已用完，登录后可无限使用",
        ) from None


@router.post("/lookups")
def create_lookup(body: LookupIn, request: Request, db: Session = Depends(get_db)):
    if not check_request_rate(
        db,
        action="lookup",
        identity=_anonymous_request_identity(request),
        limit=2000,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="查词请求过多，请稍后再试")
    started_at = time.perf_counter()
    user = current_user(request, db)
    user_credential = _user_ai_credential(db, user)
    guest = user is None
    guest_remaining = None
    text = _validate_lookup_query(body.text)
    head = _strip_pos_suffix(text)
    query_type = _lookup_type(head)
    if guest:
        guest_remaining = _reserve_guest_lookup(db, request)
    # 严格区分大小写：查词身份按用户输入保留（March != march）；
    # 查词一律做词形还原（running -> run），避免衍生词污染词库；
    # 括号词性保留给 AI（run (v.)），词形还原只作用于词头（running (v.) -> run (v.)）。
    original_query = text
    spelling_note = None
    if query_type == "word":
        identity = vocab.user_word_identity(head)
        if identity != head and head in text:
            text = text.replace(head, identity, 1)
        elif identity != head:
            text = identity
        # 缓存键区分词性（run (v.) 与 run (n.) 释义不同），内置词库与缓存命中仍按词头。
        cache_key = identity + _pos_suffix(text)
    else:
        cache_key = text

    result = None
    ai_error = None
    first_call_charged = False
    lookup_source = "unavailable"
    builtin_result = builtin_lookup.get(identity) if query_type == "word" else None
    cached = db.get(LookupCache, _lookup_cache_storage_key(cache_key))
    if builtin_result:
        card_front, card_back = ai_mod._card_fields_from_streamlit_result(
            builtin_result, text, query_type
        )
        result = {
            "explanation": builtin_result,
            "card_front": card_front,
            "card_back": card_back,
        }
        lookup_source = "builtin"
    elif (
        cached
        and cached.prompt_version == _LOOKUP_CACHE_VERSION
        and cached.query_type == query_type
        and cached.explanation.strip()
    ):
        result = {
            "explanation": cached.explanation,
            "card_front": cached.card_front or "",
            "card_back": cached.card_back or "",
        }
        lookup_source = "local_cache"
    elif (
        ai_mod.ai_enabled(user_credential)
        if user_credential
        else ai_mod.ai_enabled()
    ):
        if user_credential:
            result, ai_error, first_call_charged = ai_mod.explain_lookup(
                db,
                user.id if user else None,
                text,
                query_type,
                user_api_key=user_credential,
            )
        else:
            result, ai_error, first_call_charged = ai_mod.explain_lookup(
                db, user.id if user else None, text, query_type
            )
        if result:
            lookup_source = ai_mod._active_provider(user_credential)
            if cached:
                cached.query_type = query_type
                cached.explanation = result["explanation"]
                cached.card_front = result["card_front"]
                cached.card_back = result["card_back"]
                cached.prompt_version = _LOOKUP_CACHE_VERSION
                cached.source = lookup_source
                cached.updated_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
            else:
                try:
                    with db.begin_nested():
                        db.add(
                            LookupCache(
                                query=_lookup_cache_storage_key(cache_key),
                                query_type=query_type,
                                explanation=result["explanation"],
                                card_front=result["card_front"],
                                card_back=result["card_back"],
                                prompt_version=_LOOKUP_CACHE_VERSION,
                                source=lookup_source,
                            )
                        )
                except IntegrityError:
                    # 并发请求已写入同一词条的缓存；保留对方的结果即可。
                    pass
        elif query_type == "word" and not (result or {}).get("explanation", "").strip():
            # AI 已启用但调用失败：若 WordEntry 已有该词释义则使用本地释义兜底。
            entry = db.query(WordEntry).filter(WordEntry.word == text).first()
            if entry and (entry.zh_def or entry.en_def):
                explanation = card_builder.definition_text(entry)
                result = {
                    "explanation": explanation,
                    "card_front": text,
                    "card_back": explanation,
                }
                lookup_source = "word_cache"
    elif query_type == "word":
        entry = db.query(WordEntry).filter(WordEntry.word == text).first()
        if entry and (entry.zh_def or entry.en_def):
            explanation = card_builder.definition_text(entry)
            result = {
                "explanation": explanation,
                "card_front": text,
                "card_back": explanation,
            }
            lookup_source = "word_cache"
        else:
            ai_error = "服务器尚未配置 AI API Key，查询已记录"
    else:
        ai_error = "服务器尚未配置 AI API Key，查询已记录"

    # 以 AI 为准：AI 对拼写错误的词会返回正确词的释义，
    # 从释义词头提取 AI 使用的正确拼写，与用户输入比较后提示。
    if query_type == "word" and (result or {}).get("explanation", "").strip():
        headword = _extract_headword(result["explanation"], text)
        if headword.lower() != text.lower():
            spelling_note = {
                "original": original_query,
                "corrected": headword,
            }

    # 查询完全无结果且词不在词表时，用词表拼写建议重查一次。
    if (
        query_type == "word"
        and not (result or {}).get("explanation", "").strip()
        and vocab.rank_of(text) is None
    ):
        suggestion = vocab.suggest_correction(text)
        if suggestion and suggestion != text.lower():
            corrected_cache_key = suggestion
            builtin_corrected = builtin_lookup.get(corrected_cache_key)
            corrected = None
            corrected_source = None
            if builtin_corrected:
                card_front, card_back = ai_mod._card_fields_from_streamlit_result(
                    builtin_corrected, suggestion, query_type
                )
                corrected = {
                    "explanation": builtin_corrected,
                    "card_front": card_front,
                    "card_back": card_back,
                }
                corrected_source = "builtin"
            else:
                cached_corrected = db.get(
                    LookupCache, _lookup_cache_storage_key(corrected_cache_key)
                )
                if (
                    cached_corrected
                    and cached_corrected.prompt_version == _LOOKUP_CACHE_VERSION
                    and cached_corrected.query_type == query_type
                    and cached_corrected.explanation.strip()
                ):
                    corrected = {
                        "explanation": cached_corrected.explanation,
                        "card_front": cached_corrected.card_front or "",
                        "card_back": cached_corrected.card_back or "",
                    }
                    corrected_source = "local_cache"
                elif (
                    ai_mod.ai_enabled(user_credential)
                    if user_credential
                    else ai_mod.ai_enabled()
                ):
                    # 第一次 AI 查询已实际消耗配额时才复用（不再重复扣）；
                    # 未消耗（配额不足被拒、游客失败已退还）时必须重新预占，
                    # 否则拼写纠错路径会成为绕过配额免费调用 AI 的通道。
                    if user_credential:
                        corrected, _, _ = ai_mod.explain_lookup(
                            db,
                            user.id if user else None,
                            suggestion,
                            query_type,
                            reserve_quota=not first_call_charged,
                            user_api_key=user_credential,
                        )
                    else:
                        corrected, _, _ = ai_mod.explain_lookup(
                            db,
                            user.id if user else None,
                            suggestion,
                            query_type,
                            reserve_quota=not first_call_charged,
                        )
                    if corrected:
                        corrected_source = ai_mod._active_provider(user_credential)
                        # 与主路径一致写入缓存：否则同一个拼写错误每次
                        # 都重新调 AI 并扣一次配额。
                        if cached_corrected:
                            cached_corrected.query_type = query_type
                            cached_corrected.explanation = corrected["explanation"]
                            cached_corrected.card_front = corrected["card_front"]
                            cached_corrected.card_back = corrected["card_back"]
                            cached_corrected.prompt_version = _LOOKUP_CACHE_VERSION
                            cached_corrected.source = corrected_source
                            cached_corrected.updated_at = (
                                dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                            )
                        else:
                            try:
                                with db.begin_nested():
                                    db.add(
                                        LookupCache(
                                            query=_lookup_cache_storage_key(
                                                corrected_cache_key
                                            ),
                                            query_type=query_type,
                                            explanation=corrected["explanation"],
                                            card_front=corrected["card_front"],
                                            card_back=corrected["card_back"],
                                            prompt_version=_LOOKUP_CACHE_VERSION,
                                            source=corrected_source,
                                        )
                                    )
                            except IntegrityError:
                                # 并发请求已写入同一词条的缓存；保留对方的结果即可。
                                pass
            if corrected and corrected.get("explanation", "").strip():
                result = corrected
                if corrected_source:
                    lookup_source = corrected_source
                spelling_note = {
                    "original": original_query,
                    "corrected": suggestion,
                }
                text = suggestion
                cache_key = corrected_cache_key

    result = result or {"explanation": "", "card_front": text, "card_back": ""}
    if user is None:
        lookup_payload = {
            "id": None,
            "query": text,
            "query_type": query_type,
            "mode": "normal",
            "ngsl_rank": vocab.rank_of(text) if query_type == "word" else None,
            "explanation": result["explanation"],
            "card_front": result["card_front"],
            "card_back": result["card_back"],
            "card_id": None,
            "has_card": False,
            "saved": False,
            "created_at": None,
        }
        db.commit()
    else:
        _require_storage_space(
            db,
            user.id,
            _utf8_size(text)
            + _utf8_size(result["explanation"])
            + _utf8_size(result["card_front"])
            + _utf8_size(result["card_back"]),
        )
        row = LookupHistory(
            user_id=user.id,
            query=text,
            query_type=query_type,
            mode="normal",
            explanation=result["explanation"],
            card_front=result["card_front"],
            card_back=result["card_back"],
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        lookup_payload = _lookup_dict(row, db, user.id)
    elapsed_ms = max(0, round((time.perf_counter() - started_at) * 1000))
    return {
        "ok": True,
        "lookup": lookup_payload,
        "lookup_source": lookup_source,
        "elapsed_ms": elapsed_ms,
        "guest_remaining": guest_remaining,
        "ai_enabled": (
            ai_mod.ai_enabled(user_credential)
            if user_credential
            else ai_mod.ai_enabled()
        ),
        "ai_error": ai_error,
        "spelling_note": spelling_note,
    }


@router.get("/lookups/builtin-info")
def builtin_lookup_info(request: Request, db: Session = Depends(get_db)):
    _require_user(db, request)
    learned_count = (
        db.query(LookupCache)
        .filter(LookupCache.prompt_version == _LOOKUP_CACHE_VERSION)
        .count()
    )
    return {
        "builtin_count": builtin_lookup.count(),
        "learned_count": learned_count,
        "sample_terms": builtin_lookup.sample_terms(),
    }


@router.get("/lookups")
def list_lookups(
    request: Request, db: Session = Depends(get_db), limit: int = 30, mode: str = "normal"
):
    user = _require_user(db, request)
    limit = min(max(1, limit), 100)
    mode = _valid_lookup_mode(mode)
    rows = (
        db.query(LookupHistory)
        .filter(LookupHistory.user_id == user.id, LookupHistory.mode == mode)
        .order_by(LookupHistory.created_at.desc(), LookupHistory.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "lookups": [_lookup_dict(row, db, user.id) for row in rows],
        "mode": mode,
    }


@router.delete("/lookups/{lookup_id}")
def delete_lookup(lookup_id: int, request: Request, db: Session = Depends(get_db)):
    """删除单条查询历史（仅限本人）。"""
    user = _require_user(db, request)
    row = (
        db.query(LookupHistory)
        .filter(LookupHistory.id == lookup_id, LookupHistory.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="查询记录不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.delete("/lookups")
def clear_lookups(request: Request, db: Session = Depends(get_db), mode: str = "normal"):
    """清空指定来源的全部查询历史（默认简洁查词）。"""
    user = _require_user(db, request)
    mode = _valid_lookup_mode(mode)
    db.query(LookupHistory).filter(
        LookupHistory.user_id == user.id, LookupHistory.mode == mode
    ).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "mode": mode}


@router.post("/lookups/{lookup_id}/reopen")
def reopen_lookup(
    lookup_id: int, request: Request, db: Session = Depends(get_db)
):
    """从个人历史重新打开查询，不调用 AI，也不创建重复历史。"""
    user = _require_user(db, request)
    lookup = (
        db.query(LookupHistory)
        .filter(LookupHistory.id == lookup_id, LookupHistory.user_id == user.id)
        .first()
    )
    if not lookup:
        raise HTTPException(status_code=404, detail="查询记录不存在")
    return {
        "ok": True,
        "lookup": _lookup_dict(lookup, db, user.id),
        "lookup_source": "history",
        "elapsed_ms": 0,
    }


@router.post("/lookups/{lookup_id}/save")
def save_lookup_word(
    lookup_id: int, request: Request, db: Session = Depends(get_db)
):
    """把本人查词结果加入生词库；已有学习卡片时拒绝。"""
    user = _require_user(db, request)
    lookup = (
        db.query(LookupHistory)
        .filter(LookupHistory.id == lookup_id, LookupHistory.user_id == user.id)
        .first()
    )
    if not lookup:
        raise HTTPException(status_code=404, detail="查询记录不存在")
    if lookup.query_type not in {"word", "phrase"}:
        raise HTTPException(status_code=400, detail="只有英文单词或短语可以加入生词库")
    # 入库只剥离括号词性（run (v.) -> run），不做词形还原：
    # 查词阶段已统一清洗，这里保留用户输入的词形。
    word = _strip_pos_suffix(str(lookup.query or "").strip())[:100]
    if db.query(Card.id).filter(Card.user_id == user.id, Card.word == word).first():
        raise HTTPException(status_code=409, detail="这个词已有学习卡片")
    existing = db.query(SavedWord).filter(
        SavedWord.user_id == user.id,
        SavedWord.word == word,
    ).first()
    if existing:
        if existing.status == "easy":
            existing.status = "hard"
            existing.updated_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
            db.commit()
            return {
                "ok": True,
                "created": True,
                "promoted_from_easy": True,
                "word": word,
            }
        return {"ok": True, "created": False, "word": word}
    _require_storage_space(db, user.id, _utf8_size(word) + 16)
    try:
        with db.begin_nested():
            db.add(SavedWord(user_id=user.id, word=word))
            db.flush()
    except IntegrityError:
        pass
    db.commit()
    return {"ok": True, "created": True, "word": word}


# ---------- AI 释义 ----------


@router.post("/lookups/quick")
def quick_lookup(body: QuickLookupIn, request: Request, db: Session = Depends(get_db)):
    """词源速查：中文释义 + 底层逻辑 + 词源史诗。"""
    user = current_user(request, db)
    user_credential = _user_ai_credential(db, user)
    guest = user is None
    guest_remaining = None
    if not check_request_rate(
        db,
        action="quick-lookup",
        identity=(
            f"u{user.id}" if user else _anonymous_request_identity(request)
        ),
        limit=1000,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="查询请求过多，请稍后再试")
    if guest:
        guest_remaining = _reserve_guest_lookup(db, request)
    if user_credential:
        result, error = ai_mod.quick_lookup(
            db, user.id if user else None, body.text, user_credential
        )
    else:
        result, error = ai_mod.quick_lookup(db, user.id if user else None, body.text)
    if error:
        raise HTTPException(status_code=400, detail=error)
    text = re.sub(r"\s+", " ", str(body.text or "").strip())
    query_type = (
        "word" if re.fullmatch(r"[A-Za-z]+(?:['-][A-Za-z]+)?", text) else "phrase"
    )
    explanation = str(result.get("explanation") or "")
    payload = {
        "ok": True,
        "lookup": {
            **result,
            "ngsl_rank": vocab.rank_of(text) if query_type == "word" else None,
        },
        "guest_remaining": guest_remaining,
    }
    if guest:
        db.commit()
        return payload
    _require_storage_space(db, user.id, _utf8_size(text) + _utf8_size(explanation))
    row = LookupHistory(
        user_id=user.id,
        query=text,
        query_type=query_type,
        mode="quick",
        explanation=explanation,
    )
    db.add(row)
    db.commit()
    # 带 id 与词库状态：词源结果也可加入生词库
    payload["lookup"] = {**payload["lookup"], **_lookup_dict(row, db, user.id)}
    return payload


@router.post("/lookups/question")
def ask_question(body: QuestionIn, request: Request, db: Session = Depends(get_db)):
    """回答英语学习问题（用法、语法、翻译、改写等）。"""
    user = current_user(request, db)
    user_credential = _user_ai_credential(db, user)
    guest = user is None
    guest_remaining = None
    if not check_request_rate(
        db,
        action="question",
        identity=(
            f"u{user.id}" if user else _anonymous_request_identity(request)
        ),
        limit=1000,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="问答请求过多，请稍后再试")
    if guest:
        guest_remaining = _reserve_guest_lookup(db, request)
    if user_credential:
        answer, error = ai_mod.answer_question(
            db, user.id if user else None, body.question, user_credential
        )
    else:
        answer, error = ai_mod.answer_question(
            db, user.id if user else None, body.question
        )
    if error:
        raise HTTPException(status_code=400, detail=error)
    question = re.sub(r"\s+", " ", str(body.question or "").strip())
    # 纯英文单词/短语的问题可加入生词库（如直接问 apple），
    # 自由提问（含中文/句子）不提供保存。
    if re.fullmatch(r"[A-Za-z]+(?:['-][A-Za-z]+)*", question):
        qtype = "word"
    elif re.fullmatch(
        r"[A-Za-z]+(?:['-][A-Za-z]+)*(?: [A-Za-z]+(?:['-][A-Za-z]+)*){1,4}", question
    ):
        qtype = "phrase"
    else:
        qtype = "qa"
    payload = {"ok": True, "answer": answer, "guest_remaining": guest_remaining}
    if guest:
        db.commit()
        return payload
    _require_storage_space(db, user.id, _utf8_size(question) + _utf8_size(answer))
    row = LookupHistory(
        user_id=user.id,
        query=question,
        query_type=qtype,
        mode="qa",
        explanation=answer,
    )
    db.add(row)
    db.commit()
    if qtype in {"word", "phrase"}:
        payload["lookup"] = _lookup_dict(row, db, user.id)
    return payload


@router.post("/words/{word}/enrich")
def enrich_word(word: str, request: Request, db: Session = Depends(get_db)):
    user = _require_user(db, request)
    if not check_request_rate(
        db,
        action="enrich",
        identity=f"u{user.id}",
        limit=1000,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="释义请求过多，请稍后再试")
    word = word.strip()
    if not word or len(word) > 100:
        raise HTTPException(status_code=400, detail="单词格式不正确")
    entry, error = ai_mod.enrich_word(db, user.id, word)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {
        "ok": True,
        "word": entry.word,
        "pos": entry.pos,
        "en_def": entry.en_def,
        "zh_def": entry.zh_def,
        "text": card_builder.definition_text(entry),
    }
