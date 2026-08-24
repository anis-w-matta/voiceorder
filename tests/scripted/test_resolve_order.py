import pytest

from app.models import Customer, Item
from app.services.scripted.resolve_order import resolve
from app.services.scripted.models import (MatchStatus, ParsedItemSpan,
                                          ParsedPlaceOrder, ParsedReorder,
                                          ParsedReturnOrder, ParseError,
                                          ParseFailure)


@pytest.fixture
def fixtures(db_session):
    db_session.add(Customer(customer_number="ZZRC1", customer_name="Zzresolve Trading"))
    db_session.add(Item(item_number="ZZRI1", item_desc="ZZRESOLVE WIDGET MED 5X2",
                        category="Misc"))
    db_session.flush()


def _place_order(customer_text):
    return ParsedPlaceOrder(customer_text=customer_text, items=[
        ParsedItemSpan(item_text="zzresolve widget med 5x2",
                       quantity_text="three", uom_text="packets")])


def test_place_order_success_when_everything_resolves(db_session, fixtures):
    parsed = _place_order("Zzresolve Trading")
    result = resolve(db_session, parsed)
    assert result.status == "success"
    assert result.customer.customer_number == "ZZRC1"
    assert result.lines[0].match.item_number == "ZZRI1"


def test_place_order_needs_confirmation_when_customer_unmatched(db_session, fixtures):
    parsed = _place_order("Totally Unknown Company")
    result = resolve(db_session, parsed)
    assert result.status == "needs_confirmation"


def test_place_order_needs_confirmation_when_item_ambiguous(db_session, fixtures):
    db_session.add(Item(item_number="ZZRI2", item_desc="ZZRESOLVE WIDGET MED 5X2",
                        category="Misc"))
    db_session.flush()
    parsed = _place_order("Zzresolve Trading")
    result = resolve(db_session, parsed)
    assert result.status == "needs_confirmation"
    assert result.lines[0].match.status == MatchStatus.AMBIGUOUS


def test_parse_failure_maps_to_parse_error_status(db_session, fixtures):
    parsed = ParseFailure(ParseError.COMMAND_START_NOT_FOUND,
                          "no command found", "hello how are you")
    result = resolve(db_session, parsed)
    assert result.status == "parse_error"
    assert result.errors[0] == "COMMAND_START_NOT_FOUND"


def test_return_order_full_return_success(db_session, fixtures):
    parsed = ParsedReturnOrder(order_reference="12345", items=[])
    result = resolve(db_session, parsed)
    assert result.status == "success"
    assert result.full_return is True
    assert result.order_reference == "12345"


def test_reorder_plain_success_ignores_empty_items(db_session, fixtures):
    parsed = ParsedReorder(customer_text="Zzresolve Trading", mode="last")
    result = resolve(db_session, parsed)
    assert result.status == "success"
    assert result.lines == []


def test_reorder_needs_confirmation_when_customer_unresolved(db_session, fixtures):
    parsed = ParsedReorder(customer_text="", mode="order_nb",
                           reference="260000094")
    result = resolve(db_session, parsed)
    assert result.status == "needs_confirmation"
    assert result.customer.status == MatchStatus.NOT_FOUND


def test_reorder_with_clean_adjustment_items_is_success(db_session, fixtures):
    parsed = ParsedReorder(
        customer_text="Zzresolve Trading", mode="order_nb",
        reference="260000094",
        items=[ParsedItemSpan(item_text="zzresolve widget med 5x2",
                              quantity_text="four", uom_text="each")])
    result = resolve(db_session, parsed)
    assert result.status == "success"
    assert len(result.lines) == 1
    assert result.lines[0].match.item_number == "ZZRI1"


def test_reorder_with_unresolved_adjustment_item_needs_confirmation(
        db_session, fixtures):
    parsed = ParsedReorder(
        customer_text="Zzresolve Trading", mode="order_nb",
        reference="260000094",
        items=[ParsedItemSpan(item_text="totally unknown widget",
                              quantity_text="four", uom_text="each")])
    result = resolve(db_session, parsed)
    assert result.status == "needs_confirmation"
