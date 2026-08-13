from __future__ import annotations

import re

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from . import builtin_lookup, config, vocab
from .models import Card, Corpus, LookupCache, LookupHistory, WordEntry

_WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?", re.IGNORECASE)
# 旧卡补句子时扫描语料的预算：最多扫最近 2MB 正文，且每本书截取前 500KB，
# 避免卡多书大的用户每次复习都全量扫描全部语料。
_SENTENCE_CORPUS_SCAN_CHARS = 2_000_000
_SENTENCE_CORPUS_PER_BOOK_CHARS = 500_000


def definition_text(entry: WordEntry | None) -> str:
    if not entry or not (entry.en_def or entry.zh_def):
        return "暂无释义（可在卡片页点击“deepseek-v4-flash 释义”）"
    parts = []
    if entry.zh_def:
        parts.append(f"释义：{entry.zh_def}")
    if entry.en_def:
        parts.append(f"Definition: {entry.en_def}")
    if entry.pos:
        parts.append(f"（{entry.pos}）")
    return "；".join(parts)


def _replace_word(text: str, word: str, replacement: str, preserve_surface: bool = False) -> str:
    # 去掉括号注解（taxi (verb) 只按 taxi 匹配），与 is_complete_sentence 一致；
    # 否则带注解的词永远匹配不上句子里的单词，正面不会挖空/高亮。
    target = re.sub(
        r"\s+", " ", re.sub(r"[\(\（].*?[\)\）]", "", word, flags=re.IGNORECASE)
    ).strip().lower()

    def _sub(m: re.Match) -> str:
        if vocab.normalize_word(m.group(0)) != target:
            return m.group(0)
        return replacement.format(surface=m.group(0)) if preserve_surface else replacement

    if re.search(r"\s", target):
        # 多词短语：按完整短语匹配（忽略大小写），整段高亮/挖空；
        # 单词语正则无法匹配短语，会导致短语目标在正面不凸显。
        def _phrase_sub(m: re.Match) -> str:
            surface = m.group(0)
            return replacement.format(surface=surface) if preserve_surface else replacement

        pattern = r"(?<![A-Za-z])" + re.escape(target) + r"(?![A-Za-z])"
        return re.sub(pattern, _phrase_sub, text, flags=re.IGNORECASE)

    return _WORD_RE.sub(_sub, text)


def _complete_sentence(sentence: str) -> str:
    sentence = " ".join(sentence.replace("**", "").strip().split())
    if sentence and not re.search(r"[.!?][\"'’”)]?$", sentence):
        sentence += "."
    return sentence


def complete_sentence(sentence: str) -> str:
    return _complete_sentence(sentence)


def is_complete_sentence(sentence: str, word: str) -> bool:
    """卡片正面必须是包含目标词（或词形）的完整语境句，而不是孤立词。"""
    sentence = _complete_sentence(sentence)
    tokens = _WORD_RE.findall(sentence)
    if len(tokens) < 4:
        return False
    # 去掉括号注解（如 span(v) 只看 span），避免把注解字母当成目标词。
    base_word = re.sub(r"[\(\（].*?[\)\）]", "", word, flags=re.IGNORECASE).strip()
    target_tokens = _WORD_RE.findall(base_word.lower())
    if not target_tokens:
        return False
    target_norms = [vocab.normalize_word(token) for token in target_tokens]
    sentence_norms = [vocab.normalize_word(token) for token in tokens]
    width = len(target_norms)
    return any(
        sentence_norms[index : index + width] == target_norms
        for index in range(len(sentence_norms) - width + 1)
    )


def sentence_front(sentence: str, word: str, cloze: bool = False) -> str:
    sentence = _complete_sentence(sentence)
    if not is_complete_sentence(sentence, word):
        # 找不到包含目标词的完整句：阅读卡退化为加粗单词，正面仍凸显目标词；
        # 不审核 AI 生成内容，也不因此拒绝这张卡。Cloze 卡保持原句即可。
        if cloze:
            return sentence
        base = re.sub(r"[\(\（].*?[\)\）]", "", word, flags=re.IGNORECASE).strip()
        return f"**{base}**"
    return _sentence_front(sentence, word, cloze=cloze)


