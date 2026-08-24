from decimal import Decimal

from app.models import Customer, Item, OrderDetail, OrderHeader, VoiceMessage
from app.schemas.enums import Intent
from app.services.catalogue import CatalogueService
from app.services.draft_builder import DraftBuilder
from app.services.prior_order import PriorOrderService
from app.services.scripted.models import (ParsedItemSpan, ParsedPlaceOrder,
                                          ParsedReorder, ParsedReturnOrder)
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
    parsed = ParsedPlaceOrder(customer_text="Zzdraft Trading", items=[
        ParsedItemSpan(item_text="zzdraft widget med 5x2",
                       quantity_text="four", uom_text="cartons")])
    result = resolve(db_session, parsed)
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
    parsed = ParsedReturnOrder(order_reference="ZZ990001", items=[])
    result = resolve(db_session, parsed)
    prior = PriorOrderService(db_session)
    header = prior.find_so_by_order_nb(result.order_reference)
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
    parsed = ParsedReturnOrder(order_reference="00000000000000", items=[])
    result = resolve(db_session, parsed)
    req = _builder(db_session).build_return(voice, None, result)

    assert req.cust_nb is None
    assert "return_order_reference_not_found" in req.flags


def test_find_so_by_order_nb_ignores_return_row_reusing_same_nb(db_session):
    # commit.py reuses a return's target SO's order_nb for the RETURN row
    # it creates (order-number-reuse) - a *second* "return order X"
    # referencing that same number must still resolve to the original SO,
    # not go ambiguous just because a RETURN row now shares its order_nb.
    db_session.add(Customer(customer_number="ZZDB10", customer_name="Zzreuse Trading"))
    db_session.add(Item(item_number="ZZDBI10", item_desc="ZZREUSE WIDGET",
                        category="Misc"))
    db_session.flush()
    db_session.add(OrderHeader(order_nb="ZZ990009", order_type="SO",
                              cust_nb="ZZDB10", status="open"))
    db_session.add(OrderHeader(order_nb="ZZ990009", order_type="RETURN",
                              cust_nb="ZZDB10", status="open"))
    db_session.add(OrderDetail(order_nb="ZZ990009", order_type="SO", line_nb=1,
                              item_nb="ZZDBI10", item_desc="ZZREUSE WIDGET",
                              qty=Decimal("2"), uom="EACH"))
    db_session.flush()

    prior = PriorOrderService(db_session)
    header = prior.find_so_by_order_nb("ZZ990009")

    assert header is not None
    assert header.order_type == "SO"


def test_find_so_by_order_nb_falls_back_to_digits_only(db_session):
    # A real production order_nb is plain digits - if Gemini's extraction
    # leaves stray non-digit text attached to the reference ("order
    # number 260000094"), the exact-match attempt misses, and this must
    # still resolve via the same digits-only normalization
    # resolve_target_explicit's order_nb mode already applies, rather than
    # failing just because this is the customer-agnostic path.
    db_session.add(Customer(customer_number="ZZDB13", customer_name="Zzdigits Trading"))
    db_session.flush()
    db_session.add(OrderHeader(order_nb="260000099", order_type="SO",
                              cust_nb="ZZDB13", status="open"))
    db_session.flush()

    prior = PriorOrderService(db_session)
    header = prior.find_so_by_order_nb("order number 260000099")

    assert header is not None
    assert header.order_nb == "260000099"


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
    parsed = ParsedReturnOrder(order_reference="ZZ990004", items=[
        ParsedItemSpan(item_text="zzscope widget alpha",
                       quantity_text="1", uom_text="")])
    result = resolve(db_session, parsed)
    prior = PriorOrderService(db_session)
    header = prior.find_so_by_order_nb(result.order_reference)
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
    parsed = ParsedReturnOrder(order_reference="ZZ990003", items=[
        ParsedItemSpan(item_text="zzpartial widget beta",
                       quantity_text="1", uom_text="")])
    result = resolve(db_session, parsed)
    prior = PriorOrderService(db_session)
    header = prior.find_so_by_order_nb(result.order_reference)
    req = _builder(db_session).build_return(voice, header, result)

    assert len(req.lines) == 1
    assert req.lines[0].item_nb is None
    assert "item_not_in_order" in req.lines[0].line_flags
    assert req.lines[0].resolution_meta["catalogue_top_match"] == "ZZDBI4B"


