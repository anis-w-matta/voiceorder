from app.services.scripted.command_parser import parse
from app.services.scripted.models import (ParseError, ParseFailure,
                                          ParsedPlaceOrder, ParsedReorder,
                                          ParsedReturnOrder)


def test_place_order_multiple_items():
    t = ("place order for ABC Trading items one tendrex adult med twelve "
        "by four quantity five kartouneh two medical underpad large "
        "eighty by sixty twenty by four quantity three cartons the end")
    r = parse(t)
    assert isinstance(r, ParsedPlaceOrder)
    assert r.customer_text == "ABC Trading"
    assert len(r.items) == 2
    assert r.items[0].quantity_text == "five"
    assert r.items[0].uom_text == "kartouneh"
    assert r.items[1].quantity_text == "three"
    assert r.items[1].uom_text == "cartons"


def test_place_order_tolerates_copula_between_marker_and_quantity():
    t = ("place order for Test Trading items tendrex adult large twelve "
        "by four quantity is six cartons the end")
    r = parse(t)
    assert isinstance(r, ParsedPlaceOrder)
    assert len(r.items) == 1
    assert r.items[0].quantity_text == "six"
    assert r.items[0].uom_text == "cartons"


def test_place_order_implicit_quantity_no_marker_word():
    t = ("place order for Test Trading items tendrex adult large 5 carton "
        "medica pull ups 3 dozen the end")
    r = parse(t)
    assert isinstance(r, ParsedPlaceOrder)
    assert len(r.items) == 2
    assert r.items[0].item_text == "tendrex adult large"
    assert r.items[0].quantity_text == "5"
    assert r.items[0].uom_text == "carton"
    assert r.items[1].item_text == "medica pull ups"
    assert r.items[1].quantity_text == "3"
    assert r.items[1].uom_text == "dozen"


def test_place_order_mixed_marker_and_implicit_uses_explicit_only():
    # One item says "quantity", the other doesn't - explicit markers exist
    # somewhere in the span, so implicit fallback must NOT kick in (that's
    # the collision the all-or-nothing design exists to avoid). The
    # markerless item just fails to produce a clean quantity, same as
    # today's behavior with no marker present.
    t = ("place order for Test Trading items tendrex adult large 5 carton "
        "medica pull ups quantity 3 dozen the end")
    r = parse(t)
    assert isinstance(r, ParsedPlaceOrder)
    assert len(r.items) == 1
    assert r.items[0].item_text == "tendrex adult large 5 carton medica pull ups"
    assert r.items[0].quantity_text == "3"
    assert r.items[0].uom_text == "dozen"


def test_place_order_tolerates_asr_noise_in_command_start():
    t = ("place order fer XYZ Corp items medical underpad large "
        "quantity twenty cartons the end")
    r = parse(t)
    assert isinstance(r, ParsedPlaceOrder)
    assert r.customer_text == "XYZ Corp"


def test_place_order_missing_items_delimiter_fails_safely():
    r = parse("place order for ABC Trading tendrex quantity five carton the end")
    assert isinstance(r, ParseFailure)
    assert r.error == ParseError.ITEMS_DELIMITER_NOT_FOUND


def test_place_order_missing_customer_fails_safely():
    r = parse("place order for items one tendrex quantity five carton the end")
    assert isinstance(r, ParseFailure)
    assert r.error == ParseError.CUSTOMER_DELIMITER_NOT_FOUND


def test_place_order_missing_quantity_marker_fails_safely():
    r = parse("place order for ABC Trading items tendrex the end")
    assert isinstance(r, ParseFailure)
    assert r.error == ParseError.ITEM_QUANTITY_NOT_FOUND


def test_no_recognized_command_fails_safely():
    r = parse("hello how are you")
    assert isinstance(r, ParseFailure)
    assert r.error == ParseError.COMMAND_START_NOT_FOUND


def test_missing_end_marker_still_recovers_trailing_text():
    t = "place order for ABC items one tendrex quantity five carton"
    r = parse(t)
    assert isinstance(r, ParsedPlaceOrder)
    assert r.items[0].item_text == "tendrex"


def test_return_order_full():
    r = parse("return order 1432071 the end")
    assert isinstance(r, ParsedReturnOrder)
    assert r.order_reference == "1432071"
    assert r.is_full_return


def test_return_order_partial():
    t = "return order 1432071 item tendrex adult med quantity two cartons the end"
    r = parse(t)
    assert isinstance(r, ParsedReturnOrder)
    assert r.order_reference == "1432071"
    assert not r.is_full_return
    assert len(r.items) == 1
    assert r.items[0].item_text == "tendrex adult med"


def test_return_order_missing_reference_fails_safely():
    r = parse("return order the end")
    assert isinstance(r, ParseFailure)
    assert r.error == ParseError.ORDER_REFERENCE_NOT_FOUND


def test_reorder_mode_last_time():
    r = parse("reorder for ABC Trading same order last time the end")
    assert isinstance(r, ParsedReorder)
    assert r.customer_text == "ABC Trading"
    assert r.mode == "last"
    assert r.reference is None


def test_reorder_mode_order_nb():
    r = parse("reorder for ABC Trading same order 1432071 the end")
    assert isinstance(r, ParsedReorder)
    assert r.mode == "order_nb"
    assert r.reference == "1432071"


def test_reorder_mode_date():
    r = parse("reorder for ABC Trading same order 5 january 2026 the end")
    assert isinstance(r, ParsedReorder)
    assert r.mode == "date"
    assert "january" in r.reference.lower()


def test_reorder_missing_marker_fails_safely():
    r = parse("reorder for ABC Trading last time the end")
    assert isinstance(r, ParseFailure)


def test_multiple_items_five():
    words = " ".join(f"item{i} quantity {i+1} carton" for i in range(5))
    t = f"place order for ABC items {words} the end"
    r = parse(t)
    assert isinstance(r, ParsedPlaceOrder)
    assert len(r.items) == 5


# ---- Arabic-script command grammar (not just Arabizi item words) --------

def test_place_order_fully_in_arabic_script():
    t = ("اطلب طلبية لـ ABC Trading "
        "اصناف واحد tendrex adult med 12x4 "
        "كمية خمسة كرتونة "
        "النهاية")
    r = parse(t)
    assert isinstance(r, ParsedPlaceOrder)
    assert r.customer_text == "ABC Trading"
    assert len(r.items) == 1
    # The Arabic counter word ("واحد" = "one") must be
    # stripped from item_text the same way the English "one" is.
    assert r.items[0].item_text == "tendrex adult med 12x4"
    assert r.items[0].quantity_text == "خمسة"
    assert r.items[0].uom_text == "كرتونة"


def test_return_order_fully_in_arabic_script():
    t = ("رجاع طلبية 12345 "
        "النهاية")
    r = parse(t)
    assert isinstance(r, ParsedReturnOrder)
    assert r.order_reference == "12345"
    assert r.is_full_return


def test_reorder_fully_in_arabic_script():
    t = ("اعادة طلبية "
        "لـ ABC Trading نفس الطلبية "
        "آخر مرة النهاية")
    r = parse(t)
    assert isinstance(r, ParsedReorder)
    assert r.customer_text == "ABC Trading"
    assert r.mode == "last"
