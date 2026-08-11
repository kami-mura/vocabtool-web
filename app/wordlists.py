"""内置词表：作为制卡来源选项，不参与排名与词汇量估算。"""

from __future__ import annotations

from functools import lru_cache

from . import config, vocab

LISTS = [
    {"id": "primary", "name": "小学大纲", "source": "义务教育课程标准"},
    {"id": "junior", "name": "初中大纲", "source": "义务教育课程标准"},
    {"id": "senior", "name": "高中大纲", "source": "普通高中课程标准"},
    {"id": "cet4", "name": "大学英语四级", "source": "四级考试大纲"},
    {"id": "cet6", "name": "大学英语六级", "source": "六级考试大纲"},
    {"id": "kaoyan", "name": "考研英语", "source": "考研英语大纲"},
    {"id": "ielts", "name": "雅思", "source": "公开频率词表近似"},
    {"id": "toefl", "name": "托福", "source": "公开频率词表近似"},
    {"id": "gre", "name": "GRE", "source": "公开频率词表近似"},
    {"id": "tem4", "name": "英语专业四级", "source": "专四大纲词汇"},
    {"id": "tem8", "name": "英语专业八级", "source": "专八大纲词汇"},
    {"id": "sat", "name": "SAT 高频", "source": "公开高频词表近似"},
    {"id": "act", "name": "ACT 核心", "source": "公开高频词表近似"},
    {"id": "awl", "name": "学术词汇 AWL", "source": "Coxhead AWL 570"},
    {"id": "coca5k", "name": "COCA 高频 5000", "source": "COCA 语料库"},
    {"id": "mba", "name": "MBA 联考", "source": "MBA 联考英语词汇"},
    {"id": "zhicheng", "name": "职称英语", "source": "职称英语考试词汇"},
    {"id": "oxford3000", "name": "牛津 3000", "source": "Oxford Learner's Word Lists"},
    {"id": "oxford5000", "name": "牛津 5000", "source": "Oxford Learner's Word Lists"},
    {"id": "longman3000", "name": "朗文 3000", "source": "Longman Communication 3000"},
    {"id": "longman9000", "name": "朗文 9000", "source": "Longman Communication 9000"},
    {"id": "collins5", "name": "柯林斯 5 星", "source": "Collins COBUILD 词频"},
    {"id": "collins4", "name": "柯林斯 4 星", "source": "Collins COBUILD 词频"},
    {"id": "collins3", "name": "柯林斯 3 星", "source": "Collins COBUILD 词频"},
    {"id": "collins2", "name": "柯林斯 2 星", "source": "Collins COBUILD 词频"},
    {"id": "collins1", "name": "柯林斯 1 星", "source": "Collins COBUILD 词频"},
    {"id": "vocabcom1000", "name": "Vocabulary.com Top 1000", "source": "vocabulary.com 官方列表"},
    {"id": "ngsl_core", "name": "NGSL 1.2 核心 2809", "source": "New General Service List 1.2"},
    {"id": "ngsl_spoken", "name": "日常口语 NGSL-S", "source": "NGSL-Spoken 1.2"},
    {"id": "nawl", "name": "新学术词汇 NAWL", "source": "New Academic Word List 1.2"},
    {"id": "avl", "name": "COCA 学术词汇 AVL 3000", "source": "Davies & Gardner AVL（COCA 学术语料）"},
    {"id": "eap_science", "name": "EAP 科学词表", "source": "Coxhead & Hirsh 科学语料词表"},
    {"id": "bsl", "name": "商务英语 BSL", "source": "Business Service List 1.2"},
    {"id": "toeic", "name": "TOEIC TSL", "source": "TOEIC Service List 1.2"},
    {"id": "phave150", "name": "高频短语动词 PHaVE 150", "source": "Garnier & Schmitt PHaVE List"},
    {"id": "academic_collocations", "name": "学术搭配 ACL", "source": "Pearson Academic Collocation List 2025"},
    {"id": "medical", "name": "医学口语 MOEL", "source": "Medical Oral English List 1.0"},
    {"id": "fitness", "name": "健身英语 FEL", "source": "Fitness English List 1.2"},
    {"id": "ndl", "name": "儿童核心词 New Dolch", "source": "New Dolch List 1.1（儿童语料）"},
    {"id": "legal", "name": "法律学术词汇", "source": "COCA 学术语料 LAW 域提取"},
    {"id": "programming", "name": "IT 与编程术语", "source": "MDN Glossary"},
    {"id": "finance", "name": "金融与投资术语", "source": "SEC Investor.gov Glossary"},
    {"id": "cefr_a1", "name": "CEFR-J A1（入门）", "source": "CEFR-J 1.5（TUFS）"},
    {"id": "cefr_a2", "name": "CEFR-J A2（基础）", "source": "CEFR-J 1.5（TUFS）"},
    {"id": "cefr_b1", "name": "CEFR-J B1（中级）", "source": "CEFR-J 1.5（TUFS）"},
    {"id": "cefr_b2", "name": "CEFR-J B2（中高级）", "source": "CEFR-J 1.5（TUFS）"},
    {"id": "cefr_c1", "name": "CEFR C1（Octanove 扩展）", "source": "Octanove C1/C2 1.0"},
    {"id": "cefr_c2", "name": "CEFR C2（Octanove 扩展）", "source": "Octanove C1/C2 1.0"},
]


def list_ids() -> set[str]:
    return {entry["id"] for entry in LISTS}


def _entry(list_id: str) -> dict:
    for entry in LISTS:
        if entry["id"] == list_id:
            return entry
    raise ValueError(f"未知词表：{list_id}")


def list_name(list_id: str) -> str:
    return _entry(list_id)["name"]


@lru_cache(maxsize=1024)
def load_wordlist(list_id: str) -> dict[str, int]:
    """加载词表：word -> NGSL 排名（不在 NGSL 的词 rank=0）。"""
    _entry(list_id)
    if list_id == "ngsl_core":
        return {
            word: rank
            for word, rank in vocab.load_ngsl().items()
            if rank <= 2809
        }
    path = config.BASE_DIR / "data" / "wordlists" / f"{list_id}.csv"
    mapping: dict[str, int] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            word = line.strip().lower()
            if not word:
                continue
            mapping[word] = vocab.rank_of(word) or 0
    return mapping


def wordlist_counts() -> dict[str, int]:
    return {entry["id"]: len(load_wordlist(entry["id"])) for entry in LISTS}
