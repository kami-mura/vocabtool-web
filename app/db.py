from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Callable

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from . import config

# Python 3.12 弃用了 sqlite3 默认 datetime 适配器；注册与旧默认完全一致的
# 格式（isoformat 空格分隔），消除弃用警告且不改变存量数据的存储格式。
sqlite3.register_adapter(
    dt.datetime, lambda value: value.isoformat(sep=" ")
)

_engine_kwargs: dict = {}
_engine_kwargs["pool_pre_ping"] = True
if config.DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs["pool_recycle"] = 1800

engine = create_engine(config.DATABASE_URL, **_engine_kwargs)


if config.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _configure_sqlite(connection, _record) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()
SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)
Base = declarative_base()


def init_db() -> None:
    """创建所有表（幂等），并按版本表顺序执行一次性迁移。

    只做必要的结构工作，不做数据清理——清理挪到
    run_startup_maintenance()，由应用在后台线程执行，
    避免大库时启动被拖到 systemd 启动超时。
    """

    Base.metadata.create_all(bind=engine)
    _apply_schema_migrations()
    _ensure_runtime_indexes()
    _configure_sqlite_journal()


def run_startup_maintenance() -> None:
    """启动后的数据清理（限流表、过期会话、共享缓存等）。"""
    _prune_ai_usage()
    _prune_ephemeral_rows()
    _prune_temp_uploads()
    _prune_shared_caches()


def _apply_schema_migrations() -> None:
    """版本化迁移：每条只跑一次；函数本身仍保持幂等以兼容旧库。"""
    _ensure_schema_migrations_table()
    applied = _applied_migration_versions()
    for version, runner in _SCHEMA_MIGRATIONS:
        if version in applied:
            continue
        runner()
        _record_migration_version(version)


