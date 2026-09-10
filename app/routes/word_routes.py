from __future__ import annotations

from fastapi import APIRouter

from ..api_support import (
    Card,
    Depends,
    HTTPException,
    Request,
    SavedWord,
    Session,
    VocabularyProfile,
    WordEntry,
    _require_user,
    _vocabulary_profile,
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
    WordBatchDeleteIn,
)

router = APIRouter()

# ---------- 生词库 ----------


@router.get("/words")
def list_words(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    limit: int = 120,
):
    """生词库列表：本人加入的、尚未制卡的词。

    「是否已制卡」由 cards 表派生（制卡后词暂离生词库，删卡后自动回来），
    词库本身不保存任何掌握状态。
    """
    user = _require_user(db, request)
    query = q.strip()
    query_lower = query.lower()
    limit = min(max(1, limit), 2000)

    card_words = {
        str(row[0] or "").split(" [", 1)[0]
        for row in db.query(Card.word)
        .filter(Card.user_id == user.id)
        .distinct()
        .all()
    }
    all_words = {
        str(row[0] or "")
        for row in db.query(SavedWord.word)
        .filter(SavedWord.user_id == user.id)
        .all()
    } - card_words
    if query_lower:
        all_words = {word for word in all_words if query_lower in word.lower()}

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
        result.append(
            {
                "word": word,
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
