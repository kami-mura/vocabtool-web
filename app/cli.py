from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from .db import SessionLocal, init_db
from .models import SentenceRefreshPreference, User
from .sentence_refresh import run_refresh_for_user

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _cmd_refresh_reading_sentences(limit: int, recent_hours: int) -> int:
    init_db()
    db = SessionLocal()
    try:
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        enabled_users = (
            db.query(SentenceRefreshPreference.user_id)
            .filter(
                SentenceRefreshPreference.interval > 0,
                SentenceRefreshPreference.enabled_at.is_not(None),
            )
            .all()
        )
        total_updated = 0
        for (user_id,) in enabled_users:
            updated, errors = run_refresh_for_user(
                db,
                user_id,
                limit=limit,
                recent_hours=recent_hours,
                now=now,
            )
            if updated:
                logger.info("user=%s updated=%d", user_id, updated)
            if errors:
                for err in errors:
                    logger.warning("user=%s refresh_error: %s", user_id, err)
            total_updated += updated
        logger.info("total_updated=%d users=%d", total_updated, len(enabled_users))
        return 0
    except Exception:
        db.rollback()
        logger.exception("刷新阅读卡例句失败")
        return 1
    finally:
        db.close()


def _cmd_list_users() -> int:
    init_db()
    db = SessionLocal()
    try:
        for user in db.query(User).order_by(User.id).all():
            pref = (
                db.query(SentenceRefreshPreference)
                .filter(SentenceRefreshPreference.user_id == user.id)
                .first()
            )
            interval = pref.interval if pref else 0
            enabled_at = pref.enabled_at if pref else None
            print(f"{user.id}\t{user.email}\tinterval={interval}\tenabled_at={enabled_at}")
        return 0
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    refresh_parser = subparsers.add_parser(
        "refresh-reading-sentences",
        help="按用户设置自动刷新阅读卡例句",
    )
    refresh_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="每用户每轮最多刷新多少张卡（默认 10）",
    )
    refresh_parser.add_argument(
        "--recent-hours",
        type=int,
        default=48,
        help="只考虑最近 N 小时内有学习记录的卡（默认 48）",
    )

    subparsers.add_parser("list-users", help="列出用户及其例句轮换设置")

    args = parser.parse_args(argv)
    _setup_logging()

    if args.command == "refresh-reading-sentences":
        return _cmd_refresh_reading_sentences(args.limit, args.recent_hours)
    if args.command == "list-users":
        return _cmd_list_users()
    return 2


if __name__ == "__main__":
    sys.exit(main())