def _sentence_front(sentence: str, word: str, cloze: bool = False) -> str:
    replacement = "______" if cloze else "**{surface}**"
    return _replace_word(sentence, word, replacement, preserve_surface=not cloze)


def _remove_standalone_word(back: str, word: str) -> str:
    parts = back.split("\n\n", 1)
    if len(parts) == 2 and parts[0].strip().lower() == word.strip().lower():
        return parts[1].lstrip()
    return back


def refresh_sentence_cards(db: Session, user_id: int) -> int:
    """把旧语料卡升级为完整句子格式；并把历史 bug 写成句子的通用卡正面恢复为单词。"""
    changed = 0
    # 历史 bug：通用卡正面曾被写成阅读材料里的完整句子。这里只修复
    # 正面“包含”单词（被句子污染）的卡；带括号注解的合法正面不受影响。
    for card in (
        db.query(Card)
        .filter(
            Card.user_id == user_id,
            Card.card_type == "general",
            func.lower(func.trim(Card.front)) != func.lower(func.trim(Card.word)),
        )
        .all()
    ):
        if str(card.word or "").strip().lower() in str(card.front or "").lower():
            card.front = card.word
            changed += 1
    cards = (
        db.query(Card)
        .filter(
            Card.user_id == user_id,
            Card.card_type.in_(["reading", "cloze"]),
            or_(
                Card.context.is_(None),
                func.trim(Card.context) == "",
                func.lower(func.trim(Card.context)) == func.lower(func.trim(Card.word)),
                func.lower(func.trim(Card.front)) == func.lower(func.trim(Card.word)),
                # 旧版刷新把正面写成了无高亮句子/裸单词，这里重新纳入修复。
                Card.front.notlike("%**%"),
                func.lower(
                    func.trim(func.replace(func.replace(Card.front, "**", ""), " ", ""))
                )
                == func.lower(func.trim(Card.word)),
            ),
        )
        .all()
    )
    if not cards:
        if changed:
            db.commit()
        return changed
    words = {card.word for card in cards}
    histories: dict[str, LookupHistory] = {}
    word_list = list(words)
    for index in range(0, len(word_list), 500):
        # 与 LookupCache 一致按 500 分块，避免旧版 SQLite 绑定参数上限报错。
        for row in (
            db.query(LookupHistory)
            .filter(
                LookupHistory.user_id == user_id,
                LookupHistory.query.in_(word_list[index : index + 500]),
            )
            .order_by(LookupHistory.created_at.desc(), LookupHistory.id.desc())
            .all()
        ):
            key = row.query.strip()
            current = histories.get(key)
            if current is None or row.id > current.id:
                histories[key] = row
    caches: dict[str, LookupCache] = {}
    word_list = list(words)
    for index in range(0, len(word_list), 500):
        for row in (
            db.query(LookupCache)
            .filter(LookupCache.query.in_(word_list[index : index + 500]))
            .all()
        ):
            caches[row.query] = row

    def lookup_sentence(front: str, explanation: str, word: str) -> str:
        candidate = _complete_sentence(front)
        if is_complete_sentence(candidate, word):
            return candidate
        if explanation:
            from .ai import _card_fields_from_streamlit_result

            parsed, _ = _card_fields_from_streamlit_result(
                explanation, word, "phrase" if " " in word else "word"
            )
            parsed = _complete_sentence(parsed)
            if is_complete_sentence(parsed, word):
                return parsed
        return ""

    corpus_texts: list[str] | None = None
    for card in cards:
        sentence = _complete_sentence(card.context or "")
        if not is_complete_sentence(sentence, card.word):
            sentence = lookup_sentence(card.front or "", "", card.word)
        history = histories.get(card.word.strip())
        if not sentence and history:
            sentence = lookup_sentence(
                history.card_front or "", history.explanation or "", card.word
            )
        cached = caches.get(card.word.strip())
        if not sentence and cached:
            sentence = lookup_sentence(
                cached.card_front or "", cached.explanation or "", card.word
            )
        if not sentence:
            builtin = builtin_lookup.get(card.word)
            sentence = lookup_sentence("", builtin or "", card.word)
        if not sentence:
            if corpus_texts is None:
                corpus_texts = []
                scanned = 0
                for (raw_text,) in (
                    db.query(Corpus.raw_text)
                    .filter(Corpus.user_id == user_id)
                    .order_by(Corpus.created_at.desc())
                    .all()
                ):
                    text = str(raw_text or "")[:_SENTENCE_CORPUS_PER_BOOK_CHARS]
                    if not text:
                        continue
                    if scanned + len(text) > _SENTENCE_CORPUS_SCAN_CHARS:
                        break
                    corpus_texts.append(text)
                    scanned += len(text)
            for text in corpus_texts:
                sentence = _complete_sentence(vocab.sentence_for_word(text, card.word))
                if is_complete_sentence(sentence, card.word):
                    break
                sentence = ""
        if is_complete_sentence(sentence, card.word):
            if card.card_type == "cloze":
                desired = sentence_front(sentence, card.word, cloze=True)
            else:
                desired = sentence_front(sentence, card.word, cloze=False)
            if desired != card.front:
                card.front = desired
                changed += 1
            card.context = sentence
        elif card.card_type == "reading" and "**" not in (card.front or ""):
            # 实在找不到完整例句：正面加粗单词，仍凸显目标词。
            base = re.sub(r"[\(\（].*?[\)\）]", "", card.word, flags=re.IGNORECASE).strip()
            if card.front != f"**{base}**":
                card.front = f"**{base}**"
                changed += 1
        cleaned_back = _remove_standalone_word(card.back, card.word)
        if cleaned_back != card.back:
            card.back = cleaned_back
            changed += 1
    if changed:
        db.commit()
    return changed


