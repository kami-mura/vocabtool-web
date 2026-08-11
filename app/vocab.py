from __future__ import annotations

import csv
import difflib
import re
from collections import Counter
from functools import lru_cache

from . import config

_TOKEN_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")

# 常见不规则动词/名词：变体 -> 词头（NGSL 只收录词头形式）
_IRREGULAR = {
    # NGSL 以词头为主，常见代词的宾格和物主形式需要共享同一个认识状态。
    "me": "i", "my": "i",
    "your": "you", "yours": "you",
    "him": "he", "his": "he",
    "her": "she", "hers": "she",
    "its": "it",
    "us": "we", "our": "we", "ours": "we",
    "them": "they", "their": "they", "theirs": "they",
    "whom": "who", "whose": "who",
    "myself": "i",
    "yourself": "you", "yourselves": "you",
    "himself": "he", "herself": "she",
    "itself": "it",
    "ourselves": "we",
    "themselves": "they",
    "am": "be", "is": "be", "are": "be", "was": "be", "were": "be",
    "been": "be", "being": "be",
    "has": "have", "had": "have", "having": "have",
    "does": "do", "did": "do", "done": "do", "doing": "do",
    "went": "go", "gone": "go", "going": "go",
    "said": "say", "says": "say", "saying": "say",
    "got": "get", "gotten": "get", "gets": "get", "getting": "get",
    "made": "make", "makes": "make", "making": "make",
    "knew": "know", "known": "know", "knows": "know", "knowing": "know",
    "thought": "think", "thinks": "think", "thinking": "think",
    "took": "take", "taken": "take", "takes": "take", "taking": "take",
    "saw": "see", "seen": "see", "sees": "see", "seeing": "see",
    "came": "come", "comes": "come", "coming": "come",
    "found": "find", "finds": "find", "finding": "find",
    "gave": "give", "given": "give", "gives": "give", "giving": "give",
    "told": "tell", "tells": "tell", "telling": "tell",
    "became": "become", "becomes": "become", "becoming": "become",
    "left": "leave", "leaves": "leave", "leaving": "leave",
    "felt": "feel", "feels": "feel", "feeling": "feel",
    "brought": "bring", "brings": "bring", "bringing": "bring",
    "began": "begin", "begun": "begin", "begins": "begin", "beginning": "begin",
    "kept": "keep", "keeps": "keep", "keeping": "keep",
    "held": "hold", "holds": "hold", "holding": "hold",
    "wrote": "write", "written": "write", "writes": "write", "writing": "write",
    "stood": "stand", "stands": "stand", "standing": "stand",
    "heard": "hear", "hears": "hear", "hearing": "hear",
    "meant": "mean", "means": "mean", "meaning": "mean",
    "met": "meet", "meets": "meet", "meeting": "meet",
    "ran": "run", "runs": "run", "running": "run",
    "paid": "pay", "pays": "pay", "paying": "pay",
    "sat": "sit", "sits": "sit", "sitting": "sit",
    "spoke": "speak", "spoken": "speak", "speaks": "speak", "speaking": "speak",
    "led": "lead", "leads": "lead", "leading": "lead",
    "grew": "grow", "grown": "grow", "grows": "grow", "growing": "grow",
    "lost": "lose", "loses": "lose", "losing": "lose",
    "fell": "fall", "fallen": "fall", "falls": "fall", "falling": "fall",
    "sent": "send", "sends": "send", "sending": "send",
    "built": "build", "builds": "build", "building": "build",
    "understood": "understand", "understands": "understand", "understanding": "understand",
    "drew": "draw", "drawn": "draw", "draws": "draw", "drawing": "draw",
    "broke": "break", "broken": "break", "breaks": "break", "breaking": "break",
    "spent": "spend", "spends": "spend", "spending": "spend",
    "rose": "rise", "risen": "rise", "rises": "rise", "rising": "rise",
    "drove": "drive", "driven": "drive", "drives": "drive", "driving": "drive",
    "bought": "buy", "buys": "buy", "buying": "buy",
    "wore": "wear", "worn": "wear", "wears": "wear", "wearing": "wear",
    "chose": "choose", "chosen": "choose", "chooses": "choose", "choosing": "choose",
    "flew": "fly", "flown": "fly", "flies": "fly", "flying": "fly",
    "caught": "catch", "catches": "catch", "catching": "catch",
    "ate": "eat", "eaten": "eat", "eats": "eat", "eating": "eat",
    "slept": "sleep", "sleeps": "sleep", "sleeping": "sleep",
    "swam": "swim", "swum": "swim", "swims": "swim", "swimming": "swim",
    "sang": "sing", "sung": "sing", "sings": "sing", "singing": "sing",
    "taught": "teach", "teaches": "teach", "teaching": "teach",
    "sold": "sell", "sells": "sell", "selling": "sell",
    "fought": "fight", "fights": "fight", "fighting": "fight",
    "threw": "throw", "thrown": "throw", "throws": "throw", "throwing": "throw",
    "won": "win", "wins": "win", "winning": "win",
    "rode": "ride", "ridden": "ride", "rides": "ride", "riding": "ride",
    "children": "child",
    "men": "man", "women": "woman",
    "feet": "foot", "teeth": "tooth", "mice": "mouse",
}


