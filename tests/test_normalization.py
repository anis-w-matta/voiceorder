from app.services.normalization import (expand_size_synonyms, normalize_color,
                                        normalize_size, normalize_text)


def test_normalize_text_lowercases_and_collapses_whitespace():
    assert normalize_text("  Blue   PAINT  5L ") == "blue paint 5l"


def test_normalize_text_preserves_arabizi_digits():
    assert normalize_text("3andkon shu fi") == "3andkon shu fi"


def test_normalize_text_strips_punctuation_but_not_letters():
    assert normalize_text("cleaning, sponge!") == "cleaning sponge"


def test_normalize_text_handles_empty_and_none():
    assert normalize_text("") == ""
    assert normalize_text(None) == ""


def test_normalize_text_preserves_arabic_script():
    assert normalize_text("دهان أزرق") == "دهان أزرق"


def test_expand_size_synonyms_still_maps_kbir_kbeer_to_LRG():
    assert "LRG" in expand_size_synonyms("baddi el kbir").split()
    assert "LRG" in expand_size_synonyms("baddi el kbeer").split()


def test_expand_size_synonyms_leaves_unrelated_text_unchanged():
    assert expand_size_synonyms("blue paint") == "blue paint"


def test_normalize_size_accepts_word_and_catalogue_abbreviation():
    assert normalize_size("large") == "LRG"
    assert normalize_size("kbir") == "LRG"
    assert normalize_size("LRG") == "LRG"
    assert normalize_size(None) is None
    assert normalize_size("purple") is None


def test_normalize_color_maps_ahmar_and_7amar_to_RED():
    assert normalize_color("ahmar") == "RED"
    assert normalize_color("7amar") == "RED"
    assert normalize_color("red") == "RED"
    assert normalize_color("unknown-color") is None
