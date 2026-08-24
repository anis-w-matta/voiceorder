from decimal import Decimal

from app.services.scripted.match_qty_uom import parse_quantity_uom_span


def test_numeric_quantity_english_uom():
    r = parse_quantity_uom_span("5", "packets")
    assert r.status == "matched"
    assert r.quantity == Decimal("5")
    assert r.uom == "PKT"


def test_spoken_number_word():
    r = parse_quantity_uom_span("twenty", "packets")
    assert r.status == "matched"
    assert r.quantity == Decimal("20")


def test_lebanese_arabizi_number_word():
    # No Arabizi word for "packets" is in the vocabulary (see
    # quantity_uom.UOM_SYNONYMS) - pairing an Arabizi number with the
    # English unit word matches how code-switched speech actually gets
    # transcribed here.
    r = parse_quantity_uom_span("khamse", "packets")
    assert r.status == "matched"
    assert r.quantity == Decimal("5")
    assert r.uom == "PKT"


def test_arabic_script_number_and_uom():
    r = parse_quantity_uom_span("خمسة", "packets")
    assert r.status == "matched"
    assert r.quantity == Decimal("5")
    assert r.uom == "PKT"


def test_french_number_and_uom():
    r = parse_quantity_uom_span("trois", "paquets")
    assert r.status == "matched"
    assert r.quantity == Decimal("3")
    assert r.uom == "PKT"


def test_asr_homophone_number_word():
    # "to each" is a common ASR mishearing of "two each" - see the
    # _NUMBER_WORDS homophone entries in match_qty_uom.py.
    r = parse_quantity_uom_span("to", "each")
    assert r.status == "matched"
    assert r.quantity == Decimal("2")
    assert r.uom == "EACH"


def test_unrecognized_uom_is_explicit_error():
    r = parse_quantity_uom_span("5", "bananas")
    assert r.status == "error"
    assert r.quantity == Decimal("5")
    assert r.uom is None
    assert "bananas" in r.reason


def test_unrecognized_quantity_is_explicit_error():
    r = parse_quantity_uom_span("many", "cartons")
    assert r.status == "error"
    assert r.quantity is None


def test_zero_or_negative_quantity_rejected():
    r = parse_quantity_uom_span("0", "cartons")
    assert r.status == "error"


def test_missing_quantity_never_guesses():
    r = parse_quantity_uom_span("", "cartons")
    assert r.status == "error"
    assert r.quantity is None
