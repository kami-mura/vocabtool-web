import datetime as dt
import os
import sqlite3
import time

from sqlalchemy import create_engine, text

import app.db as db_mod


def _create_old_schema(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(128) NOT NULL,
            salt VARCHAR(64) NOT NULL,
            created_at DATETIME
        );
        CREATE TABLE sessions (
            token VARCHAR(64) PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at DATETIME NOT NULL,
            created_at DATETIME
        );
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            word VARCHAR(100) NOT NULL,
            card_type VARCHAR(20) NOT NULL,
            front TEXT NOT NULL,
            back TEXT NOT NULL,
            context TEXT,
            state VARCHAR(20),
            interval_days FLOAT,
            ease FLOAT,
            due_at DATETIME NOT NULL,
            reps INTEGER,
            lapses INTEGER,
            created_at DATETIME
        );
        CREATE TABLE lookup_history (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            query TEXT NOT NULL,
            query_type VARCHAR(20) NOT NULL,
            explanation TEXT DEFAULT '',
            card_front TEXT DEFAULT '',
            card_back TEXT DEFAULT '',
            card_id INTEGER,
            created_at DATETIME
        );
        CREATE TABLE review_logs (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            card_id INTEGER NOT NULL,
            rating VARCHAR(20) NOT NULL,
            is_new BOOLEAN,
            interval_days FLOAT,
            ease FLOAT,
            reviewed_at DATETIME
        );
        CREATE TABLE word_status (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            word VARCHAR(100) NOT NULL,
            status VARCHAR(20) NOT NULL,
            updated_at DATETIME,
            UNIQUE(user_id, word)
        );
        """
    )
    connection.execute(
        "INSERT INTO users (id, email, password_hash, salt) "
        "VALUES (1, 'old@example.com', 'x', 'y')"
    )
    connection.execute(
        "INSERT INTO cards (id, user_id, word, card_type, front, back, state, due_at) "
        "VALUES (1, 1, 'run', 'reading', 'run', 'back', 'new', '2026-01-01 00:00:00')"
    )
    connection.execute(
        "INSERT INTO lookup_history (id, user_id, query, query_type, card_id) "
        "VALUES (1, 1, 'word', 'word', 999)"
    )
    connection.execute(
        "INSERT INTO review_logs (user_id, card_id, rating) VALUES (1, 1, 'good')"
    )
    connection.executemany(
        "INSERT INTO word_status (user_id, word, status) VALUES (1, ?, ?)",
        [
            ("run", "unknown"),
            ("quasar", "unknown"),
            ("the", "known"),
            ("orphan", "learning"),
        ],
    )
    connection.commit()
    connection.close()


def _create_fk_orphan_schema(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(128) NOT NULL,
            salt VARCHAR(64) NOT NULL,
            created_at DATETIME
        );
        CREATE TABLE sessions (
            token VARCHAR(64) PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at DATETIME NOT NULL,
            created_at DATETIME
        );
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            word VARCHAR(100) NOT NULL,
            card_type VARCHAR(20) NOT NULL,
            front TEXT NOT NULL,
            back TEXT NOT NULL,
            context TEXT,
            state VARCHAR(20),
            interval_days FLOAT,
            ease FLOAT,
            due_at DATETIME NOT NULL,
            reps INTEGER,
            lapses INTEGER,
            created_at DATETIME
        );
        CREATE TABLE lookup_history (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            query TEXT NOT NULL,
            query_type VARCHAR(20) NOT NULL,
            explanation TEXT DEFAULT '',
            card_front TEXT DEFAULT '',
            card_back TEXT DEFAULT '',
            card_id INTEGER,
            created_at DATETIME
        );
        CREATE TABLE review_logs (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            card_id INTEGER NOT NULL,
            rating VARCHAR(20) NOT NULL,
            is_new BOOLEAN,
            interval_days FLOAT,
            ease FLOAT,
            reviewed_at DATETIME
        );
        CREATE TABLE word_status (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            word VARCHAR(100) NOT NULL,
            status VARCHAR(20) NOT NULL,
            updated_at DATETIME,
            UNIQUE(user_id, word)
        );
        """
    )
    connection.execute(
        "INSERT INTO users (id, email, password_hash, salt) "
        "VALUES (1, 'a@example.com', 'x', 'y')"
    )
    connection.execute(
        "INSERT INTO cards (id, user_id, word, card_type, front, back, state, due_at) "
        "VALUES (1, 999, 'run', 'reading', 'run', 'back', 'new', '2026-01-01 00:00:00')"
    )
    connection.commit()
    connection.close()


