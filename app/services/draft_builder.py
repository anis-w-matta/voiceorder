from sqlalchemy import select

from app.models import Item, PendingLine, PendingRequest
from app.schemas.enums import Intent, MatchMethod
from app.services.activity_log import log as log_activity
from app.services.item_classifier import classify_line
from app.services.scripted.models import MatchStatus, ScriptedOrderResult


class DraftBuilder:
    """Turns a resolved scripted command (place_order/return_order/reorder,
    app/services/scripted/resolve_order.py) into a PendingRequest for
    manual review. `catalogue` supplies the known-category list
    classify_line() checks candidate categories against; `prior` supplies
    prior-order lookups for return_order (full return) and reorder.
    """

    def __init__(self, session, prior, catalogue):
        self.s = session
        self.prior = prior
        self.catalogue = catalogue

    def _lines_from_prior_lines(self, prior_lines, raw_text_fn):
        """PendingLine rows built from a list of prior OrderDetail rows -
        shared by _from_prior (reorder) and build_return's full-return
        branch, which used to duplicate this exact lookup+construction and
        had drifted apart: one path ran the result through classify_line's
        tier-2/3 fallback and the other left category=None outright for an
        item that's since left the catalogue, instead of the "unable to
        classify" tier-3 fallback every other PendingLine in the app gets.
        `raw_text_fn(order_detail)` supplies the label distinguishing the
        two callers ("[from order ...]" vs "[return of order ...]").

        Looks up each item's *current* category rather than trusting the
        historical order_details snapshot, which may predate this column
        (NULL) or be stale relative to a catalogue re-categorisation.
        """
        if not prior_lines:
            return []
        items = {i.item_number: i for i in self.s.scalars(select(Item).where(
            Item.item_number.in_({d.item_nb for d in prior_lines})))}
        return [PendingLine(
            line_nb=n, raw_text=raw_text_fn(d),
            item_nb=d.item_nb, item_desc=d.item_desc, qty=d.qty, uom=d.uom,
            match_confidence=1.0, match_method=MatchMethod.prior_order.value,
            category=classify_line(
                matched_category=items[d.item_nb].category
                if d.item_nb in items else None,
                raw_text=d.item_desc, known_categories=[]))
            for n, d in enumerate(prior_lines, start=1)]

    def _from_prior(self, target):
        if not target:
            return []
        prior_lines = self.prior.lines_of(target)
        return self._lines_from_prior_lines(
            prior_lines, lambda d: f"[from order {target.order_nb}]")

    def _pending_lines_from_scripted(self, scripted: ScriptedOrderResult
                                     ) -> list[PendingLine]:
        """PendingLine rows for a scripted result's resolved item lines -
        shared by build_scripted_order (place_order) and build_return's
        partial-return branch."""
        known_categories = self.catalogue.all_categories()
        lines = []
        for n, rl in enumerate(scripted.lines, start=1):
            match, qty = rl.match, rl.qty
            line_flags = []
            if match.status == MatchStatus.AMBIGUOUS:
                line_flags.append("ambiguous_catalogue_match")
            elif match.status == MatchStatus.NOT_FOUND:
                line_flags.append("unknown_alias")
            if qty.status != "matched":
                line_flags.append("quantity_parse_error")
            lines.append(PendingLine(
                line_nb=n, raw_text=rl.raw_item_text,
                item_nb=match.item_number, item_desc=match.item_description,
                qty=qty.quantity, uom=qty.uom,
                match_confidence=(match.score / 100 if match.score is not None
                                  else None),
                match_method=match.method,
                candidates=[{"item_nb": c.item_number,
                            "item_desc": c.item_description,
                            "category": c.item_family or "",
                            "score": c.score,
                            "method": match.method,
                            "attribute_conflict": not c.numeric_compatible}
                           for c in match.candidates[:5]],
                line_flags=line_flags,
                resolution_meta={"explanation": match.explanation,
                                "qty_reason": qty.reason},
                category=classify_line(matched_category=match.item_family,
                                       raw_text=rl.raw_item_text,
                                       known_categories=known_categories)))
        return lines

    def _scope_return_lines_to_order(self, lines: list[PendingLine],
                                     resolved, order_header) -> None:
        """A partial return names items by ear, and catalogue-wide
        resolution (aliases, pg_trgm, fuzzy) can confidently land on an
        item the customer never actually bought on this order - which
        would draft a return for the wrong item. This narrows every line
        to what's actually on order_header: a top-1 catalogue match that
        isn't one of this order's items is refused (not committed), and an
        ambiguous/unmatched line is given a second look restricted to just
        this order's candidates, where the same catalogue ambiguity often
        collapses to one answer (see ItemMatchResult.candidates - already
        scored by resolve_item, only the pool is narrowed here, nothing
        is re-queried from the item table).
        """
        order_item_nbs = {d.item_nb for d in self.prior.lines_of(order_header)}
        for line, rl in zip(lines, resolved):
            if line.item_nb in order_item_nbs:
                continue  # top-1 catalogue match is already on this order
            in_order = sorted(
                (c for c in rl.match.candidates if c.item_number in order_item_nbs),
                key=lambda c: c.score, reverse=True)
            if in_order:
                best = in_order[0]
                line.item_nb = best.item_number
                line.item_desc = best.item_description
                line.match_confidence = best.score / 100
                line.match_method = "fuzzy"
                line.line_flags = [f for f in line.line_flags if f not in
                                   ("ambiguous_catalogue_match", "unknown_alias")]
                line.category = best.item_family
            else:
                line.item_nb = None
                line.match_confidence = None
                if "item_not_in_order" not in line.line_flags:
                    line.line_flags = line.line_flags + ["item_not_in_order"]
            line.resolution_meta = {
                **line.resolution_meta,
                "order_scoped": order_header.order_nb,
                "catalogue_top_match": rl.match.item_number,
                "order_item_numbers": sorted(order_item_nbs)}

    def build_scripted_order(self, voice, scripted: ScriptedOrderResult):
        """place_order -> PendingRequest."""
        cust_nb = (scripted.customer.customer_number
                  if scripted.customer and
                  scripted.customer.status == MatchStatus.MATCHED else None)
        lines = self._pending_lines_from_scripted(scripted)
        flags = list(scripted.errors)
        if scripted.customer and scripted.customer.status != MatchStatus.MATCHED:
            flags.append(f"customer_{scripted.customer.status.value}")
        if not lines:
            flags.append("no_lines")

        req = PendingRequest(
            voice_message_id=voice.id, cust_nb=cust_nb,
            intents=[Intent.add_order.value],
            primary_intent=Intent.add_order.value,
            raw_model_output={"scripted": True, "command_type": "place_order"},
            flags=flags,
            classification_quality="good" if scripted.status == "success"
            else "questionable",
            status="new")
        req.lines = lines
        self.s.add(req)
        self.s.flush()
        if lines:
            log_activity(self.s, "item_classified",
                        f"scripted place_order classified {len(lines)} "
                        f"line(s) for request {req.id}",
                        request_id=req.id, cust_nb=cust_nb)
        return req

    def build_return(self, voice, order_header, scripted: ScriptedOrderResult):
        """return_order -> PendingRequest. A full return (no items spoken)
        copies every line of the referenced order via PriorOrderService,
        mirroring _from_prior's reorder behaviour; a partial return
        resolves only the spoken items through the same scripted item/qty
        pipeline as place_order. `order_header` is the resolved OrderHeader
        for scripted.order_reference, or None if it couldn't be found - a
        return can still be drafted for manual review in that case, just
        with no cust_nb/target set.
        """
        cust_nb = order_header.cust_nb if order_header else None
        if scripted.full_return:
            prior_lines = self.prior.lines_of(order_header) if order_header else []
            lines = self._lines_from_prior_lines(
                prior_lines,
                lambda d: f"[return of order {order_header.order_nb}]")
        else:
            lines = self._pending_lines_from_scripted(scripted)
            if order_header:
                self._scope_return_lines_to_order(lines, scripted.lines,
                                                  order_header)

        flags = []
        if order_header is None:
            flags.append("return_order_reference_not_found")
        if not lines:
            flags.append("no_lines")

        req = PendingRequest(
            voice_message_id=voice.id, cust_nb=cust_nb,
            intents=[Intent.return_order.value],
            primary_intent=Intent.return_order.value,
            target_order_nb=order_header.order_nb if order_header else None,
            target_order_type=order_header.order_type if order_header else None,
            raw_model_output={"scripted": True, "command_type": "return_order",
                              "order_reference": scripted.order_reference,
                              "full_return": scripted.full_return},
            flags=flags,
            classification_quality="good" if scripted.status == "success"
            else "questionable",
            status="new")
        req.lines = lines
        self.s.add(req)
        self.s.flush()
        log_activity(self.s, "return_order_drafted",
                    f"return_order request {req.id} drafted "
                    f"({'full' if scripted.full_return else 'partial'})",
                    request_id=req.id, cust_nb=cust_nb)
        return req

    def build_reorder(self, voice, scripted: ScriptedOrderResult):
        """reorder -> PendingRequest, reusing the existing prior-order
        machinery (PriorOrderService.resolve_target_explicit + _from_prior)."""
        cust_nb = (scripted.customer.customer_number
                  if scripted.customer and
                  scripted.customer.status == MatchStatus.MATCHED else None)

        target, ambiguity = None, "customer_not_resolved"
        if cust_nb:
            target, ambiguity = self.prior.resolve_target_explicit(
                cust_nb, scripted.reorder_mode, scripted.order_reference)
        log_activity(
            self.s, "reorder_resolved",
            f"scripted reorder for {cust_nb} resolved to order "
            f"{target.order_nb}" if target else
            f"scripted reorder for {cust_nb} could not resolve a target "
            f"order ({ambiguity})",
            level="info" if target else "warn", cust_nb=cust_nb,
            voice_message_id=voice.id,
            details={"ambiguity": ambiguity} if ambiguity else {})

        lines = self._from_prior(target)
        flags = [] if target else [f"reorder_{ambiguity}"]
        if scripted.customer and scripted.customer.status != MatchStatus.MATCHED:
            flags.append(f"customer_{scripted.customer.status.value}")

        req = PendingRequest(
            voice_message_id=voice.id, cust_nb=cust_nb,
            intents=[Intent.repeat_order.value],
            primary_intent=Intent.repeat_order.value,
            target_order_nb=target.order_nb if target else None,
            target_order_type=target.order_type if target else None,
            raw_model_output={"scripted": True, "command_type": "reorder",
                              "mode": scripted.reorder_mode,
                              "reference": scripted.order_reference},
            flags=flags,
            classification_quality="good" if target else "questionable",
            status="new")
        req.lines = lines
        self.s.add(req)
        self.s.flush()
        return req
