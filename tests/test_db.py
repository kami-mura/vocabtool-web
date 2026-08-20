import datetime as dt
import os
import sqlite3
import time

from sqlalchemy import create_engine

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
        assert "anki_review_logs" in tables
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
        quota_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(ai_free_daily_quota)")
        }
        assert "article_count" in quota_columns
    finally:
        connection.close()


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
