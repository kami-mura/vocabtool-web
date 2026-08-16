from __future__ import annotations

import datetime as dt
from collections import Counter
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fsrs import Card as FSRSCard
from fsrs import Rating, Scheduler, State

from . import config

# FSRS 原生四档评分，一一对应，不做改编。
RATINGS = ("again", "hard", "good", "easy")

_RATING_MAP = {
    "again": Rating.Again,
    "hard": Rating.Hard,
    "good": Rating.Good,
    "easy": Rating.Easy,
}

# FSRS-6 调度器：
# - 目标保持率 90%；
# - 学习/重学步骤 0 秒（用户确认）；
# - 最大间隔 365 天（1 年，用户确认）；
# - fuzzing 开启（FSRS 默认，用户确认）。
_FSRS = Scheduler(
    desired_retention=0.9,
    learning_steps=(dt.timedelta(seconds=0),),
    relearning_steps=(dt.timedelta(seconds=0),),
    maximum_interval=365,
    enable_fuzzing=True,
)


def _aware(value: dt.datetime | None, fallback: dt.datetime | None = None) -> dt.datetime:
    value = value or fallback or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _naive_utc(value: dt.datetime) -> dt.datetime:
    return value.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _site_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(config.APP_TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def _align_due(scheduled_due: dt.datetime, now: dt.datetime) -> dt.datetime:
    """间隔 ≥1 天的到期时间对齐到到期日所在站点日期 0 点。"""
    due = _aware(scheduled_due)
    if (due - _aware(now)).total_seconds() < 86_400:
        return due
    tz = _site_timezone()
    local = due.astimezone(tz)
    boundary = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return boundary.astimezone(dt.timezone.utc)


def _fsrs_card_from_model(card, now: dt.datetime | None = None) -> FSRSCard:
    """从数据库 fsrs_state 读取 FSRS 卡；缺失时按旧字段近似初始化。"""
    raw = getattr(card, "fsrs_state", None)
    if raw:
        try:
            return FSRSCard.from_json(raw)
        except (TypeError, ValueError, KeyError):
            pass
    review_time = _aware(now)
    card_id = int(getattr(card, "id", 0) or 0)
    due = getattr(card, "due_at", None)
    if due is not None:
        due = _aware(due, review_time)
        interval = max(1.0, float(getattr(card, "interval_days", 0) or 1.0))
        learning = (
            str(getattr(card, "state", "") or "") == "learning"
            or int(getattr(card, "learning_step", 0) or 0) > 0
        )
        if learning:
            return FSRSCard(
                card_id=card_id,
                state=State.Learning,
                step=0,
                stability=interval,
                difficulty=5.0,
                due=due,
                last_review=due,
            )
        return FSRSCard(
            card_id=card_id,
            state=State.Review,
            step=None,
            stability=interval,
            difficulty=5.0,
            due=due,
            last_review=due - dt.timedelta(days=interval),
        )
    return FSRSCard(
        card_id=card_id,
        state=State.Learning,
        step=0,
        due=review_time,
    )


def _interval_label(seconds: int) -> str:
    if seconds < 3600:
        return f"{max(1, round(seconds / 60))} 分钟"
    if seconds < 86_400:
        return f"{max(1, round(seconds / 3600))} 小时"
    days = seconds / 86_400
    return f"{days:.1f} 天" if days < 2 and not days.is_integer() else f"{round(days)} 天"


def _day_interval_days(due: dt.datetime, review_time: dt.datetime) -> float:
    """到期时间相对评分时间的自然日间隔（站点时区）。"""
    if due <= review_time:
        return 0.0
    tz = _site_timezone()
    due_day = due.astimezone(tz).date()
    today = review_time.astimezone(tz).date()
    return float(max(0, (due_day - today).days))


def _preview_outcome(
    fsrs_card: FSRSCard, rating: str, review_time: dt.datetime
) -> tuple[dt.datetime, float]:
    """返回某评分的产品级结果 (due, interval_days)，不修改传入状态。"""
    updated, _ = _FSRS.review_card(
        fsrs_card, _RATING_MAP[rating], review_datetime=review_time
    )
    if rating == "again" or updated.state in (State.Learning, State.Relearning):
        return updated.due, 0.0
    due = _align_due(updated.due, review_time)
    interval_days = _day_interval_days(due, review_time)
    return due, interval_days


def _sync_model_from_fsrs(
    card,
    updated: FSRSCard,
    review_time: dt.datetime,
    *,
    rating: str,
    previous_state: str,
) -> None:
    """把 FSRS 结果同步回数据库字段（四档直接对应，无改编）。"""
    if rating == "again" or updated.state in (State.Learning, State.Relearning):
        card.fsrs_state = updated.to_json()
        card.state = "learning"
        card.due_at = _naive_utc(updated.due)
        card.interval_days = 0.0
        card.learning_step = int(updated.step or 0)
        card.session_reduce_day = ""
        card.session_reduce_used = 0
        card.reps = int(getattr(card, "reps", 0) or 0) + 1
        if rating == "again" and previous_state == "review":
            card.lapses = int(getattr(card, "lapses", 0) or 0) + 1
        return

    due = _align_due(updated.due, review_time)
    interval_days = _day_interval_days(due, review_time)
    updated.due = due
    card.fsrs_state = updated.to_json()
    card.state = "review"
    card.due_at = _naive_utc(due)
    card.interval_days = interval_days
    card.learning_step = int(updated.step or 0)
    card.session_reduce_day = ""
    card.session_reduce_used = 0
    card.reps = int(getattr(card, "reps", 0) or 0) + 1


def apply_rating_with_log(card, rating: str, now: dt.datetime | None = None):
    """FSRS-6 调度入口，返回 (card, fsrs_review_log_json)。"""
    if rating not in RATINGS:
        raise ValueError(f"无效评分: {rating}")
    review_time = _aware(now)
    previous_state = str(getattr(card, "state", "new") or "new")
    fsrs_card = _fsrs_card_from_model(card, now=review_time)
    updated, review_log = _FSRS.review_card(
        fsrs_card,
        _RATING_MAP[rating],
        review_datetime=review_time,
    )
    _sync_model_from_fsrs(
        card,
        updated,
        review_time,
        rating=rating,
        previous_state=previous_state,
    )
    return card, review_log.to_json()


def apply_rating(card, rating: str, now: dt.datetime | None = None):
    card, _ = apply_rating_with_log(card, rating, now=now)
    return card


def rating_previews(
    card, now: dt.datetime | None = None, *, session_repeat: bool = False
) -> dict[str, dict]:
    """按同一算法预览三种评分的效果：模糊/重来=今天继续，认识=下次间隔。"""
    del session_repeat
    review_time = _aware(now)
    fsrs_card = _fsrs_card_from_model(card, now=review_time)
    tz = _site_timezone()
    today = review_time.astimezone(tz).date()
    result: dict[str, dict] = {}
    for rating in RATINGS:
        due, interval_days = _preview_outcome(fsrs_card, rating, review_time)
        label = ""
        if due > review_time:
            seconds = max(0, int((due - review_time).total_seconds()))
            due_day = due.astimezone(tz).date()
            day_gap = (due_day - today).days
            label = f"{day_gap} 天" if day_gap >= 1 else _interval_label(seconds)
        result[rating] = {
            "label": label,
            "due_at": _naive_utc(due).isoformat(),
            "interval_days": interval_days,
        }
    return result


def _retrievability(stability_days: float, elapsed_days: float) -> float:
    """旧算法遗留的指数遗忘曲线，仅用于尚无 fsrs_state 的卡片。"""
    if stability_days <= 0:
        return 1.0
    return 0.9 ** (elapsed_days / stability_days)


def memory_curve(cards, days: int = 30, now: dt.datetime | None = None) -> list[dict]:
    """用 FSRS retrievability 估算未来 days 天的平均可回忆率。"""
    start = _aware(now)
    # 先逐卡解析一次 FSRS 状态：此前每天重复 from_json 反序列化，
    # 30 天 × N 张卡是 O(30N) 次 JSON 解析。
    prepared: list[tuple] = []
    for card in cards:
        due = getattr(card, "due_at", None)
        if due is None:
            continue
        fsrs_card = None
        try:
            candidate = _fsrs_card_from_model(card, now=start)
            if candidate.last_review is not None and candidate.stability is not None:
                fsrs_card = candidate
        except (TypeError, ValueError, KeyError):
            pass
        prepared.append(
            (fsrs_card, due, float(getattr(card, "interval_days", 0) or 0))
        )
    points: list[dict] = []
    for day_index in range(days):
        target = start + dt.timedelta(days=day_index)
        values: list[float] = []
        for fsrs_card, due, interval in prepared:
            if fsrs_card is not None:
                recall = _FSRS.get_card_retrievability(
                    fsrs_card, current_datetime=target
                )
                values.append(max(0.0, min(1.0, recall)))
                continue
            if interval <= 0:
                continue
            elapsed = interval + (target - _aware(due, start)).total_seconds() / 86_400
            values.append(_retrievability(interval, max(0.0, elapsed)))
        recall = round(sum(values) / len(values) * 100, 1) if values else None
        points.append({"date": target.date().isoformat(), "recall": recall})
    return points


def forecast_due_counts(cards, days: int = 30, now: dt.datetime | None = None) -> Counter:
    """按每张卡当前 FSRS 状态模拟未来 30 天到期量（每次复习都点“认识”）。"""
    start = _aware(now)
    tz = _site_timezone()
    today = start.astimezone(tz).date()
    simulations: list[tuple[FSRSCard, dt.date]] = []
    for card in cards:
        if getattr(card, "due_at", None) is None:
            continue
        try:
            fsrs_card = _fsrs_card_from_model(card, now=start)
        except (TypeError, ValueError, KeyError):
            continue
        due = fsrs_card.due.astimezone(tz).date()
        simulations.append((fsrs_card, due))
    counts: Counter = Counter()
    for day_index in range(days):
        day = today + dt.timedelta(days=day_index)
        counts[day.isoformat()] = 0
        next_round: list[tuple[FSRSCard, dt.date]] = []
        for fsrs_card, due in simulations:
            if due <= day:
                counts[day.isoformat()] += 1
                review_time = dt.datetime.combine(
                    day, dt.time(), tzinfo=tz
                ).astimezone(dt.timezone.utc)
                updated, _ = _FSRS.review_card(
                    fsrs_card, Rating.Good, review_datetime=review_time
                )
                # 与真实写入路径（_sync_model_from_fsrs）一致：间隔 ≥1 天的
                # 到期对齐到站点午夜，否则预测与实际队列有 1-2 天系统性偏差。
                if updated.state not in (State.Learning, State.Relearning):
                    updated.due = _align_due(updated.due, review_time)
                next_round.append((updated, updated.due.astimezone(tz).date()))
            else:
                next_round.append((fsrs_card, due))
        simulations = next_round
    return counts
