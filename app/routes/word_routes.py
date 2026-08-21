from __future__ import annotations

import random

from fastapi import APIRouter

from ..api_support import (
    Card,
    Corpus,
    CorpusWord,
    Counter,
    Depends,
    HTTPException,
    Request,
    SavedWord,
    Session,
    VocabularyProfile,
    WordEntry,
    _require_user,
    _vocabulary_profile,
    _words_with_cards,
    ai_mod,
    check_request_rate,
    dt,
    get_db,
    vocab,
)
from ..schemas import (
    PriorityWordsIn,
    TopicWordsIn,
    VocabularyProfileIn,
    VocabularyTestSubmitIn,
    WordBatchDeleteIn,
    WordBatchStatusIn,
)

router = APIRouter()

ALLOWED_WORD_STATUSES = {"easy", "mid", "hard"}
VOCABULARY_TEST_LEVELS = tuple(range(1_000, 21_001, 1_000))
VOCABULARY_TEST_BASE_LEVEL = 5_000
VOCABULARY_TEST_WINDOW = 100
VOCABULARY_TEST_WORDS_PER_LEVEL = 5


class _VocabularyTestSubmitIn(VocabularyTestSubmitIn):
    level: int | None = None


# ---------- 生词库 ----------


@router.get("/words")
def list_words(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    status: str = "all",
    limit: int = 120,
):
    user = _require_user(db, request)
    query = q.strip()
    query_lower = query.lower()
    limit = min(max(1, limit), 2000)
    if status != "all" and status not in ALLOWED_WORD_STATUSES:
        raise HTTPException(status_code=400, detail="无效状态")

    saved_rows = db.query(SavedWord).filter(SavedWord.user_id == user.id).all()
    saved_dict = {row.word: row for row in saved_rows}
    all_card_words = {
        str(row[0] or "").split(" [", 1)[0]
        for row in db.query(Card.word)
        .filter(Card.user_id == user.id, Card.card_type != "speaking")
        .distinct()
        .all()
    }
    all_words = set(saved_dict) | all_card_words
    if query_lower:
        all_words = {word for word in all_words if query_lower in word.lower()}

    # 兼容 status 筛选
    if status == "mid":
        all_words = all_words & all_card_words
    elif status == "easy":
        all_words = {
            w for w in all_words
            if w not in all_card_words and saved_dict.get(w) and saved_dict[w].status == "easy"
        }
    elif status == "hard":
        all_words = {
            w for w in all_words
            if w not in all_card_words and (not saved_dict.get(w) or saved_dict[w].status != "easy")
        }

    count = len(all_words)
    ordered = sorted(
        all_words,
        key=lambda word: (
            0 if query_lower and word.lower() == query_lower else 1,
            vocab.rank_of(word) or 999999,
            word,
        ),
    )[:limit]
    entries = {
        row.word: row
        for row in db.query(WordEntry).filter(WordEntry.word.in_(ordered)).all()
    } if ordered else {}
    result = []
    for word in ordered:
        entry = entries.get(word)
        is_mid = word in all_card_words
        explicit = saved_dict.get(word)
        effective = (
            "mid" if is_mid else (explicit.status if explicit else "hard")
        )
        result.append(
            {
                "word": word,
                "status": effective,
                "mid": is_mid,
                "rank": vocab.rank_of(word),
                "pos": entry.pos if entry else "",
                "en_def": entry.en_def if entry else "",
                "zh_def": entry.zh_def if entry else "",
            }
        )
    return {
        "words": result,
        "count": count,
        "query": query,
        "status": status,
    }


@router.delete("/words/{word}")
def remove_personal_word(word: str, request: Request, db: Session = Depends(get_db)):
    """从生词库移除；卡片和复习记录不受影响。"""
    user = _require_user(db, request)
    normalized = vocab.user_word_identity(word.strip())
    removed = db.query(SavedWord).filter(
        SavedWord.user_id == user.id,
        SavedWord.word == normalized,
    ).delete(synchronize_session=False)
    db.commit()
    return {
        "ok": True,
        "removed": bool(removed),
        "word": normalized,
    }


@router.post("/words/delete-batch")
def delete_words_batch(
    body: WordBatchDeleteIn, request: Request, db: Session = Depends(get_db)
):
    """批量移出生词库；卡片和复习记录保持不变。"""
    user = _require_user(db, request)
    normalized = sorted(
        {
            vocab.user_word_identity(word.strip())
            for word in body.words
            if word.strip()
        }
    )
    if not normalized:
        raise HTTPException(status_code=400, detail="没有可删除的单词")
    deleted = db.query(SavedWord).filter(
        SavedWord.user_id == user.id,
        SavedWord.word.in_(normalized),
    ).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "deleted": deleted}


