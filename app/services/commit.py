from datetime import datetime, timezone

from sqlalchemy import select

from app.errors import (AlreadyCommitted, RequestNotFound,
                        RequestNotReviewable, UnresolvedLines)
from app.models import (Item, OrderDetail, OrderHeader, PendingLine,
                        PendingRequest)
from app.schemas.enums import RequestStatus
from app.services.activity_log import log as log_activity
from app.services.activity_log import log_standalone
from app.services.item_classifier import UNABLE_TO_CLASSIFY


class OrderCommitService:
    def __init__(self, session, numbering):
        self.s = session
        self.numbering = numbering

    def commit(self, request_id: int, order_type: str, line_edits,
               operator: str, removed_line_nbs=None) -> OrderHeader:
        req = self.s.execute(
            select(PendingRequest).where(PendingRequest.id == request_id)
            .with_for_update()).scalar_one_or_none()
        if req is None:
            raise RequestNotFound(request_id)

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

        self._apply_edits(req, line_edits, removed_line_nbs or [])

        if not req.lines or any(l.item_nb is None or l.qty is None
                                for l in req.lines):
            raise UnresolvedLines()

        order_nb = self.numbering.next()
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
                                   qty=line.qty, uom=line.uom or "PCS",
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
                    details={"operator": operator, "order_type": order_type})
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
                line.uom = e.uom