def test_build_return_partial_flags_tie_between_two_in_order_candidates(
        db_session):
    # Both ALPHA and BETA are on the order, and "zztie widget" alone (no
    # ALPHA/BETA qualifier) is ambiguous between them catalogue-wide too -
    # scoping to this order's items must not silently coin-flip between
    # two still-tied candidates just because narrowing the pool happened
    # to leave exactly these two standing.
    db_session.add(Customer(customer_number="ZZDB12", customer_name="Zztie Trading"))
    db_session.add(Item(item_number="ZZDBI12A", item_desc="ZZTIE WIDGET ALPHA",
                        category="Misc"))
    db_session.add(Item(item_number="ZZDBI12B", item_desc="ZZTIE WIDGET BETA",
                        category="Misc"))
    db_session.flush()
    db_session.add(OrderHeader(order_nb="ZZ990011", order_type="SO",
                              cust_nb="ZZDB12", status="open"))
    db_session.add(OrderDetail(order_nb="ZZ990011", order_type="SO", line_nb=1,
                              item_nb="ZZDBI12A", item_desc="ZZTIE WIDGET ALPHA",
                              qty=Decimal("1"), uom="EACH"))
    db_session.add(OrderDetail(order_nb="ZZ990011", order_type="SO", line_nb=2,
                              item_nb="ZZDBI12B", item_desc="ZZTIE WIDGET BETA",
                              qty=Decimal("1"), uom="EACH"))
    db_session.flush()

    text = "return order ZZ990011 items zztie widget quantity 1 the end"
    voice = _voice(db_session, text)
    parsed = ParsedReturnOrder(order_reference="ZZ990011", items=[
        ParsedItemSpan(item_text="zztie widget", quantity_text="1", uom_text="")])
    result = resolve(db_session, parsed)
    prior = PriorOrderService(db_session)
    header = prior.find_so_by_order_nb(result.order_reference)
    req = _builder(db_session).build_return(voice, header, result)

    assert len(req.lines) == 1
    assert req.lines[0].item_nb is None
    assert "item_ambiguous_in_order" in req.lines[0].line_flags


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
    parsed = ParsedReorder(customer_text="Zzreorder Trading", mode="last",
                           reference=None)
    result = resolve(db_session, parsed)
    req = _builder(db_session).build_reorder(voice, result)

    assert req.primary_intent == Intent.repeat_order.value
    assert req.target_order_nb == "ZZ990002"
    assert len(req.lines) == 1


def test_build_reorder_adjustment_overrides_existing_line(db_session):
    db_session.add(Customer(customer_number="ZZDB6", customer_name="Zzadjust Trading"))
    db_session.add(Item(item_number="ZZDBI6A", item_desc="ZZADJUST WIDGET ALPHA",
                        category="Misc"))
    db_session.add(Item(item_number="ZZDBI6B", item_desc="ZZADJUST WIDGET BETA",
                        category="Misc"))
    db_session.flush()
    db_session.add(OrderHeader(order_nb="ZZ990005", order_type="SO",
                              cust_nb="ZZDB6", status="open"))
    db_session.add(OrderDetail(order_nb="ZZ990005", order_type="SO", line_nb=1,
                              item_nb="ZZDBI6A", item_desc="ZZADJUST WIDGET ALPHA",
                              qty=Decimal("1"), uom="EACH"))
    db_session.add(OrderDetail(order_nb="ZZ990005", order_type="SO", line_nb=2,
                              item_nb="ZZDBI6B", item_desc="ZZADJUST WIDGET BETA",
                              qty=Decimal("2"), uom="EACH"))
    db_session.flush()

    text = ("reorder for Zzadjust Trading same order but 4 each zzadjust "
            "widget alpha the end")
    voice = _voice(db_session, text)
    parsed = ParsedReorder(customer_text="Zzadjust Trading", mode="last",
                           reference=None, items=[
                               ParsedItemSpan(item_text="zzadjust widget alpha",
                                             quantity_text="4",
                                             uom_text="each")])
    result = resolve(db_session, parsed)
    req = _builder(db_session).build_reorder(voice, result)

    assert req.primary_intent == Intent.repeat_order_adjusted.value
    assert req.target_order_nb == "ZZ990005"
    assert len(req.lines) == 2
    alpha = next(l for l in req.lines if l.item_nb == "ZZDBI6A")
    beta = next(l for l in req.lines if l.item_nb == "ZZDBI6B")
    assert alpha.qty == Decimal("4")
    assert beta.qty == Decimal("2")


