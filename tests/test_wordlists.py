import pytest

from app import wordlists


def test_all_registered_lists_load_with_words():
    for entry in wordlists.LISTS:
        words = wordlists.load_wordlist(entry["id"])
        assert words, f"{entry['id']} 词表为空"


def test_invalid_list_id_raises_value_error():
    with pytest.raises(ValueError):
        wordlists.load_wordlist("not-a-list")


def test_list_ids_and_name():
    assert wordlists.list_ids() == {
        "primary", "junior", "senior", "cet4", "cet6",
        "kaoyan", "ielts", "toefl", "gre", "tem4", "tem8",
        "sat", "act", "awl", "coca5k", "mba", "zhicheng",
        "oxford3000", "oxford5000", "longman3000", "longman9000",
        "collins1", "collins2", "collins3", "collins4", "collins5",
        "vocabcom1000", "ngsl_core", "ngsl_spoken", "nawl", "avl", "eap_science",
        "bsl", "toeic", "phave150", "academic_collocations", "medical", "fitness",
        "ndl", "legal", "programming", "finance", "cefr_a1", "cefr_a2", "cefr_b1",
        "cefr_b2", "cefr_c1", "cefr_c2",
    }
    assert wordlists.list_name("cet4") == "大学英语四级"
    assert wordlists.list_name("cefr_a1") == "CEFR-J A1（入门）"
    assert wordlists.list_name("cefr_c1") == "CEFR C1（Octanove 扩展）"
    with pytest.raises(ValueError):
        wordlists.list_name("nope")


def test_words_mapped_to_ngsl_rank():
    words = wordlists.load_wordlist("primary")
    assert words.get("apple") is not None  # apple 在 NGSL 中
    assert all(isinstance(rank, int) and rank >= 0 for rank in words.values())


def test_wordlist_counts_match_loaded_length():
    counts = wordlists.wordlist_counts()
    for entry in wordlists.LISTS:
        assert counts[entry["id"]] == len(wordlists.load_wordlist(entry["id"]))


def test_new_wordlists_keep_words_phrases_and_cefr_levels():
    assert "actually" in wordlists.load_wordlist("ngsl_spoken")
    assert list(wordlists.load_wordlist("ngsl_core").items())[-1] == ("seminar", 2809)
    assert "empirical" in wordlists.load_wordlist("nawl")
    assert "carry out" in wordlists.load_wordlist("phave150")
    assert "academic achievement" in wordlists.load_wordlist("academic_collocations")
    assert "achilles tendon" in wordlists.load_wordlist("medical")
    assert "algorithm" in wordlists.load_wordlist("programming")
    assert "asset allocation" in wordlists.load_wordlist("finance")
    assert "about" in wordlists.load_wordlist("cefr_a1")
    assert "complexity" in wordlists.load_wordlist("cefr_c1")


def test_new_wordlist_counts_guard_against_truncated_source_files():
    assert {list_id: len(wordlists.load_wordlist(list_id)) for list_id in {
        "ngsl_core", "ngsl_spoken", "nawl", "bsl", "toeic", "phave150",
        "academic_collocations", "medical", "programming", "finance",
        "cefr_a1", "cefr_a2", "cefr_b1", "cefr_b2", "cefr_c1", "cefr_c2",
    }} == {
        "ngsl_core": 2809,
        "ngsl_spoken": 721,
        "nawl": 957,
        "bsl": 1744,
        "toeic": 1250,
        "phave150": 150,
        "academic_collocations": 2468,
        "medical": 656,
        "programming": 603,
        "finance": 402,
        "cefr_a1": 1084,
        "cefr_a2": 1383,
        "cefr_b1": 2390,
        "cefr_b2": 2765,
        "cefr_c1": 1026,
        "cefr_c2": 999,
    }
