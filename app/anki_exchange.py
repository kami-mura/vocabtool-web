"""Legacy Anki .apkg import/export with scheduling and review-history retention.

The legacy package is deliberately used for exchange: current Anki can import it,
and its collection is a plain SQLite database that can be validated without
executing Anki or accepting executable add-ons/media from an uploaded archive.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import io
import json
import math
import re
import sqlite3
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, insert
from sqlalchemy.orm import Session

from . import config
from .models import AnkiReviewLog, Card, ReviewLog


class AnkiExchangeError(ValueError):
    pass


_COLLECTION_NAMES = ("collection.anki21", "collection.anki2")
_MAX_COLLECTION_BYTES = 128 * 1024 * 1024
_MAX_REVIEW_ROWS = 250_000
_FIELD_SEPARATOR = "\x1f"
_RATING_TO_EASE = {"again": 1, "hard": 2, "good": 3, "easy": 4}
_MARKDOWN_BOLD = re.compile(r"\*\*(.+?)\*\*")
# One-time note-format version: lets Anki refresh previously exported native
# notes with HTML highlights without changing their guid or scheduling identity.
_TARGET_WORD_FORMAT_MOD = 1_786_665_600  # 2026-08-14 00:00:00 UTC


class _TextExtractor(HTMLParser):
    _BREAKS = {"br", "div", "p", "li", "tr", "h1", "h2", "h3", "h4"}
    _SKIP = {"script", "style", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        del attrs
        tag = tag.lower()
        if tag in self._SKIP:
            self.skip_depth += 1
        elif not self.skip_depth and tag in self._BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag in self._BREAKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts).replace("\xa0", " ")
        value = re.sub(r"[ \t\r\f\v]+", " ", value)
        value = re.sub(r" *\n *", "\n", value)
        return re.sub(r"\n{3,}", "\n\n", value).strip()


def _plain_text(value: object) -> str:
    parser = _TextExtractor()
    parser.feed(str(value or ""))
    return parser.text()


def _html_field(value: object, *, markdown: bool = False) -> str:
    escaped = html.escape(str(value or ""))
    if markdown:
        escaped = _MARKDOWN_BOLD.sub(r'<span class="target-word">\1</span>', escaped)
    return escaped.replace("\n", "<br>")


def _site_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(config.APP_TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Shanghai")


def _naive_utc_from_timestamp(value: int) -> dt.datetime:
    if value < 0 or value > 4_000_000_000:
        raise AnkiExchangeError("Anki 卡片时间格式不正确")
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).replace(tzinfo=None)


def _review_datetime(review_id: int) -> dt.datetime:
    # Anki revlog ids are millisecond timestamps. Reject implausible values from
    # an untrusted package instead of letting platform datetime conversion fail.
    if review_id < 100_000_000_000 or review_id > 4_000_000_000_000:
        raise AnkiExchangeError("Anki 复习历史时间格式不正确")
    return dt.datetime.fromtimestamp(review_id / 1000, dt.timezone.utc).replace(tzinfo=None)


def _timestamp(value: dt.datetime | None, fallback: int = 0) -> int:
    if value is None:
        return fallback
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return int(value.timestamp())


def _interval_days(value: object) -> float:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if number < 0:
        return min(36500.0, abs(number) / 86400.0)
    return min(36500.0, float(number))


def _safe_json_object(raw: object) -> dict[str, object]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _model_field_names(models: dict[str, object], model_id: object) -> list[str]:
    model = models.get(str(model_id))
    if not isinstance(model, dict):
        return []
    fields = model.get("flds")
    if not isinstance(fields, list):
        return []
    ordered = sorted(
        (field for field in fields if isinstance(field, dict)),
        key=lambda field: int(field.get("ord", 0) or 0),
    )
    return [str(field.get("name") or "") for field in ordered]


def _field_map(names: list[str], values: list[str]) -> dict[str, str]:
    return {
        names[index].strip().lower(): values[index]
        for index in range(min(len(names), len(values)))
        if names[index].strip()
    }


def _read_collection_bytes(data: bytes) -> tuple[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 20_000:
                raise AnkiExchangeError("Anki 包内文件数量过多")
            names = set(archive.namelist())
            if "collection.anki21b" in names:
                raise AnkiExchangeError(
                    "暂不支持 Anki 新版压缩包；请导出时勾选“支持旧版 Anki”后重试"
                )
            collection_name = next((name for name in _COLLECTION_NAMES if name in names), None)
            if not collection_name:
                raise AnkiExchangeError("文件中没有可识别的 Anki 集合")
            info = archive.getinfo(collection_name)
            if info.file_size > _MAX_COLLECTION_BYTES:
                raise AnkiExchangeError("Anki 集合解压后过大")
            with archive.open(collection_name) as source:
                collection = source.read(_MAX_COLLECTION_BYTES + 1)
    except zipfile.BadZipFile as exc:
        raise AnkiExchangeError("文件不是有效的 .apkg 包") from exc
    if len(collection) > _MAX_COLLECTION_BYTES:
        raise AnkiExchangeError("Anki 集合解压后过大")
    return collection_name, collection


def _require_columns(connection: sqlite3.Connection, table: str, names: Iterable[str]) -> None:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    available = {str(row[1]) for row in rows}
    missing = set(names) - available
    if missing:
        raise AnkiExchangeError(f"Anki 集合缺少必要字段：{table}.{sorted(missing)[0]}")


def parse_apkg(data: bytes, max_cards: int) -> dict[str, object]:
    """Parse a validated legacy package into plain data; never mutates app data."""
    _name, collection = _read_collection_bytes(data)
    with tempfile.TemporaryDirectory(prefix="vocabflow_anki_import_") as directory:
        path = Path(directory) / "collection.anki2"
        path.write_bytes(collection)
        # 临时副本可写：缺 cid 索引的异常/恶意包补建索引，避免逐块全表扫描。
        uri = f"file:{path.as_posix()}"
        try:
            connection = sqlite3.connect(uri, uri=True)
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise AnkiExchangeError("Anki 集合数据库校验失败")
            _require_columns(connection, "col", ("crt", "models", "decks"))
            _require_columns(connection, "notes", ("id", "guid", "mid", "mod", "flds"))
            _require_columns(
                connection,
                "cards",
                (
                    "id",
                    "nid",
                    "did",
                    "ord",
                    "mod",
                    "type",
                    "queue",
                    "due",
                    "ivl",
                    "factor",
                    "reps",
                    "lapses",
                ),
            )
            col = connection.execute("SELECT crt, models, decks FROM col LIMIT 1").fetchone()
            if not col:
                raise AnkiExchangeError("Anki 集合缺少元数据")
            creation_time = int(col[0] or 0)
            models = _safe_json_object(col[1])
            decks = _safe_json_object(col[2])
            deck_names = {
                str(key): str(value.get("name") or "Anki")
                for key, value in decks.items()
                if isinstance(value, dict)
            }
            rows = connection.execute(
                """
                SELECT c.id, c.nid, c.did, c.ord, c.mod, c.type, c.queue,
                       c.due, c.ivl, c.factor, c.reps, c.lapses, c.data,
                       n.guid, n.mid, n.mod, n.flds
                FROM cards c JOIN notes n ON n.id = c.nid
                ORDER BY c.id LIMIT ?
                """,
                (max_cards + 1,),
            ).fetchall()
            if len(rows) > max_cards:
                raise AnkiExchangeError(f"Anki 包卡片过多，最多支持 {max_cards} 张")
            card_ids = [int(row[0]) for row in rows]
            review_rows: dict[int, list[dict[str, object]]] = {card_id: [] for card_id in card_ids}
            if card_ids:
                _require_columns(
                    connection,
                    "revlog",
                    ("id", "cid", "ease", "ivl", "lastIvl", "factor", "type"),
                )
                index_names = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA index_list(revlog)"
                    ).fetchall()
                }
                if not index_names:
                    # 缺 cid 索引的包按 500 卡分块查询会反复全表扫描；补建索引
                    # 把最坏情况的数十次全表扫描降为一次建索引 + 20 次索引查找。
                    # 建不出来（仍只读/损坏）时静默退回原扫描路径。
                    try:
                        connection.execute("PRAGMA query_only=OFF")
                        connection.execute(
                            "CREATE INDEX IF NOT EXISTS idx_vf_import_revlog_cid "
                            "ON revlog (cid)"
                        )
                        connection.execute("PRAGMA query_only=ON")
                    except sqlite3.DatabaseError:
                        pass
                for start in range(0, len(card_ids), 500):
                    chunk = card_ids[start : start + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    remaining = _MAX_REVIEW_ROWS - sum(len(value) for value in review_rows.values())
                    reviews = connection.execute(
                        "SELECT id, cid, ease, ivl, lastIvl, factor, type "
                        f"FROM revlog WHERE cid IN ({placeholders}) ORDER BY id LIMIT ?",
                        [*chunk, remaining + 1],
                    ).fetchall()
                    if len(reviews) > remaining:
                        raise AnkiExchangeError("Anki 包复习历史过多")
                    for review in reviews:
                        review_rows[int(review[1])].append(
                            {
                                "id": int(review[0]),
                                "ease": max(1, min(4, int(review[2] or 1))),
                                "interval_days": _interval_days(review[3]),
                                "last_interval_days": _interval_days(review[4]),
                                "factor": int(review[5] or 2500),
                                "type": int(review[6] or 0),
                            }
                        )
        except sqlite3.DatabaseError as exc:
            raise AnkiExchangeError("Anki 集合数据库格式不正确") from exc
        finally:
            try:
                connection.close()
            except UnboundLocalError:
                pass

    parsed_cards: list[dict[str, object]] = []
    for row in rows:
        values = str(row[16] or "").split(_FIELD_SEPARATOR)
        names = _model_field_names(models, row[14])
        fields = _field_map(names, values)
        front = fields.get("front") or (values[0] if values else "")
        back = fields.get("back") or (values[1] if len(values) > 1 else "")
        word = fields.get("word") or front
        card_type = (fields.get("vocabflowtype") or "").strip().lower()
        if card_type not in {"general", "reading", "cloze", "speaking", "dictation"}:
            card_type = "anki"
        context = fields.get("context") or ""
        if card_type == "anki":
            context = json.dumps(
                {
                    "deck": deck_names.get(str(row[2]), "Anki"),
                    "anki_note_mod": int(row[15] or 0),
                    "anki_card_mod": int(row[4] or 0),
                },
                ensure_ascii=False,
            )
        state, due_at, learning_step = _import_schedule(
            creation_time=creation_time,
            card_type=int(row[5] or 0),
            queue=int(row[6] or 0),
            due=int(row[7] or 0),
            interval=int(row[8] or 0),
        )
        exchange_guid = (fields.get("vocabflowguid") or "").strip()
        if not exchange_guid:
            exchange_guid = f"{str(row[13])}:{int(row[3] or 0)}"
        parsed_cards.append(
            {
                "source_card_id": int(row[0]),
                "anki_guid": exchange_guid[:128],
                "word": _plain_text(word)[:100],
                "card_type": card_type,
                "front": _plain_text(front)[:100_000],
                "back": _plain_text(back)[:100_000],
                "context": _plain_text(context)[:100_000] if card_type != "anki" else context,
                "state": state,
                "due_at": due_at,
                "interval_days": min(36500.0, max(0.0, float(row[8] or 0))),
                "ease": min(10.0, max(1.3, float(row[9] or 2500) / 1000.0)),
                "learning_step": learning_step,
                "reps": min(2_000_000_000, max(0, int(row[10] or 0))),
                "lapses": min(2_000_000_000, max(0, int(row[11] or 0))),
                "buried": int(row[6] or 0) < 0,
                "modified_at": _naive_utc_from_timestamp(int(row[4] or 0)),
                "reviews": review_rows.get(int(row[0]), []),
            }
        )
    return {
        "cards": parsed_cards,
        "review_count": sum(len(card["reviews"]) for card in parsed_cards),
    }


def _import_schedule(
    *, creation_time: int, card_type: int, queue: int, due: int, interval: int
) -> tuple[str, dt.datetime | None, int]:
    if card_type == 0 and queue <= 0:
        # 暂停/掩埋的新卡（queue=-1/-2）仍是新卡：buried 已在解析层置位，
        # 此前会落进 review 分支变成“创建起就到期”的复习卡。
        return "new", None, 0
    if card_type in {1, 3} or queue in {1, 3, 4}:
        if queue == 3:
            # 跨日学习/重学卡的 due 是「集合创建起算的天数」，不是 Unix 秒；
            # 以前小数值被当成过期时间戳改成“导入时刻”，未来跨日卡立刻到期。
            base = _naive_utc_from_timestamp(creation_time)
            return "learning", base + dt.timedelta(days=max(0, due)), 0
        if due > 1_000_000_000:
            due_at = _naive_utc_from_timestamp(due)
        else:
            due_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        return "learning", due_at, 0
    if due > 365_000:
        raise AnkiExchangeError("Anki 到期时间超出支持范围")
    base = _naive_utc_from_timestamp(creation_time)
    due_at = base + dt.timedelta(days=max(0, due))
    return "review", due_at, 0


def _latest_local_progress(db: Session, card: Card) -> dt.datetime | None:
    local = (
        db.query(func.max(ReviewLog.reviewed_at))
        .filter(ReviewLog.user_id == card.user_id, ReviewLog.card_id == card.id)
        .scalar()
    )
    imported = (
        db.query(func.max(AnkiReviewLog.reviewed_at))
        .filter(AnkiReviewLog.user_id == card.user_id, AnkiReviewLog.card_id == card.id)
        .scalar()
    )
    return max((value for value in (local, imported) if value is not None), default=None)


def import_parsed(db: Session, user_id: int, parsed: dict[str, object]) -> dict[str, int]:
    """Merge parsed cards without deleting data. Caller owns the final transaction."""
    created = updated = progress_kept = conflicts = histories = 0
    cards = parsed.get("cards")
    if not isinstance(cards, list):
        raise AnkiExchangeError("Anki 解析结果不完整")

    # 预取用户全部卡片与已导入复习历史键、每卡最新进度，避免逐卡/逐条重复查询
    # （此前 1 万卡导入约产生 25 万次历史查询，会拖住唯一全局导入槽）。
    existing_cards = db.query(Card).filter(Card.user_id == user_id).all()
    by_guid: dict[str, Card] = {}
    by_word_type: dict[tuple[str, str], Card] = {}
    card_ids: list[int] = []
    for card in existing_cards:
        card_ids.append(card.id)
        if card.anki_guid:
            by_guid.setdefault(str(card.anki_guid), card)
        by_word_type.setdefault((str(card.word), str(card.card_type)), card)
    existing_review_keys: set[str] = set()
    local_progress: dict[int, dt.datetime] = {}
    imported_progress: dict[int, dt.datetime] = {}
    for start in range(0, len(card_ids), 500):
        chunk = card_ids[start : start + 500]
        existing_review_keys.update(
            str(row[0])
            for row in db.query(AnkiReviewLog.source_key)
            .filter(AnkiReviewLog.user_id == user_id, AnkiReviewLog.card_id.in_(chunk))
            .all()
        )
        for card_id, latest in (
            db.query(ReviewLog.card_id, func.max(ReviewLog.reviewed_at))
            .filter(ReviewLog.user_id == user_id, ReviewLog.card_id.in_(chunk))
            .group_by(ReviewLog.card_id)
            .all()
        ):
            local_progress[int(card_id)] = latest
        for card_id, latest in (
            db.query(AnkiReviewLog.card_id, func.max(AnkiReviewLog.reviewed_at))
            .filter(AnkiReviewLog.user_id == user_id, AnkiReviewLog.card_id.in_(chunk))
            .group_by(AnkiReviewLog.card_id)
            .all()
        ):
            imported_progress[int(card_id)] = latest

    def _latest_progress(card_id: int) -> dt.datetime | None:
        values = [
            value
            for value in (local_progress.get(card_id), imported_progress.get(card_id))
            if value is not None
        ]
        return max(values, default=None)

    pending_reviews: list[dict[str, object]] = []
    seen_guids: dict[str, tuple] = {}
    for item in cards:
        if not isinstance(item, dict) or not item.get("word") or not item.get("front"):
            conflicts += 1
            continue
        guid = str(item["anki_guid"])
        content_key = (
            str(item["word"]),
            str(item["card_type"]),
            str(item["front"]),
            str(item["back"]),
            str(item["context"]),
        )
        if guid in seen_guids:
            if seen_guids[guid] != content_key:
                conflicts += 1
            continue
        seen_guids[guid] = content_key
        card = by_guid.get(guid)
        if card is None:
            candidate = by_word_type.get(
                (str(item["word"]), str(item["card_type"]))
            )
            if candidate is not None and candidate.anki_guid not in {None, guid}:
                if str(item["card_type"]) != "anki":
                    conflicts += 1
                    continue
                # Anki 多模板 note（Basic+Reverse、多 Cloze ordinal）：同词
                # 的第二张卡 guid 含 ordinal，允许新建；唯一性由
                # (user_id, anki_guid) 约束保证。站内卡仍走冲突路径。
                candidate = None
            card = candidate
        if card is None:
            card = Card(
                user_id=user_id,
                word=str(item["word"]),
                card_type=str(item["card_type"]),
                front=str(item["front"]),
                back=str(item["back"]),
                context=str(item["context"]),
                anki_guid=guid,
            )
            db.add(card)
            db.flush()
            by_guid[guid] = card
            by_word_type[(str(item["word"]), str(item["card_type"]))] = card
            card_ids.append(card.id)
            created += 1
            apply_progress = True
        else:
            if card.anki_guid is None:
                card.anki_guid = guid
                by_guid[guid] = card
            # 已有卡片内容保持原样；交换只合并身份、进度和历史，避免导入包
            # 静默覆盖用户原有正反面或上下文。
            updated += 1
            latest = _latest_progress(card.id)
            source_modified = item.get("modified_at")
            apply_progress = latest is None or (
                isinstance(source_modified, dt.datetime) and source_modified >= latest
            )
            if latest is None and (card.reps or card.due_at is not None):
                # 极旧数据可能有排程却没有 ReviewLog。此时只有 Anki 的复习次数
                # 明确更多才视为更新，否则宁可保留站内现值。
                apply_progress = int(item["reps"]) > int(card.reps or 0)
            if not apply_progress and (card.reps or card.due_at is not None):
                progress_kept += 1
        if apply_progress:
            card.state = str(item["state"])
            card.due_at = item.get("due_at")
            card.interval_days = float(item["interval_days"])
            card.ease = float(item["ease"])
            card.learning_step = int(item["learning_step"])
            card.reps = int(item["reps"])
            card.lapses = int(item["lapses"])
            card.buried = bool(item["buried"])
            card.fsrs_state = None
            card.revision = int(card.revision or 0) + 1
        for review in item.get("reviews", []):
            review_id = int(review["id"])
            key = hashlib.sha256(f"{user_id}:{guid}:{review_id}".encode()).hexdigest()
            if key in existing_review_keys:
                continue
            existing_review_keys.add(key)
            pending_reviews.append(
                {
                    "source_key": key,
                    "user_id": user_id,
                    "card_id": card.id,
                    "anki_review_id": review_id,
                    "rating": int(review["ease"]),
                    "interval_days": float(review["interval_days"]),
                    "last_interval_days": float(review["last_interval_days"]),
                    "ease": max(1.3, int(review["factor"] or 2500) / 1000.0),
                    "review_type": int(review["type"]),
                    "reviewed_at": _review_datetime(review_id),
                }
            )
            histories += 1
    if pending_reviews:
        for start in range(0, len(pending_reviews), 1000):
            db.execute(
                insert(AnkiReviewLog),
                pending_reviews[start : start + 1000],
            )
    return {
        "created": created,
        "updated": updated,
        "progress_kept": progress_kept,
        "conflicts": conflicts,
        "histories": histories,
    }


def _stable_anki_id(value: str, base: int = 1_600_000_000_000) -> int:
    number = int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big")
    return base + number % 300_000_000_000


def _ensure_guid(card: Card) -> str:
    if card.anki_guid:
        return str(card.anki_guid)
    created = card.created_at.isoformat() if card.created_at else ""
    digest = hashlib.sha256(f"vocabflow:{card.user_id}:{card.id}:{created}".encode()).hexdigest()[
        :24
    ]
    card.anki_guid = f"vf-{digest}:0"
    return card.anki_guid


def _collection_day_start(now: dt.datetime) -> tuple[int, dt.datetime]:
    timezone = _site_timezone()
    local = now.astimezone(timezone)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp()), start.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _anki_schedule(
    card: Card, day_start: dt.datetime, new_position: int
) -> tuple[int, int, int, int]:
    if card.due_at is None:
        return 0, 0, new_position, 0
    if card.state == "learning" or float(card.interval_days or 0) < 1:
        return (
            1,
            -2 if card.buried else 1,
            _timestamp(card.due_at),
            max(0, round(card.interval_days or 0)),
        )
    due_day = card.due_at.date()
    day_number = max(0, (due_day - day_start.date()).days)
    return 2, -2 if card.buried else 2, day_number, max(1, round(card.interval_days or 1))


def _latest_card_mod(db: Session, card: Card) -> int:
    latest = _latest_local_progress(db, card)
    context = _safe_json_object(card.context) if card.card_type == "anki" else {}
    imported_mod = int(context.get("anki_card_mod", 0) or 0)
    return max(imported_mod, _timestamp(latest), _timestamp(card.created_at, 1))


def _anki_card_data(card: Card) -> str:
    """Map VocabFlow's FSRS memory state to Anki's card data JSON."""
    state = _safe_json_object(card.fsrs_state)
    try:
        stability = float(state["stability"])
        difficulty = float(state["difficulty"])
    except (KeyError, TypeError, ValueError):
        return ""
    if (
        not math.isfinite(stability)
        or not math.isfinite(difficulty)
        or stability <= 0
        or not 1 <= difficulty <= 10
    ):
        return ""
    data: dict[str, object] = {"s": stability, "d": difficulty}
    last_review = state.get("last_review")
    if isinstance(last_review, str):
        try:
            reviewed_at = dt.datetime.fromisoformat(last_review.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            data["lrt"] = _timestamp(reviewed_at)
    return json.dumps(data, separators=(",", ":"))


def _export_review_rows(db: Session, card: Card, anki_card_id: int) -> list[tuple[int, ...]]:
    result: list[tuple[int, ...]] = []
    used_ids = set()
    imported = (
        db.query(AnkiReviewLog)
        .filter(
            AnkiReviewLog.user_id == card.user_id,
            AnkiReviewLog.card_id == card.id,
        )
        .order_by(AnkiReviewLog.reviewed_at, AnkiReviewLog.anki_review_id)
        .all()
    )
    for log in imported:
        review_id = int(log.anki_review_id)
        used_ids.add(review_id)
        result.append(
            (
                review_id,
                anki_card_id,
                -1,
                int(log.rating),
                _anki_interval(log.interval_days, log.review_type),
                _anki_interval(log.last_interval_days, log.review_type),
                round(float(log.ease or 2.5) * 1000),
                0,
                int(log.review_type),
            )
        )
    local = (
        db.query(ReviewLog)
        .filter(
            ReviewLog.user_id == card.user_id,
            ReviewLog.card_id == card.id,
        )
        .order_by(ReviewLog.reviewed_at, ReviewLog.id)
        .all()
    )
    for index, log in enumerate(local):
        review_id = int(log.reviewed_at.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        while review_id in used_ids:
            review_id += 1
        used_ids.add(review_id)
        next_interval = (
            float(local[index + 1].interval_days or 0)
            if index + 1 < len(local)
            else float(card.interval_days or 0)
        )
        review_type = 0 if log.is_new else (2 if log.rating == "again" else 1)
        result.append(
            (
                review_id,
                anki_card_id,
                -1,
                _RATING_TO_EASE.get(str(log.rating), 1),
                _anki_interval(next_interval, review_type),
                _anki_interval(float(log.interval_days or 0), review_type),
                round(float(log.ease or 2.5) * 1000),
                0,
                review_type,
            )
        )
    result.sort(key=lambda row: row[0])
    return result


def _anki_interval(days: float, review_type: int) -> int:
    if review_type in {0, 2} and days < 1:
        return -max(1, round(max(0.0, days) * 86400))
    return max(1, round(days)) if days else 0


def export_apkg(db: Session, user_id: int) -> tuple[bytes, int]:
    cards = db.query(Card).filter(Card.user_id == user_id).order_by(Card.id).all()
    if len(cards) > config.MAX_APKG_CARDS:
        raise AnkiExchangeError(f"卡片过多，单次最多导出 {config.MAX_APKG_CARDS} 张")
    now = dt.datetime.now(dt.timezone.utc)
    now_seconds = int(now.timestamp())
    creation_time, day_start = _collection_day_start(now)
    deck_id = 1_600_000_001
    model_id = 1_600_000_002
    with tempfile.TemporaryDirectory(prefix="vocabflow_anki_export_") as directory:
        path = Path(directory) / "collection.anki2"
        connection = sqlite3.connect(str(path))
        try:
            _create_legacy_schema(connection)
            model = _anki_model(model_id, deck_id, now_seconds)
            deck = _anki_deck(deck_id, now_seconds)
            connection.execute(
                "INSERT INTO col VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    1,
                    creation_time,
                    now_seconds * 1000,
                    now_seconds * 1000,
                    11,
                    0,
                    0,
                    0,
                    json.dumps(_anki_conf(model_id, deck_id, len(cards) + 1)),
                    json.dumps({str(model_id): model}, ensure_ascii=False),
                    json.dumps({str(deck_id): deck}, ensure_ascii=False),
                    json.dumps({"1": _anki_dconf(now_seconds)}, ensure_ascii=False),
                    "{}",
                ),
            )
            for position, card in enumerate(cards, start=1):
                guid = _ensure_guid(card)
                note_id = _stable_anki_id(f"note:{guid}")
                card_id = _stable_anki_id(f"card:{guid}")
                note_mod = _timestamp(card.created_at, now_seconds)
                context = _safe_json_object(card.context) if card.card_type == "anki" else {}
                note_mod = max(note_mod, int(context.get("anki_note_mod", 0) or 0))
                if card.card_type != "anki":
                    note_mod = max(note_mod, _TARGET_WORD_FORMAT_MOD)
                fields = _FIELD_SEPARATOR.join(
                    (
                        _html_field(card.word),
                        _html_field(card.front, markdown=card.card_type != "anki"),
                        _html_field(card.back, markdown=card.card_type != "anki"),
                        _html_field(card.context or ""),
                        card.card_type,
                        guid,
                    )
                )
                checksum = int(hashlib.sha1(_plain_text(card.front).encode()).hexdigest()[:8], 16)
                connection.execute(
                    "INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        note_id,
                        guid.rsplit(":", 1)[0],
                        model_id,
                        note_mod,
                        -1,
                        "",
                        fields,
                        card.word,
                        checksum,
                        0,
                        "",
                    ),
                )
                anki_type, queue, due, interval = _anki_schedule(card, day_start, position)
                connection.execute(
                    "INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        card_id,
                        note_id,
                        deck_id,
                        0,
                        _latest_card_mod(db, card),
                        -1,
                        anki_type,
                        queue,
                        due,
                        interval,
                        round(float(card.ease or 2.5) * 1000),
                        max(0, int(card.reps or 0)),
                        max(0, int(card.lapses or 0)),
                        0,
                        0,
                        0,
                        0,
                        _anki_card_data(card),
                    ),
                )
                connection.executemany(
                    "INSERT OR IGNORE INTO revlog VALUES (?,?,?,?,?,?,?,?,?)",
                    _export_review_rows(db, card, card_id),
                )
            connection.commit()
        finally:
            connection.close()
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(path, "collection.anki2")
            archive.writestr("media", "{}")
    db.flush()
    return output.getvalue(), len(cards)


def _create_legacy_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE col (id integer primary key, crt integer not null, mod integer not null,
          scm integer not null, ver integer not null, dty integer not null, usn integer not null,
          ls integer not null, conf text not null, models text not null, decks text not null,
          dconf text not null, tags text not null);
        CREATE TABLE notes (id integer primary key, guid text not null, mid integer not null,
          mod integer not null, usn integer not null, tags text not null, flds text not null,
          sfld text not null, csum integer not null, flags integer not null, data text not null);
        CREATE TABLE cards (id integer primary key, nid integer not null, did integer not null,
          ord integer not null, mod integer not null, usn integer not null, type integer not null,
          queue integer not null, due integer not null, ivl integer not null, factor integer not null,
          reps integer not null, lapses integer not null, left integer not null, odue integer not null,
          odid integer not null, flags integer not null, data text not null);
        CREATE TABLE revlog (id integer primary key, cid integer not null, usn integer not null,
          ease integer not null, ivl integer not null, lastIvl integer not null,
          factor integer not null, time integer not null, type integer not null);
        CREATE TABLE graves (usn integer not null, oid integer not null, type integer not null);
        CREATE INDEX ix_notes_usn ON notes (usn);
        CREATE INDEX ix_cards_usn ON cards (usn);
        CREATE INDEX ix_cards_nid ON cards (nid);
        CREATE INDEX ix_cards_sched ON cards (did, queue, due);
        CREATE INDEX ix_revlog_usn ON revlog (usn);
        CREATE INDEX ix_revlog_cid ON revlog (cid);
        """
    )