@router.post("/words/batch-status")
def update_words_batch_status(
    body: WordBatchStatusIn, request: Request, db: Session = Depends(get_db)
):
    """批量标记词库单词状态；mid 为制卡派生状态，不接受手动标记。

    优先级：mid > hard > easy。批量标记不会降级更高优先级的词：
    已制卡（mid）与显式 hard 的词保持原状（计入 skipped_higher）；
    已处于目标状态的词（如已 Easy）跳过不重复标记（计入 skipped_existing）。
    preview=true 时不写库，只返回本次会实际更新的个数，用于前端先展示准确数字。
    """
    user = _require_user(db, request)
    status = body.status.strip().lower()
    if status not in ALLOWED_WORD_STATUSES:
        raise HTTPException(status_code=400, detail="无效状态")
    if status == "mid":
        raise HTTPException(status_code=400, detail="mid 由制卡自动维护，不能手动标记")
    normalized = sorted(
        {
            vocab.user_word_identity(word.strip())
            for word in body.words
            if word.strip()
        }
    )
    if not normalized:
        raise HTTPException(status_code=400, detail="没有可更新的单词")
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    priority = {"mid": 3, "hard": 2, "easy": 1}
    target_priority = priority[status]
    card_words = _words_with_cards(db, user.id, normalized)
    existing = {
        row.word: row
        for row in db.query(SavedWord)
        .filter(
            SavedWord.user_id == user.id,
            SavedWord.word.in_(normalized),
        )
        .all()
    }
    updated = 0
    skipped_existing = 0
    skipped_higher = 0
    for word in normalized:
        if word in card_words:
            skipped_higher += 1  # 已制卡（mid）：最高优先级，不覆盖
            continue
        row = existing.get(word)
        current_priority = priority.get(row.status, 0) if row else 0
        if current_priority > target_priority:
            skipped_higher += 1  # 更高优先级（如 hard 标记 easy 时）不降级
            continue
        if row and row.status == status:
            skipped_existing += 1  # 已在目标状态（如已 Easy），跳过不重复标记
            continue
        if row:
            row.status = status
            row.updated_at = now
        else:
            db.add(SavedWord(user_id=user.id, word=word, status=status))
        updated += 1
    if not body.preview:
        db.commit()
    return {
        "ok": True,
        "updated": updated,
        "skipped_existing": skipped_existing,
        "skipped_higher": skipped_higher,
        "preview": body.preview,
        "status": status,
    }


def _profile_dict(profile: VocabularyProfile) -> dict:
    known_rank = int(profile.ngsl_known_rank or 0)
    return {"known_rank": known_rank}


@router.get("/words/ngsl-profile")
def get_ngsl_profile(request: Request, db: Session = Depends(get_db)):
    user = _require_user(db, request)
    return _profile_dict(_vocabulary_profile(db, user))


