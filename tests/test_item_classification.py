from app.services.item_classifier import UNABLE_TO_CLASSIFY, classify_line

CATEGORIES = ["Consumables", "Misc", "Paint", "Tools"]


def test_tier1_uses_the_resolved_items_own_category():
    assert classify_line(matched_category="Paint", raw_text="anything",
                         known_categories=CATEGORIES) == "Paint"


def test_tier2_falls_back_to_category_name_in_raw_text():
    assert classify_line(matched_category=None,
                         raw_text="I need some tools for the garage",
                         known_categories=CATEGORIES) == "Tools"


def test_tier3_unable_to_classify_when_nothing_matches():
    assert classify_line(matched_category=None,
                         raw_text="1234567 xxxxxxx yyyyyyy zzzzzzz",
                         known_categories=CATEGORIES) == UNABLE_TO_CLASSIFY


def test_empty_raw_text_is_unable_to_classify():
    assert classify_line(matched_category=None, raw_text="",
                         known_categories=CATEGORIES) == UNABLE_TO_CLASSIFY
    assert classify_line(matched_category=None, raw_text=None,
                         known_categories=CATEGORIES) == UNABLE_TO_CLASSIFY