def test_config_numeric_parsing_errors_name_the_variable(monkeypatch):
    """数字环境变量非法时抛出指明变量名的清晰错误，而不是裸 ValueError。"""
    from app import config

    monkeypatch.setenv("NEW_CARDS_PER_DAY", "abc")
    try:
        config._env_int("NEW_CARDS_PER_DAY", 10)
    except RuntimeError as exc:
        assert "NEW_CARDS_PER_DAY" in str(exc)
        assert "abc" in str(exc)
    else:
        raise AssertionError("非法整数配置应抛出明确错误")
    assert config._env_int("SOME_UNSET_VAR", 42) == 42
    assert config._env_float("UPLOAD_BODY_TIMEOUT_SECONDS_UNSET", 1.5) == 1.5
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "2048")
    assert config._env_int("MAX_UPLOAD_BYTES", 0) == 2048


def test_old_sqlite_database_migrates_cleanly(monkeypatch, tmp_path):
    path = str(tmp_path / "old.db")
    _create_old_schema(path)
    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(db_mod, "engine", engine)

    db_mod.init_db()

    connection = sqlite3.connect(path)
    try:
        orphan = connection.execute(
            "SELECT card_id FROM lookup_history WHERE id = 1"
        ).fetchone()
        assert orphan == (None,)
        card_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(cards)")
        }
        assert "fsrs_state" in card_columns
        assert "session_reduce_day" in card_columns
        assert "session_reduce_used" in card_columns
        assert "anki_guid" in card_columns
        review_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(review_logs)")
        }
        assert "previous_state" in review_columns
        assert "previous_fsrs_state" in review_columns
        assert "fsrs_review_log" in review_columns
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "review_requests" in tables
        assert "daily_new_assignments" in tables
        assert "schema_migrations" in tables
        assert "saved_words" in tables
        assert "user_api_credentials" in tables
        assert "ai_free_daily_quota" in tables
        assert "word_status" not in tables
        assert "word_status_legacy" in tables
        legacy = connection.execute(
            "SELECT word, status FROM word_status_legacy WHERE user_id = 1"
        ).fetchall()
        assert set(legacy) == {
            ("run", "unknown"),
            ("quasar", "unknown"),
            ("the", "known"),
            ("orphan", "learning"),
        }
        saved_words = connection.execute(
            "SELECT word FROM saved_words WHERE user_id = 1"
        ).fetchall()
        assert saved_words == [("quasar",)]
        versions = {
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
        assert "009_card_due_nullable" in versions
        assert "010_ai_usage_user_created_index" in versions
        assert "019_card_session_reduce_state" in versions
        assert "020_fsrs_state" in versions
        assert "024_saved_words" in versions
        assert "026_anki_exchange" in versions
        assert "028_user_api_credentials_free_quota" in versions
        assert "029_free_article_quota" in versions
        assert "031_drop_word_status_columns" in versions
        quota_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(ai_free_daily_quota)")
        }
        assert "article_count" in quota_columns
        # 单词三态状态已整体移除：两张表都不应再有状态列。
        saved_word_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(saved_words)")
        }
        assert "status" not in saved_word_columns
        review_log_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(review_logs)")
        }
        assert "previous_word_status" not in review_log_columns
    finally:
        connection.close()


