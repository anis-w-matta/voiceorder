from decimal import Decimal

from app.services.quantity_uom import parse_quantity_uom


def test_100_meters_dumped_into_uom_is_recovered():
    parsed = parse_quantity_uom(None, "100 meters", "bade 100 meter cable")
    assert parsed.qty == Decimal("100")
    assert parsed.uom == "MTR"
    assert parsed.ok is True


def test_clean_qty_and_uom_pass_through_canonicalized():
    parsed = parse_quantity_uom(3, "carton", "baddi 3 cartons")
    assert parsed.qty == Decimal("3")
    assert parsed.uom == "CTN"
    assert parsed.ok is True


def test_clean_qty_with_unrecognized_uom_keeps_uom_as_is():
    parsed = parse_quantity_uom(5, "gallons", "baddi 5 gallons")
    assert parsed.qty == Decimal("5")
    assert parsed.uom == "gallons"
    assert parsed.ok is True


def test_ambiguous_multiple_unit_mentions_flags_parse_error_and_preserves_raw():
    parsed = parse_quantity_uom(None, "100 meters 50 kg", "mixed units")
    assert parsed.ok is False
    assert parsed.qty is None
    assert parsed.uom == "100 meters 50 kg"


def test_bare_unrecognized_unit_with_no_number_is_not_recoverable():
    parsed = parse_quantity_uom(None, "gallons", "baddi gallons")
    assert parsed.ok is False
    assert parsed.qty is None


def test_arabizi_uom_3ilbe_and_kartouna_recognized():
    assert parse_quantity_uom(2, "3ilbe", "").uom == "BOX"
    assert parse_quantity_uom(4, "kartouna", "").uom == "CTN"


def test_no_qty_and_no_uom_is_a_clean_noop():
    parsed = parse_quantity_uom(None, None, "just checking prices")
    assert parsed.qty is None
    assert parsed.uom is None
    assert parsed.ok is True


# ---- QA regressions found via the adversarial stress-test suite ----------

def test_sqft_is_a_recognized_unit():
    # Real catalogue vocabulary (25/37.5/75/100 SQFT aluminum foil packs) -
    # was previously not in UOM_SYNONYMS at all, silently discarding the
    # quantity whenever the unit word was unrecognized (see next test).
    parsed = parse_quantity_uom(None, "25 sqft", "aluminum 25 sqft")
    assert parsed.qty == Decimal("25")
    assert parsed.uom == "SQFT"
    assert parsed.ok is True


def test_unrecognized_unit_word_does_not_discard_a_clearly_found_quantity():
    parsed = parse_quantity_uom(None, "20 gallons", "bade 20 gallons")
    assert parsed.qty == Decimal("20")
    assert parsed.uom == "gallons"  # kept as-is, not silently dropped
    assert parsed.ok is True


def test_arabizi_whole_word_unit_not_misparsed_as_number_plus_unit():
    # "3ilbe" is a single Arabizi word (box) - the "3" must not be
    # misinterpreted as a spoken quantity of 3.
    parsed = parse_quantity_uom(None, "3ilbe", "bade 3ilbe")
    assert parsed.qty is None
    assert parsed.uom == "BOX"
    assert parsed.ok is True


def test_quantity_contradicting_spoken_text_is_not_silently_trusted():
    # The extractor says 200 but the only number actually spoken was 20 -
    # a hallucinated quantity that would otherwise silently place a
    # 10x-wrong order.
    parsed = parse_quantity_uom(200, "boxes", "bade 20 boxes")
    assert parsed.ok is False


def test_quantity_matching_spoken_text_is_trusted():
    parsed = parse_quantity_uom(20, "boxes", "bade 20 boxes")
    assert parsed.ok is True
    assert parsed.qty == Decimal("20")


def test_word_number_translation_is_not_falsely_flagged():
    # "khamse" (5) has no digit characters at all - nothing to contradict.
    parsed = parse_quantity_uom(5, "cartons", "baddi khamse cartons")
    assert parsed.ok is True


def test_arabizi_digit_letter_inside_a_word_is_not_mistaken_for_a_spoken_number():
    # "wa7de" (one) contains the digit 7 as a consonant substitution, not a
    # spoken quantity of 7 - qty=1 here must not be flagged as contradicting it.
    parsed = parse_quantity_uom(1, None, "baddi wa7de bas")
    assert parsed.ok is True
    assert parsed.qty == Decimal("1")


def test_negative_quantity_is_rejected():
    parsed = parse_quantity_uom(-5, "boxes", "bade -5 boxes")
    assert parsed.ok is False


def test_zero_quantity_is_rejected():
    parsed = parse_quantity_uom(0, "boxes", "bade 0 boxes")
    assert parsed.ok is False


# ---- QA regression: SKU-shaped digit+letter codes are not mistaken for --
# ---- a spoken number via regex backtracking (found while seeding demo --
# ---- data against the real catalogue: "12X4" is a pack-count code, not --
# ---- a quantity, but a naive lookaround regex backtracked to a false --
# ---- partial match of "1") --------------------------------------------

def test_sku_shaped_code_is_not_mistaken_for_a_spoken_number():
    # qty=5 does not appear anywhere in "12X4" - if the SKU code were
    # misread as containing the number 1 (via regex backtracking), this
    # would be wrongly flagged as a quantity contradiction.
    parsed = parse_quantity_uom(5, None, "baddi Tendrex Adult Med 12X4")
    assert parsed.ok is True
    assert parsed.qty == Decimal("5")


def test_sku_shaped_code_does_not_mask_a_genuine_contradiction():
    # The SKU code "12X4" contributes no spoken number, but "5" is a real
    # standalone number that genuinely contradicts qty=999.
    parsed = parse_quantity_uom(999, None, "bade 5 Tendrex Adult Med 12X4")
    assert parsed.ok is False