@lru_cache(maxsize=1)
def load_ngsl() -> dict[str, int]:
    """加载 NGSL+SFI 词频表：word -> rank。"""
    path = config.BASE_DIR / "data" / "ngsl_sfi_31k.csv"
    mapping: dict[str, int] = {}
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                word = (row.get("word") or "").strip().lower()
                if not word:
                    continue
                try:
                    mapping[word] = int(row.get("rank") or 0)
                except ValueError:
                    mapping[word] = 0
    return mapping


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def tokenize_with_spans(text: str):
    """返回 [{token, norm, start, end}]，token 为原文小写形式。"""
    spans = []
    for m in _TOKEN_RE.finditer(text):
        token = m.group(0).lower()
        spans.append(
            {
                "token": token,
                "norm": normalize_word(token),
                "start": m.start(),
                "end": m.end(),
            }
        )
    return spans


@lru_cache(maxsize=200_000)
def normalize_word(token: str) -> str:
    """取词形还原后的词头：running/ran -> run；查不到词库时保留原词。"""
    w = token.lower()
    ngsl = load_ngsl()
    if w in _IRREGULAR:
        return _IRREGULAR[w]
    if w in ngsl:
        return w
    candidates = []
    if w.endswith("ies") and len(w) > 4:
        candidates.append(w[:-3] + "y")
    if w.endswith("ied") and len(w) > 4:
        candidates.append(w[:-3] + "y")
    if w.endswith("es") and len(w) > 3:
        candidates.append(w[:-2])
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        candidates.append(w[:-1])
    if w.endswith("ing") and len(w) > 5:
        base = w[:-3]
        if len(base) > 1 and base[-1] == base[-2]:
            base = base[:-1]
        candidates.append(base)
        candidates.append(base + "e")
    if w.endswith("ed") and len(w) > 4:
        base = w[:-2]
        if len(base) > 1 and base[-1] == base[-2]:
            base = base[:-1]
        candidates.append(base)
        candidates.append(base + "e")
    for cand in candidates:
        if cand in ngsl:
            return cand
    return w


def user_word_identity(word: str) -> str:
    """按用户书写保留大小写的词身份。

    全小写词沿用词形还原（ran -> run、cats -> cat）；
    含大写字母的词（March、iPhone）按原样区分，不再与全小写词合并。
    """
    w = re.sub(r"\s+", " ", str(word or "")).strip()
    if not w:
        return ""
    return w if w != w.lower() else normalize_word(w)


def rank_of(word: str) -> int | None:
    return load_ngsl().get(word.lower())


@lru_cache(maxsize=2048)
def suggest_correction(word: str) -> str | None:
    """拼写纠错：词不在 NGSL/SFI 词表时，返回最接近的正确词。

    使用 difflib 在词表里找相似词，相似度 0.6 以上才建议；
    完全相同的词（大小写差异已归一）或过短的词不处理。
    """
    w = str(word or "").strip().lower()
    if not w or len(w) < 2:
        return None
    ngsl = load_ngsl()
    if w in ngsl:
        return None
    # 常见拼写错误：双写/漏写一个字母时优先候选
    matches = difflib.get_close_matches(w, ngsl, n=1, cutoff=0.6)
    if not matches:
        return None
    candidate = matches[0]
    # 太短或差异过大时不建议，避免误报
    if abs(len(candidate) - len(w)) > 3:
        return None
    if len(w) <= 4 and candidate != w:
        # 短词只接受差一个字母的纠正
        if len(candidate) != len(w) or difflib.SequenceMatcher(None, w, candidate).ratio() < 0.75:
            return None
    return candidate


def is_valid_word(word: str) -> bool:
    """判断一个词是否符合处理条件：长度 2–25、无连续重复字符、含元音（移植自 Streamlit）。"""
    value = str(word or "").lower().strip()
    if len(value) < 2 or len(value) > 25:
        return False
    if re.search(r"(.)\1{2,}", value):
        return False
    return re.search(r"[aeiouy]", value)


def analyze(text: str, normalize: bool = True) -> dict[str, int]:
    """统计文本中每个词的出现次数。

    默认把词形归一化到词头（running -> run），适合词频统计；
    normalize=False 时保留原文词形，用于制卡目标提取等需要保留用户原文的场景。
    """
    surface_counts: Counter = Counter(_TOKEN_RE.findall(text.lower()))
    counts: Counter = Counter()
    for token, count in surface_counts.items():
        counts[normalize_word(token) if normalize else token] += count
    return dict(counts)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]


def sentence_for_word(text: str, word: str) -> str:
    target = word.lower()
    for s in sentences(text):
        if any(normalize_word(token) == target for token in tokenize(s)):
            return s
    return ""