def _ensure_schema_migrations_table() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version VARCHAR(64) PRIMARY KEY,
                    applied_at TIMESTAMP NOT NULL
                )
                """
            )
        )


def _applied_migration_versions() -> set[str]:
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT version FROM schema_migrations")).fetchall()
    return {str(row[0]) for row in rows}


def _record_migration_version(version: str) -> None:
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO schema_migrations (version, applied_at) "
                "VALUES (:version, :applied_at)"
            ),
            {"version": version, "applied_at": now},
        )


def _migrate_ai_usage_user_created_index() -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_ai_usage_user_created_at "
                "ON ai_usage (user_id, created_at)"
            )
        )


def _migrate_anki_exchange() -> None:
    """为旧库增加稳定 Anki 身份；历史表由模型元数据幂等创建。"""
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("cards")}
    with engine.begin() as connection:
        if "anki_guid" not in columns:
            connection.execute(text("ALTER TABLE cards ADD COLUMN anki_guid VARCHAR(128)"))
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_cards_user_anki_guid "
                "ON cards (user_id, anki_guid)"
            )
        )


def _migrate_graduate_one_day_to_midnight() -> None:
    """把存量卡到期时间对齐到站点时区所在日期 0 点（Anki 日界风格）。

    旧逻辑到期是精确 N×24 小时（昨晚 23:00 学 → 今晚 23:00 到期），
    改为日界后：间隔 ≥1 天的卡，due 提前到所在日期 0 点即可复习。
    小于 1 天的间隔（学习步骤、新卡 hard 8 分钟）保持不变。
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        timezone = ZoneInfo(config.APP_TIMEZONE)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("Asia/Shanghai")
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, due_at, interval_days FROM cards
                WHERE learning_step = 0 AND due_at IS NOT NULL
                """
            )
        ).fetchall()
        for card_id, due_at, interval_days in rows:
            if due_at is None:
                continue
            try:
                interval_days = float(interval_days or 0)
            except (TypeError, ValueError):
                interval_days = 0.0
            if interval_days < 1:
                continue
            if isinstance(due_at, str):
                try:
                    due_at = dt.datetime.fromisoformat(due_at)
                except ValueError:
                    continue
            if getattr(due_at, "tzinfo", None) is None:
                due = due_at.replace(tzinfo=dt.timezone.utc)
            else:
                due = due_at.astimezone(dt.timezone.utc)
            local = due.astimezone(timezone)
            boundary = local.replace(hour=0, minute=0, second=0, microsecond=0)
            new_due = boundary.astimezone(dt.timezone.utc).replace(tzinfo=None)
            if new_due < due_at:
                connection.execute(
                    text("UPDATE cards SET due_at = :new_due WHERE id = :card_id"),
                    {"new_due": new_due, "card_id": card_id},
                )


def _migrate_card_learning_step() -> None:
    inspector = inspect(engine)
    if "cards" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("cards")}
    if "learning_step" not in existing:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE cards ADD COLUMN learning_step "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )


def _migrate_card_session_reduce_state() -> None:
    inspector = inspect(engine)
    if "cards" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("cards")}
    additions = {
        "session_reduce_day": "VARCHAR(10) NOT NULL DEFAULT ''",
        "session_reduce_used": "INTEGER NOT NULL DEFAULT 0",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in existing:
                connection.execute(
                    text(f"ALTER TABLE cards ADD COLUMN {name} {definition}")
                )


def _migrate_fsrs_state() -> None:
    """为存量库添加 FSRS 状态列，并把已有待复习卡近似初始化。"""
    from datetime import timedelta

    from fsrs import Card as FSRSCard
    from fsrs import State

    inspector = inspect(engine)
    if "cards" not in inspector.get_table_names():
        return
    card_columns = {column["name"] for column in inspector.get_columns("cards")}
    with engine.begin() as connection:
        if "fsrs_state" not in card_columns:
            connection.execute(text("ALTER TABLE cards ADD COLUMN fsrs_state TEXT"))

    if "review_logs" in inspector.get_table_names():
        review_columns = {
            column["name"] for column in inspector.get_columns("review_logs")
        }
        additions = {
            "previous_fsrs_state": "TEXT",
            "fsrs_review_log": "TEXT",
        }
        with engine.begin() as connection:
            for name, definition in additions.items():
                if name not in review_columns:
                    connection.execute(
                        text(f"ALTER TABLE review_logs ADD COLUMN {name} {definition}")
                    )

    with engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, state, interval_days, due_at, learning_step
                FROM cards
                WHERE due_at IS NOT NULL
                  AND (fsrs_state IS NULL OR fsrs_state = '')
                """
            )
        ).fetchall()
        for card_id, state, interval_days, due_at, learning_step in rows:
            if due_at is None:
                continue
            try:
                interval_days = float(interval_days or 1)
            except (TypeError, ValueError):
                interval_days = 1.0
            interval_days = max(1.0, interval_days)
            if isinstance(due_at, str):
                try:
                    due = dt.datetime.fromisoformat(due_at)
                except ValueError:
                    continue
            else:
                due = due_at
            if due.tzinfo is None:
                due = due.replace(tzinfo=dt.timezone.utc)
            else:
                due = due.astimezone(dt.timezone.utc)

            learning = str(state or "") == "learning" or int(
                learning_step or 0
            ) > 0
            if learning:
                fsrs_card = FSRSCard(
                    card_id=card_id,
                    state=State.Learning,
                    step=0,
                    stability=interval_days,
                    difficulty=5.0,
                    due=due,
                    last_review=due,
                )
            else:
                fsrs_card = FSRSCard(
                    card_id=card_id,
                    state=State.Review,
                    step=None,
                    stability=interval_days,
                    difficulty=5.0,
                    due=due,
                    last_review=due - timedelta(days=interval_days),
                )
            connection.execute(
                text("UPDATE cards SET fsrs_state = :state WHERE id = :card_id"),
                {"state": fsrs_card.to_json(), "card_id": card_id},
            )


