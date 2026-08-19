from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_operator
from app.errors import (AlreadyCommitted, AlreadyDecided, CustomerNotFound,
                        RequestNotFound, RequestNotReviewable,
                        UnresolvedLines)
from app.models import PendingRequest
from app.schemas.api_in import AcceptIn, CallbackIn, RejectIn
from app.schemas.enums import RequestStatus
from app.services.activity_log import log as log_activity
from app.services.commit import OrderCommitService
from app.services.numbering import OrderNumberService

router = APIRouter(tags=["review"])

DECIDED = {RequestStatus.committed.value, RequestStatus.rejected.value}


def _load_for_decision(s: Session, req_id: int) -> PendingRequest:
    """Fetch a request that is still open to a decision, locking the row.

    Without the status check a committed request could be rejected (or a
    rejected one sent to callback): the buffer row would end up claiming an
    outcome that contradicts the order actually placed, and decided_by /
    decided_at / decision_note would be overwritten with the second caller's.
    """
    r = s.execute(select(PendingRequest)
                  .where(PendingRequest.id == req_id)
                  .with_for_update()).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "no such request")
    if r.status in DECIDED:
        raise AlreadyDecided(r.status, r.committed_order_nb)
    return r


@router.post("/requests/{req_id}/accept")
def accept(req_id: int, body: AcceptIn, s: Session = Depends(get_db),
           operator: str = Depends(get_operator)):
    svc = OrderCommitService(s, OrderNumberService(s))
    try:
        h = svc.commit(req_id, body.order_type, body.lines, operator,
                       body.removed_line_nbs)
    except RequestNotFound:
        raise HTTPException(404, "no such request")
    except CustomerNotFound:
        raise HTTPException(
            404, "request denied: customer number does not exist - please "
                "call again")
    except AlreadyCommitted as e:
        raise HTTPException(409, f"already committed as {e.order_nb}")
    except UnresolvedLines:
        raise HTTPException(422, "every line needs an item and a quantity")
    except RequestNotReviewable:
        raise HTTPException(409, "not reviewable")
    return {"order_nb": h.order_nb, "order_type": h.order_type}


@router.post("/requests/{req_id}/reject")
def reject(req_id: int, body: RejectIn, s: Session = Depends(get_db),
           operator: str = Depends(get_operator)):
    try:
        r = _load_for_decision(s, req_id)
    except AlreadyDecided as e:
        raise HTTPException(409, f"already decided ({e.status})"
                            + (f", order {e.order_nb}" if e.order_nb else ""))
    r.status = RequestStatus.rejected.value
    r.decision_note = f"{body.reason}: {body.note or ''}"
    r.decided_by = operator
    r.decided_at = datetime.now(timezone.utc)
    log_activity(s, "request_rejected",
                f"request {r.id} rejected by {operator}: {body.reason}",
                request_id=r.id, cust_nb=r.cust_nb,
                details={"operator": operator, "reason": body.reason})
    return {"ok": True}


@router.post("/requests/{req_id}/callback")
def callback(req_id: int, body: CallbackIn, s: Session = Depends(get_db),
             operator: str = Depends(get_operator)):
    try:
        r = _load_for_decision(s, req_id)
    except AlreadyDecided as e:
        raise HTTPException(409, f"already decided ({e.status})"
                            + (f", order {e.order_nb}" if e.order_nb else ""))
    r.status = RequestStatus.callback.value
    r.decision_note = body.note
    r.decided_by = operator
    r.decided_at = datetime.now(timezone.utc)
    return {"ok": True}
