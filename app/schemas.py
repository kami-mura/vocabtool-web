"""API 请求模型（Pydantic），从 api_support 拆出。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from . import config


class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=10, max_length=128)
    code: str = Field(default="", max_length=6)


class VerificationCodeIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class PasswordResetIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=10, max_length=128)
    code: str = Field(min_length=6, max_length=6)


class ReadingDisplayIn(BaseModel):
    font_family: str = Field(default="book", max_length=32)
    font_size: int = Field(default=17, ge=14, le=24)
    page_margin: int = Field(default=36, ge=18, le=64)


class CardsBatchDeleteIn(BaseModel):
    words: list[str] = Field(default=[], max_length=2000)
    card_ids: list[int] = Field(default=[], max_length=2000)


class CardTargetsIn(BaseModel):
    source: str
    corpus_id: int | None = None
    list_id: str = Field(default="", max_length=32)
    text: str = Field(default="", max_length=1_000_000)
    from_rank: int = Field(default=3001, ge=1, le=100_000)
    to_rank: int = Field(default=5000, ge=1, le=100_000)
    count: int = Field(default=100, ge=1, le=5000)
    randomize: bool = False
    include_unknown: bool = False
    ngsl_filter: bool = False
    card_type: str = "reading"


class CardStudioCreateIn(BaseModel):
    words: list[str] = Field(min_length=1, max_length=config.MAX_CARDS_PER_RUN)
    card_type: str = "reading"
    corpus_id: int | None = None


class ReviewIn(BaseModel):
    rating: str
    practice: bool = False
    session_repeat: bool = False
    action_id: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")
    expected_revision: int | None = Field(default=None, ge=0)


class ReviewBatchItemIn(BaseModel):
    card_id: int
    rating: str
    session_repeat: bool = False
    action_id: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_-]*$")
    expected_revision: int | None = Field(default=None, ge=0)


class ReviewBatchIn(BaseModel):
    ratings: list[ReviewBatchItemIn] = Field(default_factory=list, max_length=500)


class ReviewSettingsIn(BaseModel):
    new_cards_per_day: int = Field(ge=0, le=200)


class WordBatchDeleteIn(BaseModel):
    words: list[str] = Field(default=[], max_length=2000)


class WordBatchStatusIn(BaseModel):
    words: list[str] = Field(default=[], max_length=5000)
    status: str = Field(pattern="^(easy|mid|hard)$")
    preview: bool = False


class LookupIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class QuickLookupIn(BaseModel):
    text: str = Field(min_length=1, max_length=80)


class QuestionIn(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


class TopicWordsIn(BaseModel):
    topic: str = Field(min_length=1, max_length=80)
    count: int = Field(default=20, ge=1, le=50)


class PriorityWordsIn(BaseModel):
    candidates: list[str] = Field(min_length=1, max_length=800)
    count: int = Field(default=20, ge=1, le=200)


class VocabularyProfileIn(BaseModel):
    known_rank: int = Field(ge=0, le=31_000)


class VocabularyTestAnswer(BaseModel):
    word: str = Field(min_length=1, max_length=100)
    known: bool


class VocabularyTestSubmitIn(BaseModel):
    answers: list[VocabularyTestAnswer] = Field(min_length=5, max_length=200)


__all__ = [
    "RegisterIn", "VerificationCodeIn", "LoginIn", "PasswordResetIn",
    "ReadingDisplayIn",
    "CardsBatchDeleteIn", "CardTargetsIn", "CardStudioCreateIn", "ReviewIn",
    "ReviewBatchItemIn", "ReviewBatchIn", "ReviewSettingsIn",
    "LookupIn",
    "VocabularyProfileIn", "VocabularyTestSubmitIn",
    "QuickLookupIn", "QuestionIn", "TopicWordsIn",
    "PriorityWordsIn",
]