def _migrate_review_undo_columns() -> None:
    """为已有安装补齐可撤回评分所需的调度快照列。"""
    inspector = inspect(engine)
    if "review_logs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("review_logs")}
    additions = {
        "previous_state": "VARCHAR(20) NOT NULL DEFAULT 'new'",
        "previous_due_at": "TIMESTAMP NULL",
        "previous_reps": "INTEGER NOT NULL DEFAULT 0",
        "previous_lapses": "INTEGER NOT NULL DEFAULT 0",
        "previous_word_status": "VARCHAR(20) NOT NULL DEFAULT ''",
        "session_pending": "BOOLEAN NOT NULL DEFAULT FALSE",
        "session_correct_streak": "INTEGER NOT NULL DEFAULT 0",
        "previous_session_pending": "BOOLEAN NOT NULL DEFAULT FALSE",
        "previous_session_correct_streak": "INTEGER NOT NULL DEFAULT 0",
        "previous_session_rating": "VARCHAR(20) NOT NULL DEFAULT ''",
    }
    with engine.begin() as connection:
        for name, definition in additions.items():
            if name not in existing:
                connection.execute(
                    text(f"ALTER TABLE review_logs ADD COLUMN {name} {definition}")
                )


def _migrate_review_learning_step_column() -> None:
    inspector = inspect(engine)
    if "review_logs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("review_logs")}
    if "previous_learning_step" not in existing:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE review_logs ADD COLUMN "
                    "previous_learning_step INTEGER NOT NULL DEFAULT 0"
                )
            )


def _migrate_corpus_status_columns() -> None:
    """为旧库补齐语料解析状态列。"""
    inspector = inspect(engine)
    if "corpora" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("corpora")}
    with engine.begin() as connection:
        if "status" not in existing:
            connection.execute(
                text(
                    "ALTER TABLE corpora ADD COLUMN status "
                    "VARCHAR(20) NOT NULL DEFAULT 'ready'"
                )
            )
        if "error_message" not in existing:
            connection.execute(
                text("ALTER TABLE corpora ADD COLUMN error_message TEXT")
            )


def _migrate_reading_state_paragraph_columns() -> None:
    """为阅读状态补齐段落级进度列。"""
    inspector = inspect(engine)
    if "corpus_reading_states" not in inspector.get_table_names():
        return
    existing = {
        column["name"] for column in inspector.get_columns("corpus_reading_states")
    }
    with engine.begin() as connection:
        if "paragraph_index" not in existing:
            connection.execute(
                text(
                    "ALTER TABLE corpus_reading_states ADD COLUMN "
                    "paragraph_index INTEGER NOT NULL DEFAULT 0"
                )
            )
        if "character_offset" not in existing:
            connection.execute(
                text(
                    "ALTER TABLE corpus_reading_states ADD COLUMN "
                    "character_offset INTEGER NOT NULL DEFAULT 0"
                )
            )


def _migrate_highlight_note_column() -> None:
    inspector = inspect(engine)
    if "corpus_highlights" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("corpus_highlights")}
    if "note" not in existing:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE corpus_highlights ADD COLUMN note TEXT")
            )


def _clean_orphan_lookup_history() -> None:
    """旧库可能残留指向已删除卡片的查询历史，先按模型语义置空。"""
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE lookup_history SET card_id = NULL "
                "WHERE card_id IS NOT NULL AND card_id NOT IN (SELECT id FROM cards)"
            )
        )


def _ensure_runtime_indexes() -> None:
    """create_all 不会为旧表补新增索引，因此在升级时显式补齐。"""
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_sessions_expires_at "
                "ON sessions (expires_at)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_review_logs_user_reviewed_at "
                "ON review_logs (user_id, reviewed_at)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_ai_usage_created_at "
                "ON ai_usage (created_at)"
            )
        )


def _prune_ai_usage() -> None:
    """保留最近一段时间的 AI 用量记录，避免 ai_usage 无限增长。"""
    from .models import AiDailyQuota, AiUsage

    cutoff = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=config.AI_USAGE_RETENTION_DAYS)
    with engine.begin() as connection:
        connection.execute(
            AiUsage.__table__.delete().where(AiUsage.created_at < cutoff)
        )
        # 每日计数只关心当天，最多保留 3 天即可。
        old_day = (dt.date.today() - dt.timedelta(days=2)).isoformat()
        connection.execute(
            AiDailyQuota.__table__.delete().where(AiDailyQuota.day < old_day)
        )


