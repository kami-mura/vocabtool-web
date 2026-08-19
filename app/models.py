from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)

from .db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    salt = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=_utcnow)


class UserApiCredential(Base):
    """用户自带的 API Key；只保存服务端加密后的密文与尾号提示。"""

    __tablename__ = "user_api_credentials"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider = Column(String(20), default="deepseek", nullable=False)
    encrypted_key = Column(Text, nullable=False)
    key_hint = Column(String(4), nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class VocabularyProfile(Base):
    """每个用户自己的 NGSL 基础词汇量范围。"""

    __tablename__ = "vocabulary_profiles"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    ngsl_known_rank = Column(Integer, default=3000, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class StorageUsage(Base):
    """每用户已用存储字节数的缓存；过期后由全量统计重算回填。"""

    __tablename__ = "storage_usage"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    used_bytes = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, nullable=False)


class ReadingVocabularyPreference(Base):
    """阅读中自动显示的下一档 NGSL Hard 窗口。"""

    __tablename__ = "reading_vocabulary_preferences"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    hard_window_size = Column(Integer, default=1000, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class ReadingDisplayPreference(Base):
    """每个用户同步的阅读书页排版设置。"""

    __tablename__ = "reading_display_preferences"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    font_family = Column(String(32), default="book", nullable=False)
    font_size = Column(Integer, default=17, nullable=False)
    page_margin = Column(Integer, default=36, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class ReviewPreference(Base):
    """每个用户自己的 Anki 式每日学习设置。"""

    __tablename__ = "review_preferences"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    new_cards_per_day = Column(Integer, default=10, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class SentenceRefreshState(Base):
    """旧卡句子修复的节流状态，持久化到数据库而不是进程内字典。"""

    __tablename__ = "sentence_refresh_state"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    last_run_at = Column(DateTime, default=_utcnow, nullable=False)


class SentenceRefreshPreference(Base):
    """阅读卡例句自动轮换的每用户设置。interval=0 表示关闭。"""

    __tablename__ = "sentence_refresh_preferences"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    interval = Column(Integer, default=0, nullable=False)
    enabled_at = Column(DateTime, nullable=True)


class DailyNewAssignment(Base):
    """每天零点后固定抽取的新卡，刷新页面不会换一批；is_extra 标记加学卡。"""

    __tablename__ = "daily_new_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "day", "card_id", name="uq_daily_new_card"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    day = Column(String(10), index=True, nullable=False)
    card_id = Column(
        Integer, ForeignKey("cards.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at = Column(DateTime, default=_utcnow)
    is_extra = Column(Boolean, default=False, nullable=False)


class Session(Base):
    __tablename__ = "sessions"

    token = Column(String(64), primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow)


class EmailVerification(Base):
    __tablename__ = "email_verifications"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), index=True, nullable=False)
    code_hash = Column(String(64), nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    consumed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)


class PasswordResetVerification(Base):
    """与注册验证码隔离，防止验证码跨用途使用。"""

    __tablename__ = "password_reset_verifications"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    code_hash = Column(String(64), nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    consumed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)


class LoginThrottle(Base):
    __tablename__ = "login_throttle"

    email = Column(String(255), primary_key=True)
    failures = Column(Integer, default=0, nullable=False)
    window_started = Column(DateTime, default=_utcnow, nullable=False)
    locked_until = Column(DateTime, nullable=True)


class EmailNoticeThrottle(Base):
    """不含验证码的提醒邮件按邮箱节流：防止借邮件服务轰炸任意第三方邮箱。"""

    __tablename__ = "email_notice_throttle"

    email = Column(String(255), primary_key=True)
    attempts = Column(Integer, default=0, nullable=False)
    window_started = Column(DateTime, default=_utcnow, nullable=False)


class RequestThrottle(Base):
    """对昂贵的匿名操作做短窗口限流；key 只保存不可逆摘要。"""

    __tablename__ = "request_throttle"

    key = Column(String(128), primary_key=True)
    attempts = Column(Integer, default=0, nullable=False)
    window_started = Column(DateTime, default=_utcnow, nullable=False)


class GuestLookupQuota(Base):
    """未登录用户查词体验次数；key 为匿名身份摘要，不存原始 IP。"""

    __tablename__ = "guest_lookup_quota"

    key = Column(String(128), primary_key=True)
    count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class GuestAiQuota(Base):
    """全站未登录用户每日 AI 查词总量；按天原子计数，防止换 IP 烧 API 额度。"""

    __tablename__ = "guest_ai_quota"

    day = Column(String(10), primary_key=True)
    count = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class Corpus(Base):
    __tablename__ = "corpora"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title = Column(String(200), nullable=False)
    raw_text = Column(Text, nullable=False)
    source_type = Column(String(30), default="paste")
    status = Column(String(20), default="ready", nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class CorpusWord(Base):
    __tablename__ = "corpus_words"
    __table_args__ = (UniqueConstraint("corpus_id", "word", name="uq_corpus_word"),)

    id = Column(Integer, primary_key=True)
    corpus_id = Column(
        Integer, ForeignKey("corpora.id", ondelete="CASCADE"), index=True, nullable=False
    )
    word = Column(String(100), index=True, nullable=False)
    count = Column(Integer, default=0, nullable=False)
    # AI 文章的目标词标记：只高亮生成这篇文章时真正使用的目标词。
    is_target = Column(Boolean, default=False, nullable=False, server_default="0")


class CorpusChapter(Base):
    """AI 短文正文的章节存储（每篇文章一章）。"""

    __tablename__ = "corpus_chapters"
    __table_args__ = (
        UniqueConstraint("corpus_id", "position", name="uq_corpus_chapter_position"),
    )

    id = Column(Integer, primary_key=True)
    corpus_id = Column(
        Integer, ForeignKey("corpora.id", ondelete="CASCADE"), index=True, nullable=False
    )
    position = Column(Integer, nullable=False)
    title = Column(String(300), nullable=False)
    text = Column(Text, nullable=False)
    word_count = Column(Integer, default=0, nullable=False)


class SavedWord(Base):
    """用户词库中的单词：三态 easy/mid/hard（mid=已制卡，自动维护）。"""

    __tablename__ = "saved_words"
    __table_args__ = (
        UniqueConstraint("user_id", "word", name="uq_saved_word_user_word"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    word = Column(String(100), index=True, nullable=False)
    status = Column(String(20), default="hard", nullable=False)  # easy/mid/hard
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (
        # 站内生成卡仍按 (user, word, type) 去重；Anki 卡例外：一个 note 的
        # 多模板（Basic+Reverse、多 Cloze ordinal）会解析出同词多卡，
        # 其身份由 (user_id, anki_guid) 唯一约束保证。
        Index(
            "uq_cards_word_type_non_anki",
            "user_id",
            "word",
            "card_type",
            unique=True,
            sqlite_where=text("card_type <> 'anki'"),
        ),
        UniqueConstraint("user_id", "anki_guid", name="uq_cards_user_anki_guid"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    word = Column(String(100), index=True, nullable=False)
    card_type = Column(String(20), nullable=False)  # general / reading / cloze
    front = Column(Text, nullable=False)
    back = Column(Text, nullable=False)
    context = Column(Text, default="")
    state = Column(String(20), default="new")  # new / learning / review
    interval_days = Column(Float, default=0.0)
    ease = Column(Float, default=2.5)
    learning_step = Column(Integer, default=0, nullable=False)
    session_reduce_day = Column(String(10), default="", nullable=False)
    session_reduce_used = Column(Integer, default=0, nullable=False)
    # FSRS-6 调度状态（py-fsrs Card 的 JSON）；NULL 表示尚未初始化。
    fsrs_state = Column(Text, nullable=True)
    # 唯一对用户可见的新旧语义：NULL 是新卡；有日期就是待复习卡。
    due_at = Column(DateTime, nullable=True, index=True)
    reps = Column(Integer, default=0)
    lapses = Column(Integer, default=0)
    revision = Column(Integer, default=0, nullable=False, server_default="0")
    buried = Column(Boolean, default=False, nullable=False, server_default="0")
    # Anki note guid + template ordinal；用于重复导入时更新同一张卡而非复制。
    anki_guid = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class ReviewLog(Base):
    __tablename__ = "review_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    card_id = Column(
        Integer, ForeignKey("cards.id", ondelete="CASCADE"), index=True, nullable=False
    )
    rating = Column(String(20), nullable=False)
    is_new = Column(Boolean, default=False)
    interval_days = Column(Float, default=0.0)
    ease = Column(Float, default=2.5)
    previous_state = Column(String(20), default="new")
    previous_due_at = Column(DateTime, nullable=True)
    previous_reps = Column(Integer, default=0)
    previous_lapses = Column(Integer, default=0)
    previous_word_status = Column(String(20), default="")
    session_pending = Column(Boolean, default=False, nullable=False)
    session_correct_streak = Column(Integer, default=0, nullable=False)
    previous_session_pending = Column(Boolean, default=False, nullable=False)
    previous_session_correct_streak = Column(Integer, default=0, nullable=False)
    previous_session_rating = Column(String(20), default="", nullable=False)
    previous_learning_step = Column(Integer, default=0, nullable=False)
    # FSRS 评分前快照与本次评分日志，用于撤回和以后的参数优化。
    previous_fsrs_state = Column(Text, nullable=True)
    fsrs_review_log = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, default=_utcnow, index=True)


class AnkiReviewLog(Base):
    """从 Anki 包保留的原始复习历史；独立存储，避免影响站内今日统计和撤回。"""

    __tablename__ = "anki_review_logs"

    source_key = Column(String(64), primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    card_id = Column(
        Integer, ForeignKey("cards.id", ondelete="CASCADE"), index=True, nullable=False
    )
    anki_review_id = Column(BigInteger, nullable=False)
    rating = Column(Integer, nullable=False)
    interval_days = Column(Float, default=0.0, nullable=False)
    last_interval_days = Column(Float, default=0.0, nullable=False)
    ease = Column(Float, default=2.5, nullable=False)
    review_type = Column(Integer, default=1, nullable=False)
    reviewed_at = Column(DateTime, nullable=False, index=True)


class ReviewRequest(Base):
    """记录客户端评分动作，防止网络重试把同一次点击应用两遍。"""

    __tablename__ = "review_requests"

    action_id = Column(String(64), primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    card_id = Column(
        Integer, ForeignKey("cards.id", ondelete="CASCADE"), index=True, nullable=False
    )
    review_log_id = Column(
        Integer, ForeignKey("review_logs.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, default=_utcnow, index=True)


class WordEntry(Base):
    """deepseek-v4-flash 释义缓存：全站共享，一个单词只生成一次。"""

    __tablename__ = "word_entries"

    word = Column(String(100), primary_key=True)
    pos = Column(String(20), default="")
    en_def = Column(Text, default="")
    zh_def = Column(Text, default="")
    source = Column(String(30), default="")
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class AiUsage(Base):
    __tablename__ = "ai_usage"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at = Column(DateTime, default=_utcnow, index=True)


class AiDailyQuota(Base):
    """每用户每日 AI 请求计数的原子计数表（跨进程安全）。"""

    __tablename__ = "ai_daily_quota"
    __table_args__ = (
        UniqueConstraint("user_id", "day", name="uq_ai_daily_quota_user_day"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    day = Column(String(10), nullable=False, index=True)
    count = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class AiFreeDailyQuota(Base):
    """平台 Key 的每用户每日免费查询与制卡额度。"""

    __tablename__ = "ai_free_daily_quota"
    __table_args__ = (
        UniqueConstraint("user_id", "day", name="uq_ai_free_daily_quota_user_day"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    day = Column(String(10), nullable=False, index=True)
    query_count = Column(Integer, default=0, nullable=False)
    card_count = Column(Integer, default=0, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class LookupCache(Base):
    """全站共享的本地词典缓存，只保存查词结果，不关联用户。"""

    __tablename__ = "lookup_cache"

    query = Column(String(80), primary_key=True)
    query_type = Column(String(20), nullable=False)
    explanation = Column(Text, nullable=False)
    card_front = Column(Text, default="")
    card_back = Column(Text, default="")
    prompt_version = Column(String(30), nullable=False)
    source = Column(String(30), default="deepseek")
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class LookupHistory(Base):
    """用户查过的单词、短语和中文释义；不保存任何 API Key。"""

    __tablename__ = "lookup_history"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    query = Column(Text, nullable=False)
    query_type = Column(String(20), nullable=False)  # word / phrase / gloss / qa
    mode = Column(
        String(10), nullable=False, default="normal", server_default="normal"
    )  # normal / quick / qa
    explanation = Column(Text, default="")
    card_front = Column(Text, default="")
    card_back = Column(Text, default="")
    card_id = Column(Integer, ForeignKey("cards.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=_utcnow, index=True)
