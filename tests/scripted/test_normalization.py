from app.services.scripted.normalization import (extract_numeric_tokens,
                                                  normalize_item_text)


def test_extract_numeric_tokens_various_spacing():
    assert extract_numeric_tokens("20x4") == {"20x4"}
    assert extract_numeric_tokens("20 X 4") == {"20x4"}
    assert extract_numeric_tokens("GR(80x60cm)20X4") == {"80x60", "20x4"}


def test_extract_numeric_tokens_no_pack_code():
    assert extract_numeric_tokens("blue paint 5L") == set()


def test_normalize_item_text_expands_abbreviation():
    assert "medium" in normalize_item_text("TENDREX ADULT MED 12X4").split()


def test_normalize_item_text_expands_lebanese_synonym():
    assert "lrg" in normalize_item_text("kbeer diaper").split()
    assert "sml" in normalize_item_text("zghir diaper").split()


def test_normalize_item_text_never_corrupts_numeric_tokens():
    # "ad" -> "adult" must not fire on "undrpad" (substring, not a token) -
    # regression test for the false-positive expansion bug caught while
    # building this module.
    out = normalize_item_text("Medica Undrpad GR(80x60cm)20X4")
    assert "adult" not in out.split()
    assert "20x4" in out
    assert "80x60cm" in out


def test_normalize_item_text_preserves_original_via_extract():
    # Numeric pack codes must survive normalization unchanged, never
    # substituted by Arabizi digit mapping.
    out = normalize_item_text("tendrex 12x4")
    assert extract_numeric_tokens(out) == {"12x4"}


def test_normalize_item_text_empty():
    assert normalize_item_text("") == ""
    assert normalize_item_text(None) == ""
