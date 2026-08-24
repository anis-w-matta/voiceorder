from datetime import datetime, timezone

from sqlalchemy import select

from app.errors import (AlreadyCommitted, CustomerNotFound, RequestNotFound,
                        RequestNotReviewable, TargetOrderNotFound,
                        UnresolvedLines)
from app.models import (Customer, Item, OrderDetail, OrderHeader, PendingLine,
                        PendingRequest)
from app.schemas.enums import Intent, RequestStatus
from app.services.activity_log import log as log_activity
from app.services.activity_log import log_standalone
from app.services.alias_learning import maybe_learn_alias
from app.services.draft_builder import lines_from_prior_order
from app.services.item_classifier import UNABLE_TO_CLASSIFY
from app.services.prior_order import PriorOrderService
from app.services.quantity_uom import canonical_uom

# The business only orders in two units (see quantity_uom.py's
# UOM_SYNONYMS docstring) - enforced again here, not just in the Android
# picker that now only ever sends one of these two, since nothing stops a
# raw API caller from sending anything else.
VALID_UOMS = {"EACH", "PKT"}


class OrderCommitService:
    def __init__(self, session, numbering):
        self.s = session
        self.numbering = numbering

    def commit(self, request_id: int, order_type: str, line_edits,
               operator: str, removed_line_nbs=None,
               cust_nb_override: str | None = None,
               target_order_nb_override: str | None = None) -> OrderHeader:
        req = self.s.execute(
            select(PendingRequest).where(PendingRequest.id == request_id)
            .with_for_update()).scalar_one_or_none()
        if req is None:
            raise RequestNotFound(request_id)

        # A return's customer is never picked independently - it's pulled
        # from the sales order it's returning against, the same way
        # draft_builder.build_return already does when the order reference
        # resolves cleanly at draft time. This is the same resolution for
        # when it *didn't* (or the operator is correcting a wrong one): an
        # operator-supplied order number here re-derives cust_nb from that
        # order rather than requiring (or allowing) a separate customer pick.
        if order_type == "RETURN" and target_order_nb_override:
            target = self.s.get(OrderHeader, (target_order_nb_override, "SO"))
            if target is None:
                raise TargetOrderNotFound(target_order_nb_override)
            req.target_order_nb = target_order_nb_override
            req.cust_nb = target.cust_nb
            # A full return (no items spoken) whose reference failed to
            # resolve at draft time drafts with zero lines - draft_builder
            # only copies lines from an order it actually found
            # (build_return). Now that the operator has corrected the
            # order number, backfill those lines from it the same way
            # build_return would have at draft time, rather than leaving
            # the request stuck on UnresolvedLines with no way to finish
            # accepting short of manually re-adding every item by hand.
            # Only when the operator isn't also submitting their own
            # line_edits in this same request - respect a manually-built
            # line list rather than injecting the whole order under it
            # unasked.
            was_full_return = bool(
                req.raw_model_output and req.raw_model_output.get("full_return"))
            if not req.lines and not line_edits and was_full_return:
                prior_lines = PriorOrderService(self.s).lines_of(target)
                req.lines = lines_from_prior_order(
                    self.s, prior_lines,
                    lambda d: f"[return of order {target.order_nb}]")
        elif (order_type == "SO" and target_order_nb_override and
              req.primary_intent in (Intent.repeat_order.value,
                                     Intent.repeat_order_adjusted.value)):
            # Same correction for a reorder whose target didn't resolve at
            # draft time (customer not named/matched, or a misheard order
            # number) - an order number identifies its own customer
            # unambiguously, the same way build_reorder's mode=order_nb
            # path already resolves one without a spoken customer name
            # (prior_order.py's find_so_by_order_nb).
            prior = PriorOrderService(self.s)
            target = prior.find_so_by_order_nb(target_order_nb_override)
            if target is None:
                raise TargetOrderNotFound(target_order_nb_override)
            req.target_order_nb = target.order_nb
            req.target_order_type = target.order_type
            req.cust_nb = target.cust_nb
            if not line_edits:
                # Whatever's already in req.lines at this point is exactly
                # the adjustment items drafted against no known target
                # (build_reorder merges adjustments onto an empty prior
                # when the target didn't resolve) - merge them onto the
                # now-known order's own lines the same way
                # DraftBuilder._merge_adjustment_lines does at draft time,
                # rather than discarding the salesman's actual spoken
                # change or the prior order's other lines.
                baseline = lines_from_prior_order(
                    self.s, prior.lines_of(target),
                    lambda d: f"[from order {target.order_nb}]")
                merged = list(baseline)
                index_by_key = {(l.item_nb, l.uom): i
                                for i, l in enumerate(merged) if l.item_nb}
                for adj in req.lines:
                    idx = (index_by_key.get((adj.item_nb, adj.uom))
                          if adj.item_nb else None)
                    if idx is None:
                        merged.append(adj)
                    else:
                        merged[idx] = adj
                for n, line in enumerate(merged, start=1):
                    line.line_nb = n
                req.lines = merged
        elif cust_nb_override:
            req.cust_nb = cust_nb_override

        if req.status == RequestStatus.committed.value:
            log_standalone("update_rejected",
                          f"update attempt on request {req.id} refused: "
                          f"already committed as {req.committed_order_nb}",
                          level="warn", request_id=req.id, cust_nb=req.cust_nb,
                          order_nb=req.committed_order_nb)
            raise AlreadyCommitted(req.committed_order_nb)
        if req.status == RequestStatus.rejected.value:
            log_standalone("update_rejected",
                          f"update attempt on request {req.id} refused: "
                          f"request was rejected, not in buffer state",
                          level="warn", request_id=req.id, cust_nb=req.cust_nb)
            raise RequestNotReviewable()

        # Database is the source of truth for customer identity: a request
        # must never be committed into an order for a customer number that
        # doesn't actually exist, and there is no operator action that can
        # override this - it is checked here regardless of how the request
        # got its cust_nb.
        if not req.cust_nb or self.s.get(Customer, req.cust_nb) is None:
            log_standalone(
                "commit_denied",
                f"commit of request {req.id} refused: customer "
                f"{req.cust_nb!r} does not exist", level="warn",
                request_id=req.id, cust_nb=req.cust_nb)
            raise CustomerNotFound(req.cust_nb)

        self._apply_edits(req, line_edits, removed_line_nbs or [])

        if not req.lines or any(
            l.item_nb is None or l.qty is None or
            canonical_uom(l.uom) not in VALID_UOMS for l in req.lines
        ):
            raise UnresolvedLines()

        # A return references the order it's returning against
        # (target_order_nb, set when the return's order reference resolved -
        # see draft_builder.py's build_return). Reusing that number instead
        # of minting a fresh one keeps a return and the order it belongs to
        # under the same document number, so a reviewer can tell they're
        # related at a glance. (order_nb, order_type) is the primary key, so
        # this is only safe while no RETURN row already exists for it - a
        # second return of the same order still gets its own number rather
        # than colliding with the first.
        reused_nb = (order_type == "RETURN" and req.target_order_nb and
                    self.s.get(OrderHeader,
                               (req.target_order_nb, order_type)) is None)
        order_nb = req.target_order_nb if reused_nb else self.numbering.next()
        self.s.add(OrderHeader(order_nb=order_nb, order_type=order_type,
                               cust_nb=req.cust_nb, status="open",
                               source="voice"))
        item_nbs = {line.item_nb for line in req.lines if line.item_nb}
        items = dict(self.s.execute(
            select(Item.item_number, Item).where(
                Item.item_number.in_(item_nbs))).all())
        for i, line in enumerate(req.lines, start=1):
            item = items.get(line.item_nb)
            self.s.add(OrderDetail(order_nb=order_nb, order_type=order_type,
                                   line_nb=i, item_nb=line.item_nb,
                                   item_desc=line.item_desc or "",
                                   qty=line.qty, uom=canonical_uom(line.uom),
                                   unit_price=item.unit_price if item else None,
                                   category=item.category if item else
                                   (line.category or UNABLE_TO_CLASSIFY)))

        req.status = RequestStatus.committed.value
        req.committed_order_nb = order_nb
        req.decided_by = operator
        req.decided_at = datetime.now(timezone.utc)
        self.s.flush()
        log_activity(self.s, "order_committed",
                    f"request {req.id} committed as order {order_nb}",
                    request_id=req.id, cust_nb=req.cust_nb, order_nb=order_nb,
                    details={"operator": operator, "order_type": order_type,
                             "primary_intent": req.primary_intent})
        return self.s.get(OrderHeader, (order_nb, order_type))

    def _apply_edits(self, req, line_edits, removed_line_nbs):
        by_nb = {l.line_nb: l for l in req.lines}
        # Only drop what the caller explicitly asked to drop. Previously any
        # line absent from `line_edits` was deleted, so a caller sending a
        # partial edit list silently committed an order missing items.
        removed = set(removed_line_nbs)
        for nb in removed:
            line = by_nb.pop(nb, None)
            if line is not None:
                req.lines.remove(line)
        for e in line_edits:
            if e.line_nb in removed:
                continue
            line = by_nb.get(e.line_nb)
            if not line:
                line = PendingLine(line_nb=e.line_nb,
                                   raw_text="[operator added]",
                                   match_method="manual", operator_edited=True)
                req.lines.append(line)
                by_nb[e.line_nb] = line
            if e.item_nb is not None and e.item_nb != line.item_nb:
                suggested = (line.candidates[0]["item_nb"]
                            if line.candidates else None)
                maybe_learn_alias(
                    self.s, raw_text=line.raw_text, item_nb=e.item_nb,
                    suggested_item_nb=suggested, remember=e.remember_alias)
                line.item_nb = e.item_nb
                line.match_method = "manual"
                line.operator_edited = True
                # The category picked at intake time described the *old*
                # item; leaving it in place after an operator repoints the
                # line at a different item would mislabel it silently.
                new_item = self.s.get(Item, e.item_nb)
                line.category = new_item.category if new_item else \
                    UNABLE_TO_CLASSIFY
            if e.item_desc is not None:
                line.item_desc = e.item_desc
            if e.qty is not None and e.qty != line.qty:
                line.qty = e.qty
                line.operator_edited = True
            if e.uom is not None:
                # Store the canonical code (EACH/PKT) rather than whatever
                # casing/synonym the caller sent, so every line.uom in the
                # DB is comparable without a second normalization pass -
                # falls back to the raw value if it doesn't canonicalize,
                # letting the VALID_UOMS check above catch and report it
                # rather than silently storing an unrecognized unit.
                line.uom = canonical_uom(e.uom) or e.uom