def _prune_ephemeral_rows() -> None:
    """清理一次性/限流表与过期会话行，防止长期运行后无限膨胀。"""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM login_throttle WHERE window_started < :cutoff"),
            {"cutoff": now - dt.timedelta(days=1)},
        )
        connection.execute(
            text("DELETE FROM request_throttle WHERE window_started < :cutoff"),
            {"cutoff": now - dt.timedelta(days=2)},
        )
        connection.execute(
            text("DELETE FROM guest_lookup_quota WHERE updated_at < :cutoff"),
            {"cutoff": now - dt.timedelta(days=365)},
        )
        connection.execute(
            text("DELETE FROM guest_ai_quota WHERE day < :old_day"),
            {"old_day": (dt.date.today() - dt.timedelta(days=2)).isoformat()},
        )
        connection.execute(
            text(
                "DELETE FROM email_verifications "
                "WHERE consumed_at IS NOT NULL OR expires_at < :now"
            ),
            {"now": now},
        )
        connection.execute(
            text(
                "DELETE FROM password_reset_verifications "
                "WHERE consumed_at IS NOT NULL OR expires_at < :now"
            ),
            {"now": now},
        )
        connection.execute(
            text("DELETE FROM sessions WHERE expires_at < :now"),
            {"now": now},
        )
        # 幂等键只需覆盖网络重试窗口，保留 90 天足够；太旧的直接清掉。
        connection.execute(
            text(
                "DELETE FROM review_requests "
                "WHERE created_at < :cutoff"
            ),
            {"cutoff": now - dt.timedelta(days=90)},
        )
def _prune_temp_uploads() -> None:
    """清理遗留的临时上传文件（进程崩溃/请求中断时产生），保留 24 小时。"""
    temp_dir = config.DATA_DIR / "temp_uploads"
    if not temp_dir.is_dir():
        return
    cutoff = dt.datetime.now().timestamp() - 24 * 3600
    try:
        for path in temp_dir.iterdir():
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        return


def _prune_shared_caches() -> None:
    """全站共享缓存（查词结果、AI 词条释义）只保留最近使用的行。"""
    from sqlalchemy import delete as sa_delete
    from sqlalchemy import select

    from .models import LookupCache, WordEntry

    with engine.begin() as connection:
        lookup_keep = (
            select(LookupCache.query)
            .order_by(LookupCache.updated_at.desc())
            .limit(50_000)
            .scalar_subquery()
        )
        connection.execute(
            sa_delete(LookupCache).where(LookupCache.query.notin_(lookup_keep))
        )
        entry_keep = (
            select(WordEntry.word)
            .order_by(WordEntry.updated_at.desc())
            .limit(100_000)
            .scalar_subquery()
        )
        connection.execute(sa_delete(WordEntry).where(WordEntry.word.notin_(entry_keep)))