def _anki_model(model_id: int, deck_id: int, modified: int) -> dict[str, object]:
    names = ("Word", "Front", "Back", "Context", "VocabFlowType", "VocabFlowGuid")
    return {
        "id": model_id,
        "name": "vocabtool",
        "type": 0,
        "mod": modified,
        "usn": -1,
        "sortf": 0,
        "did": None,
        "tmpls": [
            {
                "name": "Card 1",
                "ord": 0,
                "qfmt": '<div class="card-front">{{Front}}</div>',
                "afmt": '{{FrontSide}}<hr id=answer><div class="card-back">{{Back}}</div>',
                "did": None,
                "bqfmt": "",
                "bafmt": "",
                "bfont": "",
                "bsize": 0,
                "id": _stable_anki_id(f"template:{model_id}:0", base=1),
            }
        ],
        "flds": [
            {
                "name": name,
                "ord": index,
                "sticky": False,
                "rtl": False,
                "font": "Arial",
                "size": 20,
                "description": "",
                "plainText": False,
                "collapsed": False,
                "excludeFromSearch": False,
                "id": _stable_anki_id(f"field:{model_id}:{index}", base=1),
                "tag": None,
                "preventDeletion": False,
            }
            for index, name in enumerate(names)
        ],
        "css": (
            ".card { font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", "
            "Roboto, \"PingFang SC\", \"Microsoft YaHei\", sans-serif; "
            "margin: 0; padding: 24px 20px; text-align: center; "
            "background: #f3f4f6; color: #1f2937; }\n"
            ".card-front { font-size: 30px; line-height: 1.7; font-weight: 600; "
            "color: #111827; }\n"
            ".card-back { font-size: 24px; line-height: 1.65; }\n"
            "hr#answer { width: 40%; margin: 20px auto; border: none; "
            "border-top: 1px solid rgba(17, 24, 39, 0.18); }\n"
            "@media (max-width: 600px) {\n"
            ".card { padding: 16px 12px; }\n"
            ".card-front { font-size: 26px; }\n"
            ".card-back { font-size: 21px; }\n"
            "}\n"
            ".nightMode .card { background: #111827; color: #e5e7eb; }\n"
            ".nightMode .card-front { color: #f3f4f6; }\n"
            ".nightMode hr#answer { border-top-color: rgba(229, 231, 235, 0.25); }\n"
            ".target-word { color: #2f6fed; font-weight: 700; }\n"
            ".nightMode .target-word { color: #8fb0f8; }\n"
        ),
        "latexPre": "",
        "latexPost": "",
        "latexsvg": False,
        "req": [[0, "any", [1]]],
        "originalStockKind": 1,
    }


