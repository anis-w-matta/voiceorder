from decimal import Decimal

from app.services.scripted.match_qty_uom import parse_quantity_uom_span


def test_numeric_quantity_english_uom():
    r = parse_quantity_uom_span("5", "cartons")
    assert r.status == "matched"
    assert r.quantity == Decimal("5")
    assert r.uom == "CTN"


def test_spoken_number_word():
    r = parse_quantity_uom_span("twenty", "cartons")
    assert r.status == "matched"
    assert r.quantity == Decimal("20")


def test_lebanese_arabizi_number_word():
    r = parse_quantity_uom_span("khamse", "kartouneh")
    assert r.status == "matched"
    assert r.quantity == Decimal("5")
    assert r.uom == "CTN"


def test_arabic_script_number_and_uom():
    r = parse_quantity_uom_span("خمسة", "كرتونة")
    assert r.status == "matched"
    assert r.quantity == Decimal("5")
    assert r.uom == "CTN"


def test_french_number_and_uom():
    r = parse_quantity_uom_span("trois", "caisses")
    assert r.status == "matched"
    assert r.quantity == Decimal("3")
    assert r.uom == "CTN"


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
