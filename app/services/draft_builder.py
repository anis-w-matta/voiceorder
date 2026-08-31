from app.models import PendingLine, PendingRequest
from app.schemas.enums import Intent, MatchMethod
from app.services import catalog_client
from app.services.activity_log import log as log_activity
from app.services.item_classifier import classify_line
from app.services.scripted.config import (ITEM_AMBIGUITY_MARGIN,
                                          ITEM_FUZZY_THRESHOLD)
from app.services.scripted.models import MatchStatus, ScriptedOrderResult


def lines_from_prior_order(prior_lines, raw_text_fn):
    """PendingLine rows built from a list of prior OrderDetail rows -
    shared by DraftBuilder's _from_prior (reorder) and build_return's
    full-return branch, and by commit.py's target_order_nb_override path
    (a full return whose reference resolved only at accept time still
    needs its lines populated from the now-known order, the same way
    build_return would have at draft time). A module-level function
    rather than a DraftBuilder method since it only ever needed `session`,
    not any of DraftBuilder's other collaborators.

    Drops any line the prior order recorded as a QRA free bonus
    (is_free=True): every caller here re-runs apply_qra on the paid lines
    this returns before commit, so carrying a previously-free line forward
    would both re-price it as a paid item and double it up against the
    bonus line QRA freshly computes from the paid quantity. A salesman who
    explicitly wants that item repeated as a real paid line has to say so
    - that lands as its own adjustment line (_merge_adjustment_lines),
    never through this baseline copy.

    Looks up each item's *current* category rather than trusting the
    historical order_details snapshot, which may predate this column
    (NULL) or be stale relative to a catalogue re-categorisation.
    `raw_text_fn(order_detail)` supplies the caller-specific label
    ("[from order ...]" / "[return of order ...]").
    """
    prior_lines = [d for d in prior_lines if not d.is_free]
    if not prior_lines:
        return []
    categories = catalog_client.get_items_batch(
        [d.item_nb for d in prior_lines if d.item_nb])
    return [PendingLine(
        line_nb=n, raw_text=raw_text_fn(d),
        item_nb=d.item_nb, item_desc=d.item_desc, qty=d.qty, uom=d.uom,
        match_confidence=1.0, match_method=MatchMethod.prior_order.value,
        category=classify_line(
            matched_category=categories.get(d.item_nb),
            raw_text=d.item_desc, known_categories=[]))
        for n, d in enumerate(prior_lines, start=1)]