def test_word_status_migration_drops_easy_rows_without_touching_others(
    monkeypatch, tmp_path
):
    """迁移 031 必须删掉 easy 行，否则 6855 个已掌握的词会涌进生词库。

    同时校验 hard/mid 行保留、两个遗留列被删除，且重复执行幂等。
    """
    from app import models  # noqa: F401  # 注册表结构

    path = str(tmp_path / "status.db")
    engine = create_engine(f"sqlite:///{path}")
    # 先用当前模型建表（已是新结构），再手工补回旧列以模拟历史库。
    db_mod.Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE saved_words ADD COLUMN status "
                "VARCHAR(20) NOT NULL DEFAULT 'hard'"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE review_logs ADD COLUMN previous_word_status "
                "VARCHAR(20) NOT NULL DEFAULT ''"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users (id, email, password_hash, salt) "
                "VALUES (1, 'a@example.com', 'x', 'y')"
            )
        )
        for word, status in (
            ("knownword", "easy"),
            ("freshword", "hard"),
            ("cardedword", "mid"),
        ):
            connection.execute(
                text(
                    "INSERT INTO saved_words (user_id, word, status) "
                    "VALUES (1, :word, :status)"
                ),
                {"word": word, "status": status},
            )

    monkeypatch.setattr(db_mod, "engine", engine)
    db_mod.init_db()

    def _snapshot():
        connection = sqlite3.connect(path)
        try:
            return (
                {row[1] for row in connection.execute("PRAGMA table_info(saved_words)")},
                {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(review_logs)")
                },
                {row[0] for row in connection.execute("SELECT word FROM saved_words")},
            )
        finally:
            connection.close()

    saved_columns, review_columns, words = _snapshot()
    assert "status" not in saved_columns
    assert "previous_word_status" not in review_columns
    # easy 行被删除；hard 与 mid 行原样保留。
    assert words == {"freshword", "cardedword"}

    # 幂等：再跑一次不报错，数据不变。
    db_mod.init_db()
    _saved_columns, _review_columns, words_again = _snapshot()
    assert words_again == {"freshword", "cardedword"}


def test_prune_temp_uploads_removes_stale_files(monkeypatch, tmp_path):
    from app import config
    from app.db import _prune_temp_uploads

    temp_dir = tmp_path / "temp_uploads"
    temp_dir.mkdir()
    stale = temp_dir / "stale.tmp"
    fresh = temp_dir / "fresh.tmp"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"y")
    old_time = time.time() - 48 * 3600
    os.utime(stale, (old_time, old_time))

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    _prune_temp_uploads()

    assert not stale.exists()
    assert fresh.exists()


