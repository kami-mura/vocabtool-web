from app import card_builder, vocab


def test_tokenize_ignores_punctuation_and_case():
    assert vocab.tokenize("Hello, world! It's fine.") == ["hello", "world", "it's", "fine"]


def test_normalize_returns_headword():
    assert vocab.normalize_word("running") == "run"
    assert vocab.normalize_word("studies") == "study"
    assert vocab.normalize_word("cats") == "cat"
    assert vocab.normalize_word("them") == "they"
    assert vocab.normalize_word("your") == "you"
    assert vocab.normalize_word("ours") == "we"


def test_user_word_identity_preserves_case_but_keeps_morphology():
    assert vocab.user_word_identity("march") == "march"
    assert vocab.user_word_identity("March") == "March"
    assert vocab.user_word_identity("running") == "run"
    assert vocab.user_word_identity("ran") == "run"
    assert vocab.user_word_identity("New York") == "New York"


def test_rank_of_ngsl_word():
    assert vocab.rank_of("the") == 1
    assert vocab.rank_of("unknownwordxyz") is None


def test_analyze_counts_forms_together():
    counts = vocab.analyze("He runs. She ran. Running is fun.")
    assert counts.get("run") == 3


def test_sentence_for_word():
    text = "First sentence here. The cat sat on the mat. Last one."
    assert "mat" in vocab.sentence_for_word(text, "mat")


def test_pronoun_forms_share_counts_and_sentence_lookup():
    counts = vocab.analyze("They gave them their books. Your book belongs to you.")
    assert counts["they"] == 3
    assert counts["you"] == 2
    assert "them" in vocab.sentence_for_word("We thanked them for their help.", "they")


def test_card_sentence_requires_same_word_or_true_inflection():
    assert card_builder.is_complete_sentence("She runs every morning before work.", "run")
    assert card_builder.is_complete_sentence("They thanked them for all their help.", "they")
    assert not card_builder.is_complete_sentence("The grossy texture bothered everyone there.", "gross")
    assert not card_builder.is_complete_sentence("This snack is clearly unhealthy for children.", "healthy")


def test_phrase_target_is_highlighted_or_clozed_in_front():
    sentence = "The interview will begin at ten o'clock sharp, so please arrive early."
    phrase = "at ten o'clock sharp"
    front = card_builder.sentence_front(sentence, phrase)
    assert "**at ten o'clock sharp**" in front
    cloze = card_builder.sentence_front(sentence, phrase, cloze=True)
    assert "______" in cloze and "ten o'clock" not in cloze
    # 单词目标高亮不受影响（词形变化仍可匹配）。
    front = card_builder.sentence_front(
        "She runs every morning before work.", "run"
    )
    assert "**runs**" in front