def _confident_candidate_among(candidates, allowed_item_nbs):
    """The single candidate confidently matching among `allowed_item_nbs`,
    or None - the same bar an ordinary top-1 catalogue match has to clear
    (ITEM_FUZZY_THRESHOLD, and a real margin over the runner-up), not just
    whichever scores highest among the allowed set. Shared by
    DraftBuilder._scope_return_lines_to_order (a return's items must be on
    the order it's returning against) and _merge_adjustment_lines (a
    reorder adjustment that's ambiguous catalogue-wide - e.g. two SKUs
    sharing an identical description - often isn't ambiguous once
    narrowed to what's already on the order being repeated).
    """
    narrowed = sorted(
        (c for c in candidates if c.item_number in allowed_item_nbs),
        key=lambda c: c.score, reverse=True)
    if not narrowed or narrowed[0].score < ITEM_FUZZY_THRESHOLD:
        return None
    if (len(narrowed) > 1 and
            narrowed[0].score - narrowed[1].score < ITEM_AMBIGUITY_MARGIN):
        return None
    return narrowed[0]


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

    def _from_prior(self, target):
        if not target:
            return []
        prior_lines = self.prior.lines_of(target)
        return lines_from_prior_order(
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

        The narrowed pool still has to clear the same bar an ordinary
        top-1 match does - ITEM_FUZZY_THRESHOLD, and a real margin over
        the runner-up - rather than auto-accepting whatever scores highest
        among "on this order" candidates no matter how weak, or silently
        picking one side of a genuine tie the way match_item.py's
        unique_top/tied_with_top would never allow.
        """
        order_item_nbs = {d.item_nb for d in self.prior.lines_of(order_header)}
        for line, rl in zip(lines, resolved):
            if line.item_nb in order_item_nbs:
                continue  # top-1 catalogue match is already on this order
            best = _confident_candidate_among(rl.match.candidates, order_item_nbs)
            if best is not None:
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
                # Distinguish "nothing on this order plausibly matches"
                # from "more than one thing on this order plausibly
                # matches" - the latter is a real ambiguity the reviewer
                # needs to pick between, not a "wrong item" report.
                in_order = any(c.item_number in order_item_nbs
                              for c in rl.match.candidates)
                flag = ("item_ambiguous_in_order" if in_order
                       else "item_not_in_order")
                if flag not in line.line_flags:
                    line.line_flags = line.line_flags + [flag]
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
            lines = lines_from_prior_order(
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

    def _merge_adjustment_lines(self, prior_lines: list[PendingLine],
                                adjustment_lines: list[PendingLine],
                                adjustment_resolved
                                ) -> list[PendingLine]:
        """Applies a reorder's spoken adjustments on top of the copied
        prior-order lines: an adjustment that resolves to an item already
        on the prior order *in the same unit* overrides that line's qty
        (the reviewer sees the new value, not two lines to reconcile by
        hand); one that resolves to a different item, the same item in a
        different unit, or doesn't resolve at all, is appended as a new
        line for the reviewer to confirm, never silently dropped. Keying
        on (item_nb, uom) rather than item_nb alone matters: two spoken
        adjustments for the same item in different units ("4 each X ...
        and also 2 packets X") must both survive as separate lines, not
        have the second overwrite the first just because they'd otherwise
        collide on the same prior-order slot - the same "never guess
        across a unit mismatch" rule resolve_order.py's
        _merge_duplicate_lines already applies within one utterance.

        Before falling back to "append as a new line", an adjustment that
        didn't resolve to a single item catalogue-wide (e.g. "tendrex
        adult large 12x4" ambiguous between two SKUs sharing a description)
        is given the same second look _scope_return_lines_to_order gives a
        return's items: narrowed to just what's already on the order being
        repeated. "4 each of the tendrex" almost always means the tendrex
        already on this order, not an instruction to leave the line
        unresolved for a human to notice it's the same product.
        `adjustment_resolved` is scripted.lines - the ResolvedOrderLine
        list `adjustment_lines` was built from, same order, needed here
        for its ItemMatchResult.candidates.
        """
        merged = list(prior_lines)
        prior_item_nbs = {l.item_nb for l in merged if l.item_nb}
        index_by_key = {(l.item_nb, l.uom): i for i, l in enumerate(merged)
                        if l.item_nb}
        for adj, rl in zip(adjustment_lines, adjustment_resolved):
            if adj.item_nb is None:
                best = _confident_candidate_among(rl.match.candidates,
                                                  prior_item_nbs)
                if best is not None:
                    adj.item_nb = best.item_number
                    adj.item_desc = best.item_description
                    adj.match_confidence = best.score / 100
                    adj.match_method = "fuzzy"
                    adj.line_flags = [f for f in adj.line_flags if f not in
                                      ("ambiguous_catalogue_match",
                                       "unknown_alias")]
                    adj.category = best.item_family
            idx = (index_by_key.get((adj.item_nb, adj.uom))
                  if adj.item_nb else None)
            if idx is None:
                merged.append(adj)
            else:
                adj.line_nb = merged[idx].line_nb
                merged[idx] = adj
        for n, line in enumerate(merged, start=1):
            line.line_nb = n
        return merged

    def build_reorder(self, voice, scripted: ScriptedOrderResult):
        """reorder -> PendingRequest, reusing the existing prior-order
        machinery (PriorOrderService.resolve_target_explicit + _from_prior).
        A reorder that also names adjustment items (scripted.lines) merges
        them onto the copied prior lines and is flagged as an adjusted
        repeat rather than a plain one."""
        cust_nb = (scripted.customer.customer_number
                  if scripted.customer and
                  scripted.customer.status == MatchStatus.MATCHED else None)

        # An order number, unlike a customer name, identifies its own
        # customer unambiguously - mode=order_nb resolves the target (and
        # from it, the customer) straight from order_header first, the
        # same way return_order already trusts an order reference without
        # requiring a spoken customer name at all. This is what lets
        # "reorder order 12345 but ..." resolve even when no customer was
        # named, or was misheard.
        target, ambiguity = None, "customer_not_resolved"
        customer_mismatch = False
        if scripted.reorder_mode == "order_nb" and scripted.order_reference:
            target = self.prior.find_so_by_order_nb(scripted.order_reference)
            ambiguity = None if target else "order_not_found"
        if target is None and cust_nb:
            target, ambiguity = self.prior.resolve_target_explicit(
                cust_nb, scripted.reorder_mode, scripted.order_reference)
        if target and cust_nb and target.cust_nb != cust_nb:
            # The order itself is authoritative (same trust the operator's
            # target_order_nb correction gets at commit time, in
            # commit.py) - proceed under the order's real customer, but
            # flag the disagreement so a reviewer notices a possible
            # misheard customer name rather than it passing silently.
            customer_mismatch = True
        if target:
            cust_nb = target.cust_nb

        log_activity(
            self.s, "reorder_resolved",
            f"scripted reorder for {cust_nb} resolved to order "
            f"{target.order_nb}" if target else
            f"scripted reorder for {cust_nb} could not resolve a target "
            f"order ({ambiguity})",
            level="info" if target else "warn", cust_nb=cust_nb,
            voice_message_id=voice.id,
            details={"ambiguity": ambiguity} if ambiguity else {})

        has_adjustment = bool(scripted.lines)
        prior_lines = self._from_prior(target)
        if has_adjustment:
            adjustment_lines = self._pending_lines_from_scripted(scripted)
            lines = self._merge_adjustment_lines(prior_lines, adjustment_lines,
                                                 scripted.lines)
        else:
            lines = prior_lines

        flags = [] if target else [f"reorder_{ambiguity}"]
        # Once the target resolves, cust_nb is known (from the order
        # itself if the spoken name didn't independently match) - a stale
        # "customer_not_found/ambiguous" flag about the name match alone
        # would be misleading noise at that point.
        if (not target and scripted.customer and
                scripted.customer.status != MatchStatus.MATCHED):
            flags.append(f"customer_{scripted.customer.status.value}")
        if customer_mismatch:
            flags.append("reorder_customer_mismatch")

        intent = (Intent.repeat_order_adjusted if has_adjustment
                 else Intent.repeat_order)
        req = PendingRequest(
            voice_message_id=voice.id, cust_nb=cust_nb,
            intents=[intent.value],
            primary_intent=intent.value,
            target_order_nb=target.order_nb if target else None,
            target_order_type=target.order_type if target else None,
            raw_model_output={"scripted": True, "command_type": "reorder",
                              "mode": scripted.reorder_mode,
                              "reference": scripted.order_reference,
                              "adjusted": has_adjustment},
            flags=flags,
            classification_quality="good" if target else "questionable",
            status="new")
        req.lines = lines
        self.s.add(req)
        self.s.flush()
        return req