def test_build_reorder_adjustment_rescues_ambiguous_item_already_on_order(
        db_session):
    # Two SKUs share an identical description (a real catalogue duplicate,
    # like TENDREX ADULT LRG 12X4 / .../TD) - "4 each of the tendrex adult
    # large 12x4" resolves ambiguously catalogue-wide, but only ONE of the
    # two is on the order being repeated. That's not a coincidence worth
    # re-flagging - it should update that line's quantity, not add an
    # unresolved duplicate for the reviewer to notice is the same product.
    db_session.add(Customer(customer_number="ZZDB14", customer_name="Zzrescue Trading"))
    db_session.add(Item(item_number="ZZDBI14A", item_desc="ZZRESCUE WIDGET",
                        category="Misc"))
    db_session.add(Item(item_number="ZZDBI14B", item_desc="ZZRESCUE WIDGET",
                        category="Misc"))
    db_session.flush()
    db_session.add(OrderHeader(order_nb="ZZ990012", order_type="SO",
                              cust_nb="ZZDB14", status="open"))
    db_session.add(OrderDetail(order_nb="ZZ990012", order_type="SO", line_nb=1,
                              item_nb="ZZDBI14A", item_desc="ZZRESCUE WIDGET",
                              qty=Decimal("3"), uom="EACH"))
    db_session.flush()

    text = ("reorder for Zzrescue Trading same order but 4 each zzrescue "
            "widget the end")
    voice = _voice(db_session, text)
    parsed = ParsedReorder(customer_text="Zzrescue Trading", mode="last",
                           reference=None, items=[
                               ParsedItemSpan(item_text="zzrescue widget",
                                             quantity_text="4",
                                             uom_text="each")])
    result = resolve(db_session, parsed)
    req = _builder(db_session).build_reorder(voice, result)

    assert len(req.lines) == 1
    assert req.lines[0].item_nb == "ZZDBI14A"
    assert req.lines[0].qty == Decimal("4")
    assert "ambiguous_catalogue_match" not in req.lines[0].line_flags


def test_build_reorder_adjustment_appends_new_item(db_session):
    db_session.add(Customer(customer_number="ZZDB7", customer_name="Zzappend Trading"))
    db_session.add(Item(item_number="ZZDBI7A", item_desc="ZZAPPEND WIDGET ALPHA",
                        category="Misc"))
    db_session.add(Item(item_number="ZZDBI7C", item_desc="ZZAPPEND WIDGET GAMMA",
                        category="Misc"))
    db_session.flush()
    db_session.add(OrderHeader(order_nb="ZZ990006", order_type="SO",
                              cust_nb="ZZDB7", status="open"))
    db_session.add(OrderDetail(order_nb="ZZ990006", order_type="SO", line_nb=1,
                              item_nb="ZZDBI7A", item_desc="ZZAPPEND WIDGET ALPHA",
                              qty=Decimal("1"), uom="EACH"))
    db_session.flush()

    text = ("reorder for Zzappend Trading same order but add 3 each "
            "zzappend widget gamma the end")
    voice = _voice(db_session, text)
    parsed = ParsedReorder(customer_text="Zzappend Trading", mode="last",
                           reference=None, items=[
                               ParsedItemSpan(item_text="zzappend widget gamma",
                                             quantity_text="3",
                                             uom_text="each")])
    result = resolve(db_session, parsed)
    req = _builder(db_session).build_reorder(voice, result)

    assert req.primary_intent == Intent.repeat_order_adjusted.value
    assert len(req.lines) == 2
    gamma = next(l for l in req.lines if l.item_nb == "ZZDBI7C")
    assert gamma.qty == Decimal("3")


def test_build_reorder_derives_customer_from_order_nb_when_none_named(
        db_session):
    db_session.add(Customer(customer_number="ZZDB8", customer_name="Zznocust Trading"))
    db_session.add(Item(item_number="ZZDBI8", item_desc="ZZNOCUST WIDGET",
                        category="Misc"))
    db_session.flush()
    db_session.add(OrderHeader(order_nb="ZZ990007", order_type="SO",
                              cust_nb="ZZDB8", status="open"))
    db_session.add(OrderDetail(order_nb="ZZ990007", order_type="SO", line_nb=1,
                              item_nb="ZZDBI8", item_desc="ZZNOCUST WIDGET",
                              qty=Decimal("1"), uom="EACH"))
    db_session.flush()

    text = "reorder order ZZ990007 but 4 each zznocust widget the end"
    voice = _voice(db_session, text)
    # No customer named at all - only mode=order_nb + a reference, mirroring
    # the reported bug's transcript. An order number identifies its own
    # customer, the same way return_order already resolves cust_nb from
    # target_order_nb without needing a spoken name.
    parsed = ParsedReorder(customer_text="", mode="order_nb",
                           reference="ZZ990007", items=[
                               ParsedItemSpan(item_text="zznocust widget",
                                             quantity_text="4",
                                             uom_text="each")])
    result = resolve(db_session, parsed)
    req = _builder(db_session).build_reorder(voice, result)

    assert req.primary_intent == Intent.repeat_order_adjusted.value
    assert req.cust_nb == "ZZDB8"
    assert req.target_order_nb == "ZZ990007"
    assert req.flags == []
    assert len(req.lines) == 1
    assert req.lines[0].item_nb == "ZZDBI8"
    assert req.lines[0].qty == Decimal("4")