def _migrate_card_due_nullable() -> None:
    """把旧版“state=new + 当前时间”迁为 due_at=NULL。"""
    inspector = inspect(engine)
    if "cards" not in inspector.get_table_names():
        return
    due_column = next(
        (column for column in inspector.get_columns("cards") if column["name"] == "due_at"),
        None,
    )
    if due_column and due_column.get("nullable"):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE cards SET due_at = NULL WHERE state = 'new' AND reps = 0")
            )
        return
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE cards ALTER COLUMN due_at DROP NOT NULL"))
            connection.execute(
                text("UPDATE cards SET due_at = NULL WHERE state = 'new' AND reps = 0")
            )
        return
    if engine.dialect.name != "sqlite":
        return

    raw = engine.raw_connection()
    try:
        raw.commit()
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("BEGIN IMMEDIATE")
        raw.execute(
            """
            CREATE TABLE cards_new (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                word VARCHAR(100) NOT NULL,
                card_type VARCHAR(20) NOT NULL,
                front TEXT NOT NULL,
                back TEXT NOT NULL,
                context TEXT,
                state VARCHAR(20),
                interval_days FLOAT,
                ease FLOAT,
                learning_step INTEGER NOT NULL DEFAULT 0,
                due_at DATETIME NULL,
                reps INTEGER,
                lapses INTEGER,
                created_at DATETIME,
                CONSTRAINT uq_user_word_type UNIQUE (user_id, word, card_type)
            )
            """
        )
        raw.execute(
            """
            INSERT INTO cards_new (
                id, user_id, word, card_type, front, back, context, state,
                interval_days, ease, learning_step, due_at,
                reps, lapses, created_at
            )
            SELECT id, user_id, word, card_type, front, back, context, state,
                   interval_days, ease, COALESCE(learning_step, 0),
                   CASE WHEN state = 'new' AND COALESCE(reps, 0) = 0 THEN NULL ELSE due_at END,
                   reps, lapses, created_at
            FROM cards
            """
        )
        raw.execute("DROP TABLE cards")
        raw.execute("ALTER TABLE cards_new RENAME TO cards")
        raw.execute("CREATE INDEX ix_cards_user_id ON cards (user_id)")
        raw.execute("CREATE INDEX ix_cards_word ON cards (word)")
        raw.execute("CREATE INDEX ix_cards_due_at ON cards (due_at)")
        raw.commit()
        violations = list(raw.execute("PRAGMA foreign_key_check"))
        if violations:
            raise RuntimeError("卡片日期迁移后的外键检查失败")
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.execute("PRAGMA foreign_keys=ON")
        raw.close()


def _migrate_lookup_history_mode() -> None:
    """为查询历史补齐来源模式列（normal/quick/qa），旧记录默认归入简洁查词。"""
    inspector = inspect(engine)
    if "lookup_history" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("lookup_history")}
    if "mode" not in existing:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE lookup_history ADD COLUMN mode "
                    "VARCHAR(10) NOT NULL DEFAULT 'normal'"
                )
            )


def _migrate_card_buried() -> None:
    """为卡片补齐掩埋列：掩埋的卡不进入今日学习，随时可恢复。"""
    inspector = inspect(engine)
    if "cards" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("cards")}
    if "buried" not in existing:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE cards ADD COLUMN buried "
                    "BOOLEAN NOT NULL DEFAULT 0"
                )
            )


def _migrate_cloze_back_format() -> None:
    """Cloze 卡背面统一为「完整句子 + 释义」：旧格式是「释义 + 原句：句子」，
    新版句子在前；存量卡一次性重排。"""
    with engine.begin() as connection:
        rows = connection.execute(
            text("SELECT id, back, context FROM cards WHERE card_type = 'cloze'")
        ).fetchall()
    for row in rows:
        back = str(row.back or "")
        if "原句：" not in back:
            continue
        rest = back.split("原句：", 1)[0].rstrip()
        if not (row.context and rest):
            continue
        new_back = f"{row.context}\n\n{rest}"
        if new_back != back:
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE cards SET back = :back WHERE id = :card_id"),
                    {"back": new_back, "card_id": row.id},
                )


def _migrate_cloze_back_with_sentence() -> None:
    """补全旧工作台制卡的 Cloze 背面：只有「单词 + 释义」、没有完整句子的，
    统一改为「完整句子 + 释义」。"""
    with engine.begin() as connection:
        rows = connection.execute(
            text("SELECT id, back, context, word FROM cards WHERE card_type = 'cloze'")
        ).fetchall()
    for row in rows:
        back = str(row.back or "")
        context = str(row.context or "") if row.context else ""
        if not context or context in back:
            continue
        lines = back.split("\n")
        if lines and lines[0].strip().lower() == str(row.word or "").strip().lower():
            lines = lines[1:]
        rest = "\n".join(lines).strip()
        if not rest:
            continue
        new_back = f"{context}\n\n{rest}"
        if new_back != back:
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE cards SET back = :back WHERE id = :card_id"),
                    {"back": new_back, "card_id": row.id},
                )


