from app import word_forms


def test_target_surface_forms_includes_irregulars_and_regulars():
    forms = set(word_forms.target_surface_forms("run"))
    assert "run" in forms
    assert "runs" in forms
    assert "running" in forms
    assert "ran" in forms
    assert "overhung" in set(word_forms.target_surface_forms("overhang"))


def test_target_surface_forms_keeps_phrases_intact():
    forms = word_forms.target_surface_forms("pale blue")
    assert forms == ("pale blue",)


def test_target_surface_pattern_matches_inflections_only_at_boundaries():
    pattern = word_forms.target_surface_pattern("run")
    assert pattern.search("She runs every morning")
    assert pattern.search("I ran away")
    assert not pattern.search("prune the tree")


def test_target_surface_forms_expands_from_inflected_target():
    forms = set(word_forms.target_surface_forms("deteriorated"))
    assert "deteriorate" in forms
    assert "deteriorates" in forms
    assert "deteriorating" in forms
    assert "deteriorated" in forms
    irregular = set(word_forms.target_surface_forms("took"))
    assert "take" in irregular
    assert "takes" in irregular
    assert "taken" in irregular
    assert "taking" in irregular


def test_inflected_target_pattern_matches_headword_and_surfaces():
    pattern = word_forms.target_surface_pattern("deteriorated")
    assert pattern.search("The bridge deteriorated.")
    assert pattern.search("It will deteriorate further.")
    assert pattern.search("Deteriorating conditions forced them out.")
    assert not pattern.search("detergent")
