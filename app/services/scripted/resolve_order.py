"""Orchestrator: a ParsedCommand (from GeminiCommandExtractor) -> a
ScriptedOrderResult (spec section 27). Wires together
match_customer/match_qty_uom/match_item for each slot the extractor
produced. Never returns "success" on a partially
resolved order (spec: "customer matched, item1 matched, item2 ambiguous"
must be needs_confirmation, not success).

This module does not touch the database beyond read-only resolution calls
(customer/item lookups) - building PendingRequest/PendingLine rows and
resolving a return/reorder target order stays in
app/services/draft_builder.py and app/services/prior_order.py, which
already own that responsibility for the rest of the app.
"""
from sqlalchemy.orm import Session

from app.services.scripted.match_customer import match_customer
from app.services.scripted.match_item import resolve_item
from app.services.scripted.match_qty_uom import parse_quantity_uom_span
from app.services.scripted.models import (MatchStatus, ParsedItemSpan,
                                          ParsedPlaceOrder, ParsedReorder,
                                          ParsedReturnOrder, ParseFailure,
                                          QuantityUOM, ResolvedOrderLine,
                                          ScriptedOrderResult)


def _resolve_lines(session: Session, items: list[ParsedItemSpan]
                   ) -> list[ResolvedOrderLine]:
    lines = []
    for item in items:
        qty = parse_quantity_uom_span(item.quantity_text, item.uom_text)
        match = resolve_item(session, item.item_text)
        lines.append(ResolvedOrderLine(raw_item_text=item.item_text,
                                       qty=qty, match=match))
    return _merge_duplicate_lines(lines)


def _merge_duplicate_lines(lines: list[ResolvedOrderLine]
                           ) -> list[ResolvedOrderLine]:
    """Sums repeated mentions of the same item into one line - "1 tendrex
    adult large 12x4 ... 3 tendrex adult large 12x4" is one order for 4, not
    two separate lines a reviewer has to notice and reconcile by hand
    (spec: a spoken order is what the customer wants, not a transcript of
    how they happened to phrase it).

    Only merges lines that resolved cleanly to the *same* item AND share a
    UOM - a matched item with an unmatched/ambiguous twin, or the same item
    quoted in two different units, still needs a human to reconcile it
    rather than have this guess which quantity is real or silently sum
    incompatible units.
    """
    merged: list[ResolvedOrderLine] = []
    index_by_key: dict[tuple[str, str], int] = {}
    for line in lines:
        mergeable = (line.match.status == MatchStatus.MATCHED and
                    line.match.item_number is not None and
                    line.qty.status == "matched" and
                    line.qty.quantity is not None)
        key = ((line.match.item_number or ""),
              (line.qty.uom or "").strip().upper()) if mergeable else None
        existing_idx = index_by_key.get(key) if key else None
        if existing_idx is None:
            merged.append(line)
            if key:
                index_by_key[key] = len(merged) - 1
            continue
        existing = merged[existing_idx]
        merged[existing_idx] = ResolvedOrderLine(
            raw_item_text=f"{existing.raw_item_text}; {line.raw_item_text}",
            qty=QuantityUOM(
                quantity=existing.qty.quantity + line.qty.quantity,
                uom=existing.qty.uom,
                raw_text=f"{existing.qty.raw_text}; {line.qty.raw_text}",
                status="matched"),
            match=existing.match)
    return merged


def _lines_all_clean(lines: list[ResolvedOrderLine]) -> bool:
    return all(l.qty.status == "matched" and
              l.match.status == MatchStatus.MATCHED for l in lines)


def resolve_place_order(session: Session, parsed: ParsedPlaceOrder
                        ) -> ScriptedOrderResult:
    customer = match_customer(session, parsed.customer_text)
    errors = []
    if not parsed.items:
        errors.append("NO_ITEMS_FOUND")
    lines = _resolve_lines(session, parsed.items)

    success = (customer.status == MatchStatus.MATCHED and bool(lines)
              and _lines_all_clean(lines))
    return ScriptedOrderResult(
        status="success" if success else "needs_confirmation",
        command_type="place_order", customer=customer, lines=lines,
        errors=errors)


def resolve_return_order(session: Session, parsed: ParsedReturnOrder
                         ) -> ScriptedOrderResult:
    lines = _resolve_lines(session, parsed.items)
    full_return = parsed.is_full_return
    success = full_return or (bool(lines) and _lines_all_clean(lines))
    return ScriptedOrderResult(
        status="success" if success else "needs_confirmation",
        command_type="return_order", lines=lines,
        order_reference=parsed.order_reference, full_return=full_return)


def resolve_reorder(session: Session, parsed: ParsedReorder
                    ) -> ScriptedOrderResult:
    customer = match_customer(session, parsed.customer_text)
    lines = _resolve_lines(session, parsed.items)
    # _lines_all_clean([]) is vacuously True, so a plain (unadjusted)
    # reorder's success still hinges on the customer alone.
    success = customer.status == MatchStatus.MATCHED and _lines_all_clean(lines)
    return ScriptedOrderResult(
        status="success" if success else "needs_confirmation",
        command_type="reorder", customer=customer, lines=lines,
        order_reference=parsed.reference, reorder_mode=parsed.mode)


def resolve(session: Session,
           parsed: ParsedPlaceOrder | ParsedReturnOrder | ParsedReorder | ParseFailure
           ) -> ScriptedOrderResult:
    if isinstance(parsed, ParseFailure):
        return ScriptedOrderResult(status="parse_error", command_type=None,
                                   errors=[parsed.error.value, parsed.detail])
    if isinstance(parsed, ParsedPlaceOrder):
        return resolve_place_order(session, parsed)
    if isinstance(parsed, ParsedReturnOrder):
        return resolve_return_order(session, parsed)
    return resolve_reorder(session, parsed)