def _migrate_cloze_back_highlight() -> None:
    """存量 Cloze 卡：背面首行（完整句子）里的目标词加上 ** 高亮。"""
    from .card_builder import sentence_front

    with engine.begin() as connection:
        rows = connection.execute(
            text("SELECT id, back, word FROM cards WHERE card_type = 'cloze'")
        ).fetchall()
    for row in rows:
        back = str(row.back or "")
        parts = back.split("\n\n", 1)
        sentence = parts[0].strip()
        if not sentence or "**" in sentence:
            continue
        highlighted = sentence_front(sentence, str(row.word or ""), cloze=False)
        if highlighted == sentence:
            continue
        new_back = highlighted + ("\n\n" + parts[1] if len(parts) > 1 else "")
        if new_back != back:
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE cards SET back = :back WHERE id = :card_id"),
                    {"back": new_back, "card_id": row.id},
                )


def _migrate_daily_new_assignment_extra() -> None:
    """为每日新卡分配表补齐加学标记：加学卡持久化后刷新不丢失。"""
    inspector = inspect(engine)
    if "daily_new_assignments" not in inspector.get_table_names():
        return
    existing = {
        column["name"]
        for column in inspector.get_columns("daily_new_assignments")
    }
    if "is_extra" not in existing:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE daily_new_assignments "
                    "ADD COLUMN is_extra BOOLEAN NOT NULL DEFAULT 0"
                )
            )


def _migrate_corpus_word_target_column() -> None:
    """为旧库补齐 AI 文章目标词标记列。"""
    inspector = inspect(engine)
    if "corpus_words" not in inspector.get_table_names():
        return
    existing = {
        column["name"] for column in inspector.get_columns("corpus_words")
    }
    if "is_target" not in existing:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE corpus_words ADD COLUMN "
                    "is_target BOOLEAN NOT NULL DEFAULT 0"
                )
            )


def _migrate_card_revision() -> None:
    """为复习卡增加乐观锁版本，防止多标签页重复应用评分。"""
    inspector = inspect(engine)
    if "cards" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("cards")}
    if "revision" not in existing:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE cards ADD COLUMN revision "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )


def _migrate_saved_word_status() -> None:
    """为 saved_words 增加三态 status 列（easy/mid/hard），存量词默认 hard。"""
    inspector = inspect(engine)
    if "saved_words" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("saved_words")}
    if "status" not in existing:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE saved_words ADD COLUMN status "
                    "VARCHAR(20) NOT NULL DEFAULT 'hard'"
                )
            )


def _migrate_saved_words() -> None:
    """把旧 Hard 无卡词迁入生词库，并移除 Easy/Mid/Hard 分类表。"""
    inspector = inspect(engine)
    if "word_status" not in inspector.get_table_names():
        return

    from . import vocab

    with engine.begin() as connection:
        saved_columns = {
            column["name"]
            for column in inspect(engine).get_columns("saved_words")
        } if "saved_words" in inspector.get_table_names() else set()
        status_available = "status" in saved_columns
        status_rows = connection.execute(
            text("SELECT user_id, word, status, updated_at FROM word_status")
        ).fetchall()
        card_rows = connection.execute(text("SELECT user_id, word FROM cards")).fetchall()
        profile_users = {
            int(row[0])
            for row in connection.execute(
                text("SELECT user_id FROM vocabulary_profiles")
            ).fetchall()
        }

        card_keys = {
            (int(user_id), str(word or "").split(" [", 1)[0])
            for user_id, word in card_rows
        }
        for user_id, word, status, updated_at in status_rows:
            user_id = int(user_id)
            word = str(word or "").strip()
            if status == "unknown" and word and (user_id, word) not in card_keys:
                if status_available:
                    connection.execute(
                        text(
                            "INSERT INTO saved_words (user_id, word, status, updated_at) "
                            "VALUES (:user_id, :word, 'hard', :updated_at) "
                            "ON CONFLICT (user_id, word) DO NOTHING"
                        ),
                        {"user_id": user_id, "word": word, "updated_at": updated_at},
                    )
                else:
                    connection.execute(
                        text(
                            "INSERT INTO saved_words (user_id, word, updated_at) "
                            "VALUES (:user_id, :word, :updated_at) "
                            "ON CONFLICT (user_id, word) DO NOTHING"
                        ),
                        {"user_id": user_id, "word": word, "updated_at": updated_at},
                    )

        by_user: dict[int, set[int]] = {}
        for user_id, word, status, _updated_at in status_rows:
            if status != "known":
                continue
            rank = vocab.rank_of(str(word or ""))
            if rank:
                by_user.setdefault(int(user_id), set()).add(rank)
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        for user_id, ranks in by_user.items():
            if user_id in profile_users:
                continue
            inferred = config.DEFAULT_KNOWN_RANK
            while inferred < 31_000 and inferred + 1 in ranks:
                inferred += 1
            connection.execute(
                text(
                    "INSERT INTO vocabulary_profiles "
                    "(user_id, ngsl_known_rank, updated_at) "
                    "VALUES (:user_id, :known_rank, :updated_at) "
                    "ON CONFLICT (user_id) DO NOTHING"
                ),
                {"user_id": user_id, "known_rank": inferred, "updated_at": now},
            )
        connection.execute(text("DROP TABLE word_status"))