def test_build_reorder_flags_mismatch_between_named_customer_and_order(
        db_session):
    db_session.add(Customer(customer_number="ZZDB9A", customer_name="Zzreal Trading"))
    db_session.add(Customer(customer_number="ZZDB9B", customer_name="Zzwrong Trading"))
    db_session.add(Item(item_number="ZZDBI9", item_desc="ZZMISMATCH WIDGET",
                        category="Misc"))
    db_session.flush()
    db_session.add(OrderHeader(order_nb="ZZ990008", order_type="SO",
                              cust_nb="ZZDB9A", status="open"))
    db_session.add(OrderDetail(order_nb="ZZ990008", order_type="SO", line_nb=1,
                              item_nb="ZZDBI9", item_desc="ZZMISMATCH WIDGET",
                              qty=Decimal("1"), uom="EACH"))
    db_session.flush()

    text = "reorder for Zzwrong Trading order ZZ990008 the end"
    voice = _voice(db_session, text)
    # Order ZZ990008 actually belongs to Zzreal Trading, not the named
    # Zzwrong Trading - the order still wins (same trust an operator's
    # explicit target_order_nb correction gets at commit time), but the
    # disagreement is flagged rather than passing silently.
    parsed = ParsedReorder(customer_text="Zzwrong Trading", mode="order_nb",
                           reference="ZZ990008")
    result = resolve(db_session, parsed)
    req = _builder(db_session).build_reorder(voice, result)

    assert req.cust_nb == "ZZDB9A"
    assert req.target_order_nb == "ZZ990008"
    assert "reorder_customer_mismatch" in req.flags


def test_build_reorder_adjustment_same_item_two_uoms_both_kept(db_session):
    # A spoken adjustment can name the same item in two different units in
    # one utterance ("... and also make it 2 packets") - resolve_order's
    # _merge_duplicate_lines only merges same-item mentions that share a
    # UOM, so this legitimately produces two separate adjustment lines for
    # the same item_nb. _merge_adjustment_lines must keep both (as two
    # lines, same as any other two distinct lines for the same item in
    # different units elsewhere in the app) rather than the second
    # silently overwriting the first just because they resolve to the same
    # prior-order slot.
    db_session.add(Customer(customer_number="ZZDB11", customer_name="Zzdualuom Trading"))
    db_session.add(Item(item_number="ZZDBI11", item_desc="ZZDUALUOM WIDGET",
                        category="Misc"))
    db_session.flush()
    db_session.add(OrderHeader(order_nb="ZZ990010", order_type="SO",
                              cust_nb="ZZDB11", status="open"))
    db_session.add(OrderDetail(order_nb="ZZ990010", order_type="SO", line_nb=1,
                              item_nb="ZZDBI11", item_desc="ZZDUALUOM WIDGET",
                              qty=Decimal("1"), uom="EACH"))
    db_session.flush()

    text = ("reorder for Zzdualuom Trading same order but 4 each "
            "zzdualuom widget and also 2 packets zzdualuom widget the end")
    voice = _voice(db_session, text)
    parsed = ParsedReorder(customer_text="Zzdualuom Trading", mode="last",
                           reference=None, items=[
                               ParsedItemSpan(item_text="zzdualuom widget",
                                             quantity_text="4",
                                             uom_text="each"),
                               ParsedItemSpan(item_text="zzdualuom widget",
                                             quantity_text="2",
                                             uom_text="packets")])
    result = resolve(db_session, parsed)
    req = _builder(db_session).build_reorder(voice, result)

    each_lines = [l for l in req.lines
                 if l.item_nb == "ZZDBI11" and l.uom == "EACH"]
    pkt_lines = [l for l in req.lines
                if l.item_nb == "ZZDBI11" and l.uom == "PKT"]
    assert len(each_lines) == 1 and each_lines[0].qty == Decimal("4")
    assert len(pkt_lines) == 1 and pkt_lines[0].qty == Decimal("2")
