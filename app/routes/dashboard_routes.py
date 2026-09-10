from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter
from sqlalchemy import func
from sqlalchemy.orm import load_only

from ..api_support import (
    Card,
    Corpus,
    CorpusWord,
    Counter,
    Depends,
    Request,
    ReviewLog,
    SavedWord,
    Session,
    _ensure_daily_new_assignments,
    _learning_day,
    _require_user,
    _review_preference,
    _vocabulary_profile,
    _words_with_cards,
    config,
    dt,
    get_db,
    srs,
    vocab,
)

router = APIRouter()

# ---------- 统计 ----------


def _study_date_expr(col):
    """把 UTC 裸时间按站点时区换算成本地日期的 SQL 表达式。

    SQLite 用 date(col, modifier) 双参语法。
    """
    try:
        tz = ZoneInfo(config.APP_TIMEZONE)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("Asia/Shanghai")
    offset_hours = (
        tz.utcoffset(dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600
    )
    return func.date(col, f"{offset_hours:+g} hours")


@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = _require_user(db, request)
    known_rank = _vocabulary_profile(db, user).ngsl_known_rank
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    learning_day, start_of_today, end_of_today = _learning_day(now)
    start = start_of_today - dt.timedelta(days=29)

    corpus_count = (
        db.query(func.count(Corpus.id)).filter(Corpus.user_id == user.id).scalar() or 0
    )
    corpus_ids = [
        row[0]
        for row in db.query(Corpus.id).filter(Corpus.user_id == user.id).all()
    ]
    word_counts: Counter = Counter()
    if corpus_ids:
        for word, count in (
            db.query(CorpusWord.word, CorpusWord.count)
            .filter(CorpusWord.corpus_id.in_(corpus_ids))
            .all()
        ):
            word_counts[word] += int(count or 0)
    words = list(word_counts)
    known_words = {
        word
        for word in words
        if (rank := vocab.rank_of(word)) is not None and rank <= known_rank
    }
    total_marked = len(words)
    type_coverage = (
        round(len(known_words) / total_marked * 100, 1)
        if total_marked
        else None
    )
    total_tokens = sum(word_counts.values())
    known_tokens = sum(
        count
        for word, count in word_counts.items()
        if word in known_words
    )
    token_coverage = (
        round(known_tokens / total_tokens * 100, 1) if total_tokens else None
    )
    card_words = _words_with_cards(db, user.id, words)
    target_density = round(len(card_words) / total_marked * 100, 1) if total_marked else None
    saved_word_count = db.query(func.count(SavedWord.id)).filter(
        SavedWord.user_id == user.id
    ).scalar() or 0

    # 只取统计所需列，避免加载完整 ReviewLog 实体。
    log_rows = (
        db.query(
            ReviewLog.reviewed_at,
            ReviewLog.rating,
            ReviewLog.card_id,
            ReviewLog.is_new,
            ReviewLog.previous_state,
            ReviewLog.previous_due_at,
        )
        .filter(ReviewLog.user_id == user.id, ReviewLog.reviewed_at >= start)
        .all()
    )
    daily_reviews: Counter = Counter()
    rating_counts: Counter = Counter()
    today_unique_cards: set[int] = set()
    today_again_cards: set[int] = set()
    today_studied = 0
    delayed_first: dict[tuple[str, int], str] = {}
    for reviewed_at, rating, card_id, is_new, previous_state, previous_due_at in sorted(
        log_rows, key=lambda row: row[0]
    ):
        local_day, _day_start, _day_end = _learning_day(reviewed_at)
        daily_reviews[local_day] += 1
        rating_counts[rating] += 1
        if start_of_today <= reviewed_at < end_of_today:
            today_studied += 1
            today_unique_cards.add(card_id)
            if rating == "again":
                today_again_cards.add(card_id)
        if (
            not is_new
            and previous_state != "learning"
            and previous_due_at is not None
            and previous_due_at <= reviewed_at
        ):
            delayed_first.setdefault((local_day, card_id), rating)
    review_days = []
    for i in range(30):
        day = (dt.date.fromisoformat(learning_day) - dt.timedelta(days=29 - i)).isoformat()
        review_days.append({"date": day, "count": daily_reviews.get(day, 0)})
    delayed_total = len(delayed_first)
    delayed_success = sum(
        rating in {"hard", "good", "easy"} for rating in delayed_first.values()
    )
    delayed_recall_rate = (
        round(delayed_success / delayed_total * 100, 1) if delayed_total else None
    )

    card_count = (
        db.query(func.count(Card.id)).filter(Card.user_id == user.id).scalar() or 0
    )
    due_today = (
        db.query(func.count(Card.id))
        .filter(
            Card.user_id == user.id,
            Card.buried.is_(False),
            Card.due_at.isnot(None),
            Card.due_at < end_of_today,
        )
        .scalar()
        or 0
    )
    again_pending = (
        db.query(func.count(Card.id))
        .filter(Card.user_id == user.id, Card.state == "learning")
        .scalar()
        or 0
    )
    # 记忆曲线/到期预测只需已调度卡片的调度字段。
    srs_cards = (
        db.query(Card)
        .options(
            load_only(
                Card.id,
                Card.due_at,
                Card.state,
                Card.interval_days,
                Card.ease,
                Card.reps,
                Card.lapses,
                Card.learning_step,
                # 记忆曲线/到期预测都要读 FSRS 状态；漏掉会退化为逐卡懒加载 N+1。
                Card.fsrs_state,
            )
        )
        .filter(
            Card.user_id == user.id,
            Card.buried.is_(False),
            Card.due_at.isnot(None),
        )
        .all()
    )
    total_reviews = (
        db.query(func.count(ReviewLog.id))
        .filter(ReviewLog.user_id == user.id)
        .scalar()
        or 0
    )
    # 连续学习天数：有复习记录的连续本地日期（按站点时区，与 _learning_day 一致）。
    # 今天有记录则从今天往前数；今天还没学但昨天有记录，则从昨天往前数（连续未断）。
    study_date_rows = (
        db.query(func.distinct(_study_date_expr(ReviewLog.reviewed_at)))
        .filter(ReviewLog.user_id == user.id)
        .all()
    )
    study_date_set = {row[0] for row in study_date_rows}
    try:
        tz = ZoneInfo(config.APP_TIMEZONE)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("Asia/Shanghai")
    cursor = dt.datetime.now(tz).date()
    if cursor.isoformat() not in study_date_set:
        cursor -= dt.timedelta(days=1)
    consecutive_study_days = 0
    while cursor.isoformat() in study_date_set:
        consecutive_study_days += 1
        cursor -= dt.timedelta(days=1)
    attempted_cards = (
        db.query(func.count(func.distinct(ReviewLog.card_id)))
        .filter(ReviewLog.user_id == user.id)
        .scalar()
        or 0
    )
    forecast = dict(srs.forecast_due_counts(srs_cards, days=30, now=now))
    new_cards_per_day = _review_preference(db, user).new_cards_per_day
    assigned_ids = _ensure_daily_new_assignments(db, user, learning_day, new_cards_per_day)
    new_remaining = 0
    if assigned_ids:
        new_remaining = (
            db.query(func.count(Card.id))
            .filter(
                Card.user_id == user.id,
                Card.id.in_(assigned_ids),
                Card.due_at.is_(None),
            )
            .scalar()
            or 0
        )

    return {
        "library_counts": {
            "saved": int(saved_word_count),
            "cards": int(card_count),
        },
        "total_marked": total_marked,
        "type_coverage": type_coverage,
        "token_coverage": token_coverage,
        "target_density": target_density,
        "corpus_count": int(corpus_count),
        "card_count": int(card_count),
        "total_reviews": int(total_reviews),
        "consecutive_study_days": int(consecutive_study_days),
        "attempted_cards": int(attempted_cards),
        "review_days": review_days,
        "forecast": [{"date": d, "count": forecast.get(d, 0)} for d in sorted(forecast)],
        "delayed_recall_rate": delayed_recall_rate,
        "delayed_recall_samples": delayed_total,
        "forecast_assumption": "假设每次到期复习都评分为 Good",
        "memory_curve_label": "FSRS 预测回忆率",
        "due_today": int(due_today),
        "again_pending": int(again_pending),
        "new_remaining": int(new_remaining),
        "new_cards_per_day": new_cards_per_day,
        "rating_counts": dict(rating_counts),
        "today_studied": today_studied,
        "today_unique_cards": len(today_unique_cards),
        "today_again_rate": round(
            len(today_again_cards) / len(today_unique_cards) * 100, 1
        )
        if today_unique_cards
        else 0.0,
        "memory_curve": srs.memory_curve(srs_cards, days=30, now=now),
    }