def test_prune_quota_days_use_app_timezone(monkeypatch):
    from types import SimpleNamespace

    from app import config
    from app.db import SessionLocal, _prune_ai_usage, _prune_ephemeral_rows
    from app.models import AiDailyQuota, GuestAiQuota, User

    fixed = dt.datetime(2026, 8, 13, 12, 0, 0)

    class FixedDateTime:
        @classmethod
        def now(cls, tz=None):
            return fixed

    class FixedDate:
        @classmethod
        def today(cls):
            return dt.date(2026, 8, 10)

    fake_dt = SimpleNamespace(
        datetime=FixedDateTime,
        date=FixedDate,
        timezone=dt.timezone,
        timedelta=dt.timedelta,
    )
    monkeypatch.setattr(config, "APP_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setattr(db_mod, "dt", fake_dt)

    db = SessionLocal()
    try:
        user = User(email="tz-quota@example.com", password_hash="x", salt="y")
        db.add(user)
        db.commit()
        db.add_all(
            [
                AiDailyQuota(user_id=user.id, day="2026-08-10", count=1),
                AiDailyQuota(user_id=user.id, day="2026-08-12", count=1),
                GuestAiQuota(day="2026-08-10", count=1),
            ]
        )
        db.commit()
    finally:
        db.close()

    _prune_ai_usage()
    _prune_ephemeral_rows()

    db = SessionLocal()
    try:
        days = {
            row[0]
            for row in db.query(AiDailyQuota.day).filter_by(user_id=user.id).all()
        }
        assert days == {"2026-08-12"}
        assert db.get(GuestAiQuota, "2026-08-10") is None
    finally:
        db.close()


def test_card_due_nullable_fk_failure_rolls_back(monkeypatch, tmp_path):
    path = str(tmp_path / "fk.db")
    _create_fk_orphan_schema(path)
    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(db_mod, "engine", engine)

    try:
        db_mod.init_db()
    except RuntimeError as exc:
        assert "外键检查失败" in str(exc)
    else:
        raise AssertionError("孤儿卡片的外键检查失败未抛出异常")

    connection = sqlite3.connect(path)
    try:
        due_info = connection.execute("PRAGMA table_info(cards)").fetchall()
        due_notnull = next(
            row[3] for row in due_info if row[1] == "due_at"
        )
        assert due_notnull == 1
        cards = connection.execute(
            "SELECT id, user_id, state, due_at FROM cards"
        ).fetchall()
        assert cards == [(1, 999, "new", "2026-01-01 00:00:00")]
        versions = {
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
        assert "009_card_due_nullable" not in versions
    finally:
        connection.close()


def _downgrade_cards_to_pre_027(path):
    """把已建好的 cards 表改造成迁移 027 之前的结构（含 uq_user_word_type）。

    迁移 027 的重建分支只在旧库存在该约束时才执行；测试 fixture 不带这个
    约束，等于这条会 DROP TABLE cards 的迁移从来没在 CI 里跑过。
    """
    connection = sqlite3.connect(path)
    try:
        rows = list(connection.execute("PRAGMA table_info(cards)"))
        column_names = [row[1] for row in rows]
        defs = []
        for _cid, name, coltype, notnull, default, pk in rows:
            piece = f'"{name}" {coltype or "TEXT"}'
            if notnull:
                piece += " NOT NULL"
            if default is not None:
                piece += f" DEFAULT {default}"
            if pk:
                piece += " PRIMARY KEY"
            defs.append(piece)
        defs.append("CONSTRAINT uq_user_word_type UNIQUE (user_id, word, card_type)")
        defs.append(
            "CONSTRAINT uq_cards_user_anki_guid UNIQUE (user_id, anki_guid)"
        )
        defs.append(
            "FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE"
        )
        insert_columns = ",".join(f'"{name}"' for name in column_names)
        connection.executescript(
            "CREATE TABLE cards_legacy (\n"
            + ",\n".join(defs)
            + "\n);\n"
            + f"INSERT INTO cards_legacy ({insert_columns}) "
            + f"SELECT {insert_columns} FROM cards;\n"
            + "DROP TABLE cards;\n"
            + "ALTER TABLE cards_legacy RENAME TO cards;\n"
            + "CREATE INDEX ix_cards_test_user_word ON cards (user_id, word);\n"
        )
        connection.commit()
    finally:
        connection.close()


def test_anki_multi_template_migration_rebuilds_cards_table(monkeypatch, tmp_path):
    """迁移 027 会 DROP TABLE cards 后重建，必须验证行数、外键与约束语义。

    用带 uq_user_word_type 的真实旧库 fixture 触发重建分支（否则该分支在
    CI 里永远不执行），并校验：行数与内容保留、具名索引恢复、非 anki 卡的
    唯一性继续生效、anki 卡可以同词多张、重复执行幂等。
    """
    from app import models  # noqa: F401  # 注册表结构

    path = str(tmp_path / "anki027.db")
    engine = create_engine(f"sqlite:///{path}")
    db_mod.Base.metadata.create_all(bind=engine)
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "INSERT INTO users (id, email, password_hash, salt) "
            "VALUES (1, 'anki027@example.com', 'x', 'y')"
        )
        connection.execute(
            "INSERT INTO cards (id, user_id, word, card_type, front, back, "
            "learning_step, session_reduce_day, session_reduce_used) "
            "VALUES (1, 1, 'harbor', 'general', 'harbor', '港口', 0, '', 0)"
        )
        connection.execute(
            "INSERT INTO cards (id, user_id, word, card_type, front, back, anki_guid, "
            "learning_step, session_reduce_day, session_reduce_used) "
            "VALUES (2, 1, 'lantern', 'anki', 'lantern', '灯笼', 'guid-basic', 0, '', 0)"
        )
        connection.commit()
    finally:
        connection.close()
    _downgrade_cards_to_pre_027(path)

    monkeypatch.setattr(db_mod, "engine", engine)
    db_mod.init_db()

    def _inspect():
        inspector_connection = sqlite3.connect(path)
        try:
            uniques = {
                row[1]
                for row in inspector_connection.execute("PRAGMA index_list(cards)")
                if row[2] == 1
            }
            indexes = {
                row[1] for row in inspector_connection.execute("PRAGMA index_list(cards)")
            }
            rows = sorted(
                inspector_connection.execute(
                    "SELECT id, word, card_type, anki_guid FROM cards ORDER BY id"
                ).fetchall()
            )
            violations = inspector_connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            return uniques, indexes, rows, violations
        finally:
            inspector_connection.close()

    _uniques, indexes, rows, violations = _inspect()
    # 行数与内容一字不差地保留（重建是 DROP + 复制，最怕静默丢行）
    assert rows == [
        (1, "harbor", "general", None),
        (2, "lantern", "anki", "guid-basic"),
    ]
    assert violations == []
    # 具名索引被原样恢复
    assert "ix_cards_test_user_word" in indexes

    connection = sqlite3.connect(path)
    try:
        indexes_after = {
            row[1] for row in connection.execute("PRAGMA index_list(cards)")
        }
        assert "uq_cards_word_type_non_anki" in indexes_after, (
            "重建后必须补上非 anki 卡的部分唯一索引"
        )
        # 直接证明重建分支真的跑了：旧约束必须消失
        assert not any(
            "uq_user_word_type" in str(row[1])
            for row in connection.execute("PRAGMA index_list(cards)")
        ), "uq_user_word_type 必须已被重建移除"
        # 非 anki 卡的同 (user, word, type) 重复仍被拒绝
        connection.execute(
            "INSERT INTO cards (id, user_id, word, card_type, front, back, "
            "learning_step, session_reduce_day, session_reduce_used) "
            "VALUES (10, 1, 'harbor', 'general', 'harbor', '港口', 0, '', 0)"
        )
        duplicate_rejected = False
    except sqlite3.IntegrityError:
        duplicate_rejected = True
    finally:
        connection.rollback()
        connection.close()
    assert duplicate_rejected, "非 anki 卡的同词同类型去重规则必须保持"

    connection = sqlite3.connect(path)
    try:
        # anki 卡：同一 note 的多模板允许同 (user, word, 'anki')，guid 天然区分
        connection.execute(
            "INSERT INTO cards (id, user_id, word, card_type, front, back, anki_guid, "
            "learning_step, session_reduce_day, session_reduce_used) "
            "VALUES (3, 1, 'lantern', 'anki', 'lantern', '灯笼', 'guid-reverse', 0, '', 0)"
        )
        connection.commit()
        anki_count = connection.execute(
            "SELECT COUNT(*) FROM cards WHERE card_type = 'anki' AND word = 'lantern'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert anki_count == 2, "一个 note 的多模板卡必须能同时存在"

    # 幂等：再跑一次不报错、不丢行
    db_mod.init_db()
    _uniques, _indexes, rows_again, violations_again = _inspect()
    assert violations_again == []
    assert [row[0] for row in rows_again] == [1, 2, 3]
