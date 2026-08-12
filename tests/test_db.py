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
        assert "word_status" not in tables
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
