from decimal import Decimal

from app.models import Customer, Item, OrderDetail, OrderHeader, VoiceMessage
from app.schemas.enums import Intent
from app.services.catalogue import CatalogueService
from app.services.draft_builder import DraftBuilder
from app.services.prior_order import PriorOrderService
from app.services.scripted.command_parser import parse
from app.services.scripted.resolve_order import resolve


def _builder(session):
    return DraftBuilder(session, PriorOrderService(session),
                        CatalogueService(session))


def _voice(session, text):
    vm = VoiceMessage(phone_raw="03000000", phone_e164=None,
                      audio_path="2026/08/14/x.wav", transcript=text,
                      status="drafted")
    session.add(vm)
    session.flush()
    return vm


def test_build_scripted_order_creates_pending_request(db_session):
    db_session.add(Customer(customer_number="ZZDB1", customer_name="Zzdraft Trading"))
    db_session.add(Item(item_number="ZZDBI1", item_desc="ZZDRAFT WIDGET MED 5X2",
                        category="Misc"))
    db_session.flush()

    text = ("place order for Zzdraft Trading items zzdraft widget med "
           "5x2 quantity four cartons the end")
    voice = _voice(db_session, text)
    result = resolve(db_session, parse(text))
    req = _builder(db_session).build_scripted_order(voice, result)

    assert req.cust_nb == "ZZDB1"
    assert req.primary_intent == Intent.add_order.value
    assert len(req.lines) == 1
    assert req.lines[0].item_nb == "ZZDBI1"
    assert req.lines[0].qty == Decimal("4")


def test_build_return_full_copies_prior_lines(db_session):
    db_session.add(Customer(customer_number="ZZDB2", customer_name="Zzreturn Trading"))
    db_session.add(Item(item_number="ZZDBI2", item_desc="ZZRETURN WIDGET",
                        category="Misc"))
    db_session.flush()
    db_session.add(OrderHeader(order_nb="ZZ990001", order_type="SO",
                              cust_nb="ZZDB2", status="open"))
    db_session.add(OrderDetail(order_nb="ZZ990001", order_type="SO", line_nb=1,
                              item_nb="ZZDBI2", item_desc="ZZRETURN WIDGET",
                              qty=Decimal("2"), uom="PCS"))
    db_session.flush()

    text = "return order ZZ990001 the end"
    voice = _voice(db_session, text)
    result = resolve(db_session, parse(text))
    prior = PriorOrderService(db_session)
    header = prior.find_by_order_nb(result.order_reference)
    req = _builder(db_session).build_return(voice, header, result)

    assert req.primary_intent == Intent.return_order.value
    assert req.cust_nb == "ZZDB2"
    assert req.target_order_nb == "ZZ990001"
    assert len(req.lines) == 1
    assert req.lines[0].item_nb == "ZZDBI2"
    assert req.lines[0].qty == Decimal("2")


def test_build_return_unresolvable_reference_flags_not_found(db_session):
    text = "return order 00000000000000 the end"
    voice = _voice(db_session, text)
    result = resolve(db_session, parse(text))
    req = _builder(db_session).build_return(voice, None, result)

    assert req.cust_nb is None
    assert "return_order_reference_not_found" in req.flags


def test_build_return_partial_keeps_item_actually_on_order(db_session):
    db_session.add(Customer(customer_number="ZZDB5", customer_name="Zzscope Trading"))
    db_session.add(Item(item_number="ZZDBI5A", item_desc="ZZSCOPE WIDGET ALPHA",
                        category="Misc"))
    db_session.flush()
    db_session.add(OrderHeader(order_nb="ZZ990004", order_type="SO",
                              cust_nb="ZZDB5", status="open"))
    db_session.add(OrderDetail(order_nb="ZZ990004", order_type="SO", line_nb=1,
                              item_nb="ZZDBI5A", item_desc="ZZSCOPE WIDGET ALPHA",
                              qty=Decimal("3"), uom="PCS"))
    db_session.flush()

    text = ("return order ZZ990004 items zzscope widget alpha quantity 1 "
           "the end")
    voice = _voice(db_session, text)
    result = resolve(db_session, parse(text))
    prior = PriorOrderService(db_session)
    header = prior.find_by_order_nb(result.order_reference)
    req = _builder(db_session).build_return(voice, header, result)

    assert len(req.lines) == 1
    assert req.lines[0].item_nb == "ZZDBI5A"
    assert "item_not_in_order" not in req.lines[0].line_flags


def test_build_return_partial_rejects_item_not_on_order(db_session):
    db_session.add(Customer(customer_number="ZZDB4", customer_name="Zzpartial Trading"))
    db_session.add(Item(item_number="ZZDBI4A", item_desc="ZZPARTIAL WIDGET ALPHA",
                        category="Misc"))
    db_session.add(Item(item_number="ZZDBI4B", item_desc="ZZPARTIAL WIDGET BETA",
                        category="Misc"))
    db_session.flush()
    db_session.add(OrderHeader(order_nb="ZZ990003", order_type="SO",
                              cust_nb="ZZDB4", status="open"))
    db_session.add(OrderDetail(order_nb="ZZ990003", order_type="SO", line_nb=1,
                              item_nb="ZZDBI4A", item_desc="ZZPARTIAL WIDGET ALPHA",
                              qty=Decimal("2"), uom="PCS"))
    db_session.flush()

    # BETA is a real catalogue item (would resolve cleanly on its own) but
    # was never on order ZZ990003 - only ALPHA was.
    text = ("return order ZZ990003 items zzpartial widget beta quantity 1 "
           "the end")
    voice = _voice(db_session, text)
    result = resolve(db_session, parse(text))
    prior = PriorOrderService(db_session)
    header = prior.find_by_order_nb(result.order_reference)
    req = _builder(db_session).build_return(voice, header, result)

    assert len(req.lines) == 1
    assert req.lines[0].item_nb is None
    assert "item_not_in_order" in req.lines[0].line_flags
    assert req.lines[0].resolution_meta["catalogue_top_match"] == "ZZDBI4B"


def test_build_reorder_last_time_resolves_target(db_session):
    db_session.add(Customer(customer_number="ZZDB3", customer_name="Zzreorder Trading"))
    db_session.add(Item(item_number="ZZDBI3", item_desc="ZZREORDER WIDGET",
                        category="Misc"))
    db_session.flush()
    db_session.add(OrderHeader(order_nb="ZZ990002", order_type="SO",
                              cust_nb="ZZDB3", status="open"))
    db_session.add(OrderDetail(order_nb="ZZ990002", order_type="SO", line_nb=1,
                              item_nb="ZZDBI3", item_desc="ZZREORDER WIDGET",
                              qty=Decimal("1"), uom="PCS"))
    db_session.flush()

    text = "reorder for Zzreorder Trading same order last time the end"
    voice = _voice(db_session, text)
    result = resolve(db_session, parse(text))
    req = _builder(db_session).build_reorder(voice, result)

    assert req.primary_intent == Intent.repeat_order.value
    assert req.target_order_nb == "ZZ990002"
    assert len(req.lines) == 1