def reserve_sqlite_write(db: Session) -> None:
    """在任何读取前取得 SQLite 写锁，避免读事务升级写锁时立即失败。

    只允许在业务写入开始前调用；已有事务只包含认证/限流前置读取，
    先提交结束旧快照，再用 BEGIN IMMEDIATE 等待当前短写事务完成。
    PostgreSQL 由行锁处理并发，不需要这一层。
    """
    if not config.DATABASE_URL.startswith("sqlite"):
        return
    if db.in_transaction():
        db.commit()
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")


def is_sqlite_busy_error(exc: Exception) -> bool:
    """识别可安全重试的 SQLite 写锁冲突。"""
    return config.DATABASE_URL.startswith("sqlite") and "database is locked" in str(
        exc
    ).lower()


def _configure_sqlite_journal() -> None:
    """WAL 允许读取与短写入并发，适合当前单机部署。"""
    if not config.DATABASE_URL.startswith("sqlite"):
        return
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("PRAGMA synchronous=NORMAL")


# 一次性 schema 变更按版本顺序登记；新增迁移只往列表末尾追加。
_SCHEMA_MIGRATIONS: list[tuple[str, Callable[[], None]]] = [
    ("002_card_learning_step", _migrate_card_learning_step),
    ("003_review_undo_columns", _migrate_review_undo_columns),
    ("004_review_learning_step_column", _migrate_review_learning_step_column),
    ("005_corpus_status_columns", _migrate_corpus_status_columns),
    ("006_reading_state_paragraph_columns", _migrate_reading_state_paragraph_columns),
    ("007_highlight_note_column", _migrate_highlight_note_column),
    ("008_clean_orphan_lookup_history", _clean_orphan_lookup_history),
    ("009_card_due_nullable", _migrate_card_due_nullable),
    ("010_ai_usage_user_created_index", _migrate_ai_usage_user_created_index),
    ("011_graduate_one_day_midnight", _migrate_graduate_one_day_to_midnight),
    ("012_graduate_align_interval_days", _migrate_graduate_one_day_to_midnight),
    ("013_lookup_history_mode", _migrate_lookup_history_mode),
    ("014_card_buried", _migrate_card_buried),
    ("016_cloze_back_format", _migrate_cloze_back_format),
    ("017_cloze_back_with_sentence", _migrate_cloze_back_with_sentence),
    ("018_cloze_back_highlight", _migrate_cloze_back_highlight),
    ("019_card_session_reduce_state", _migrate_card_session_reduce_state),
    ("020_fsrs_state", _migrate_fsrs_state),
    ("021_daily_new_assignment_extra", _migrate_daily_new_assignment_extra),
    ("022_corpus_word_target", _migrate_corpus_word_target_column),
    ("023_card_revision", _migrate_card_revision),
    ("024_saved_words", _migrate_saved_words),
    ("025_saved_word_status", _migrate_saved_word_status),
    ("026_anki_exchange", _migrate_anki_exchange),
]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