@router.put("/words/ngsl-profile")
def update_ngsl_profile(
    body: VocabularyProfileIn,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _require_user(db, request)
    profile = _vocabulary_profile(db, user)
    profile.ngsl_known_rank = body.known_rank
    profile.updated_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(profile)
    return {"ok": True, **_profile_dict(profile)}


def _vocabulary_test_level(rank: int) -> int | None:
    return next(
        (
            level
            for level in VOCABULARY_TEST_LEVELS
            if abs(rank - level) <= VOCABULARY_TEST_WINDOW
        ),
        None,
    )


@router.get("/words/vocabulary-test")
def start_vocabulary_test(
    request: Request,
    level: int = VOCABULARY_TEST_BASE_LEVEL,
    db: Session = Depends(get_db),
):
    """从指定千词档位附近随机抽五词。"""
    _require_user(db, request)
    if level not in VOCABULARY_TEST_LEVELS:
        raise HTTPException(status_code=400, detail="词汇测试档位无效")
    words: list[str] = []
    for word, rank in vocab.load_ngsl().items():
        if abs(rank - level) <= VOCABULARY_TEST_WINDOW:
            words.append(word)
    if len(words) < VOCABULARY_TEST_WORDS_PER_LEVEL:
        raise HTTPException(status_code=503, detail="词汇测试题库暂不可用")
    rng = random.SystemRandom()
    questions = [
        {"word": word, "level": level}
        for word in rng.sample(words, VOCABULARY_TEST_WORDS_PER_LEVEL)
    ]
    return {
        "questions": questions,
        "level": level,
        "words_per_level": VOCABULARY_TEST_WORDS_PER_LEVEL,
    }


@router.post("/words/vocabulary-test")
def finish_vocabulary_test(
    body: _VocabularyTestSubmitIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """验证升降路径，按当前档位减去错词数换算并保存。"""
    user = _require_user(db, request)
    start_level = (
        body.level if body.level is not None else VOCABULARY_TEST_BASE_LEVEL
    )
    if start_level not in VOCABULARY_TEST_LEVELS:
        raise HTTPException(status_code=400, detail="词汇测试档位无效")
    seen: set[str] = set()
    if len(body.answers) % VOCABULARY_TEST_WORDS_PER_LEVEL:
        raise HTTPException(status_code=400, detail="词汇测试答案不完整，请重新测试")
    groups: list[tuple[int, int]] = []
    for offset in range(0, len(body.answers), VOCABULARY_TEST_WORDS_PER_LEVEL):
        chunk = body.answers[offset : offset + VOCABULARY_TEST_WORDS_PER_LEVEL]
        chunk_levels: set[int] = set()
        known_count = 0
        for answer in chunk:
            word = answer.word.strip().lower()
            rank = vocab.rank_of(word)
            level = _vocabulary_test_level(rank) if rank is not None else None
            if not word or word in seen or level is None:
                raise HTTPException(status_code=400, detail="词汇测试答案无效，请重新测试")
            seen.add(word)
            chunk_levels.add(level)
            known_count += int(answer.known)
        if len(chunk_levels) != 1:
            raise HTTPException(status_code=400, detail="词汇测试答案无效，请重新测试")
        groups.append((chunk_levels.pop(), known_count))

    expected_level = start_level
    final_level = 0
    final_known = 0
    for index, (level, known_count) in enumerate(groups):
        if level != expected_level:
            raise HTTPException(status_code=400, detail="词汇测试答题顺序无效，请重新测试")
        at_lower_boundary = level == VOCABULARY_TEST_LEVELS[0]
        at_upper_boundary = level == VOCABULARY_TEST_LEVELS[-1]
        terminal = 3 <= known_count <= 4
        if known_count == VOCABULARY_TEST_WORDS_PER_LEVEL:
            terminal = terminal or at_upper_boundary
            if not terminal:
                expected_level += 1_000
        elif known_count <= 2:
            terminal = terminal or at_lower_boundary
            if not terminal:
                expected_level -= 1_000
        if terminal:
            if index != len(groups) - 1:
                raise HTTPException(status_code=400, detail="词汇测试答案过多，请重新测试")
            final_level = level
            final_known = known_count
            break
    if not final_level:
        raise HTTPException(status_code=400, detail="词汇测试尚未完成")

    known_rank = max(0, final_level - (VOCABULARY_TEST_WORDS_PER_LEVEL - final_known) * 200)
    profile = _vocabulary_profile(db, user)
    profile.ngsl_known_rank = known_rank
    profile.updated_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    db.commit()
    return {
        "ok": True,
        "known_rank": known_rank,
        "known_answers": sum(int(answer.known) for answer in body.answers),
        "question_count": len(body.answers),
    }


@router.post("/words/topic")
def topic_word_list(
    body: TopicWordsIn, request: Request, db: Session = Depends(get_db)
):
    """按主题生成英语词表。"""
    user = _require_user(db, request)
    if not check_request_rate(
        db,
        action="topic-words",
        identity=f"u{user.id}",
        limit=300,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="主题词请求过多，请稍后再试")
    words, error = ai_mod.generate_topic_word_list(db, user.id, body.topic, body.count)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {"ok": True, "words": words}


@router.post("/words/priority-select")
def priority_select(
    body: PriorityWordsIn, request: Request, db: Session = Depends(get_db)
):
    """从候选词里筛选最值得先学的词。"""
    user = _require_user(db, request)
    if not check_request_rate(
        db,
        action="priority-select",
        identity=f"u{user.id}",
        limit=300,
        window_minutes=60,
    ):
        raise HTTPException(status_code=429, detail="筛选请求过多，请稍后再试")
    result, error = ai_mod.select_priority_words(
        db, user.id, body.candidates, body.count
    )
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {"ok": True, **result}