def _anki_deck(deck_id: int, modified: int) -> dict[str, object]:
    return {
        "id": deck_id,
        "name": "vocabtool",
        "mod": modified,
        "usn": -1,
        "lrnToday": [0, 0],
        "revToday": [0, 0],
        "newToday": [0, 0],
        "timeToday": [0, 0],
        "desc": "vocabtool 双向交换牌组",
        "dyn": 0,
        "collapsed": False,
        "browserCollapsed": False,
        "extendNew": 0,
        "extendRev": 0,
        "conf": 1,
        "reviewLimit": None,
        "newLimit": None,
        "reviewLimitToday": None,
        "newLimitToday": None,
        "desiredRetention": None,
    }


def _anki_dconf(modified: int) -> dict[str, object]:
    return {
        "id": 1,
        "name": "Default",
        "mod": modified,
        "usn": -1,
        "maxTaken": 60,
        "autoplay": True,
        "timer": 0,
        "replayq": True,
        "new": {
            "delays": [1, 10],
            "ints": [1, 4, 0],
            "initialFactor": 2500,
            "order": 1,
            "perDay": 20,
            "bury": True,
        },
        "rev": {
            "perDay": 200,
            "ease4": 1.3,
            "ivlFct": 1,
            "maxIvl": 36500,
            "bury": True,
            "hardFactor": 1.2,
        },
        "lapse": {"delays": [10], "mult": 0, "minInt": 1, "leechFails": 8, "leechAction": 0},
        "dyn": False,
        "newMix": 0,
        "newPerDayMinimum": 0,
        "interdayLearningMix": 0,
        "reviewOrder": 0,
        "newSortOrder": 0,
        "newGatherPriority": 0,
        "buryInterdayLearning": False,
        "fsrsWeights": [],
        "fsrsParams5": [],
        "fsrsParams6": [],
        "desiredRetention": 0.9,
        "ignoreRevlogsBeforeDate": "",
        "easyDaysPercentages": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        "stopTimerOnAnswer": False,
        "secondsToShowQuestion": 0.0,
        "secondsToShowAnswer": 0.0,
        "questionAction": 0,
        "answerAction": 0,
        "waitForAudio": True,
        "sm2Retention": 0.9,
        "weightSearch": "",
    }


def _anki_conf(model_id: int, deck_id: int, next_position: int) -> dict[str, object]:
    offset = dt.datetime.now(_site_timezone()).utcoffset() or dt.timedelta()
    return {
        "estTimes": True,
        "newSpread": 0,
        "addToCur": True,
        "activeDecks": [deck_id],
        "dayLearnFirst": False,
        "nextPos": next_position,
        "sched2021": True,
        "creationOffset": -round(offset.total_seconds() / 60),
        "timeLim": 0,
        "sortBackwards": False,
        "collapseTime": 1200,
        "dueCounts": True,
        "sortType": "noteFld",
        "curDeck": deck_id,
        "curModel": model_id,
        "schedVer": 2,
    }
