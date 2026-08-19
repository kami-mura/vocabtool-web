from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from .card_builder import is_complete_sentence, sentence_front
from .models import Card, ReviewLog, SentenceRefreshPreference

logger = logging.getLogger(__name__)


def get_preference(db: Session, user_id: int) -> SentenceRefreshPreference | None:
    """读取用户例句轮换设置；未设置返回 None（即关闭）。"""
    return db.get(SentenceRefreshPreference, user_id)


def set_preference(db: Session, user_id: int, interval: int) -> SentenceRefreshPreference:
    """保存用户例句轮换设置。

    interval == 0 表示关闭；从 0 切到 >0 时，enabled_at 重置为当前时间，
    避免一开启就把旧卡的多年历史学习天数全部触发换句。
    """
    if interval < 0:
        interval = 0
    pref = db.get(SentenceRefreshPreference, user_id)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if pref is None:
        pref = SentenceRefreshPreference(
            user_id=user_id,
            interval=interval,
            enabled_at=now if interval > 0 else None,
        )
        db.add(pref)
    else:
        if interval > 0 and pref.interval == 0:
            pref.enabled_at = now
        pref.interval = interval
    db.commit()
    return pref


def _study_day_counts(
    db: Session,
    card_ids: Sequence[int],
    enabled_at: dt.datetime,
) -> dict[int, int]:
    """返回每个 card_id 自 enabled_at 以来的不同学习日期数。"""
    if not card_ids:
        return {}
    rows = (
        db.query(
            ReviewLog.card_id,
            func.count(func.distinct(func.date(ReviewLog.reviewed_at))).label("days"),
        )
        .filter(
            ReviewLog.card_id.in_(list(card_ids)),
            ReviewLog.reviewed_at >= enabled_at,
        )
        .group_by(ReviewLog.card_id)
        .all()
    )
    return {row.card_id: int(row.days) for row in rows}


def find_due_cards(
    db: Session,
    user_id: int,
    *,
    enabled_at: dt.datetime,
    interval: int,
    limit: int = 10,
    recent_hours: int = 48,
    now: dt.datetime | None = None,
) -> list[Card]:
    """挑选满足"累计学习天数 % interval == 0"且近期有学习记录的阅读卡。"""
    if interval <= 0 or limit <= 0:
        return []
    if now is None:
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    recent_threshold = now - dt.timedelta(hours=recent_hours)

    candidate_ids = [
        row[0]
        for row in db.query(ReviewLog.card_id)
        .join(Card, ReviewLog.card_id == Card.id)
        .filter(
            Card.user_id == user_id,
            Card.card_type == "reading",
            Card.buried.is_(False),
            ReviewLog.reviewed_at >= enabled_at,
            ReviewLog.reviewed_at >= recent_threshold,
        )
        .distinct()
        .order_by(ReviewLog.card_id)
        .limit(limit)
        .all()
    ]
    if not candidate_ids:
        return []

    counts = _study_day_counts(db, candidate_ids, enabled_at)
    due_ids = [
        card_id
        for card_id in candidate_ids
        if counts.get(card_id, 0) > 0 and counts[card_id] % interval == 0
    ]
    if not due_ids:
        return []
    return (
        db.query(Card)
        .filter(Card.id.in_(due_ids))
        .order_by(Card.id)
        .all()
    )


def _build_front_and_context(example: str, word: str) -> tuple[str, str] | None:
    """把 AI 例句加工成 front（加粗目标词）和 context（原句）。"""
    example = " ".join(example.split()).strip()
    if not example:
        return None
    if not example.endswith((".", "!", "?")):
        example += "."
    if not is_complete_sentence(example, word):
        logger.warning("AI 例句不含目标词：%s -> %s", word, example)
        return None
    context = example
    front = sentence_front(example, word, cloze=False)
    return front, context


def find_cards_for_manual_refresh(
    db: Session,
    user_id: int,
    *,
    enabled_at: dt.datetime,
    limit: int = 10,
) -> list[Card]:
    """手动「立即换 10 张」：取近期有学习记录的阅读卡，按学习天数从多到少排。"""
    if limit <= 0:
        return []
    recent_threshold = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=7)
    candidate_ids = [
        row[0]
        for row in db.query(ReviewLog.card_id)
        .join(Card, ReviewLog.card_id == Card.id)
        .filter(
            Card.user_id == user_id,
            Card.card_type == "reading",
            Card.buried.is_(False),
            ReviewLog.reviewed_at >= enabled_at,
            ReviewLog.reviewed_at >= recent_threshold,
        )
        .distinct()
        .order_by(ReviewLog.card_id)
        .all()
    ]
    if not candidate_ids:
        return []
    counts = _study_day_counts(db, candidate_ids, enabled_at)
    sorted_ids = sorted(
        [cid for cid in candidate_ids if counts.get(cid, 0) > 0],
        key=lambda cid: counts[cid],
        reverse=True,
    )[:limit]
    if not sorted_ids:
        return []
    return (
        db.query(Card)
        .filter(Card.id.in_(sorted_ids))
        .order_by(Card.id)
        .all()
    )


def refresh_cards(
    db: Session,
    user_id: int,
    cards: Sequence[Card],
) -> tuple[int, list[str]]:
    """为指定阅读卡生成新例句并更新 front/context。

    返回 (成功更新数, 错误列表)。失败时保留原句不变。
    """
    from .ai import generate_card_content_in_batches

    if not cards:
        return 0, []
    words = [card.word for card in cards]
    results, errors, _, _ = generate_card_content_in_batches(
        db,
        user_id,
        words,
        card_template="reading",
    )
    updated = 0
    skipped_errors: list[str] = []
    for card in cards:
        if card.word in errors:
            skipped_errors.append(f"{card.word}: {errors[card.word]}")
            continue
        data = results.get(card.word)
        if not data:
            skipped_errors.append(f"{card.word}: AI 未返回结果")
            continue
        example = str(data.get("e") or "").strip()
        built = _build_front_and_context(example, card.word)
        if built is None:
            skipped_errors.append(f"{card.word}: 例句格式无效")
            continue
        front, context = built
        card.front = front
        card.context = context
        updated += 1
    db.commit()
    return updated, skipped_errors


def run_refresh_for_user(
    db: Session,
    user_id: int,
    *,
    limit: int = 10,
    recent_hours: int = 48,
    now: dt.datetime | None = None,
) -> tuple[int, list[str]]:
    """按用户设置自动挑选并刷新阅读卡例句。

    返回 (成功更新数, 错误列表)。
    """
    pref = get_preference(db, user_id)
    if pref is None or pref.interval <= 0 or pref.enabled_at is None:
        return 0, []
    due_cards = find_due_cards(
        db,
        user_id,
        enabled_at=pref.enabled_at,
        interval=pref.interval,
        limit=limit,
        recent_hours=recent_hours,
        now=now,
    )
    if not due_cards:
        return 0, []
    return refresh_cards(db, user_id, due_cards)