def build_cards(
    db: Session,
    user_id: int,
    corpus,
    words: list[str],
    card_type: str,
    limit: int,
) -> tuple[int, int, list[str]]:
    """为语料库中指定词创建卡片；已存在的 (user, word, type) 自动跳过。
    返回 (本次新建数, 该类型卡片总数, 本次新建词)。"""
    if card_type not in ("general", "reading", "cloze"):
        raise ValueError("card_type 必须是 general / reading / cloze")
    limit = min(max(1, limit), config.MAX_CARDS_PER_RUN)
    existing = {
        (row.word, row.card_type)
        for row in db.query(Card).filter(
            Card.user_id == user_id, Card.card_type == card_type
        )
    }
    created = 0
    created_words: list[str] = []
    for word in words:
        if (word, card_type) in existing:
            continue
        if created >= limit:
            break
        entry = db.query(WordEntry).filter(WordEntry.word == word).first()
        rank = vocab.rank_of(word)
        rank_line = f"NGSL 排名：{rank}" if rank else "NGSL 排名：—"
        sentence = _complete_sentence(vocab.sentence_for_word(corpus.raw_text, word))
        # 通用卡正面固定为单词、不依赖语料句子；阅读卡/Cloze 卡才要求完整例句。
        if card_type != "general" and not sentence:
            continue
        definition = definition_text(entry)

        if card_type == "reading":
            front = sentence_front(sentence, word, cloze=False)
            back = f"{definition}\n\n{rank_line}"
        elif card_type == "general":
            front = word
            back = f"{definition}\n\n{rank_line}"
        else:  # cloze
            front = sentence_front(sentence, word, cloze=True)
            back = "\n\n".join(
                [sentence_front(sentence, word, cloze=False), definition, rank_line]
            )

        db.add(
            Card(
                user_id=user_id,
                word=word,
                card_type=card_type,
                front=front,
                back=back,
                context=sentence,
                state="new",
                due_at=None,
                reps=0,
                lapses=0,
                interval_days=0.0,
                ease=2.5,
            )
        )
        existing.add((word, card_type))
        created_words.append(word)
        created += 1
    db.commit()
    total = (
        db.query(Card)
        .filter(Card.user_id == user_id, Card.card_type == card_type)
        .count()
    )
    return created, total, created_words
