from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_salesman, get_db
from app.errors import (AlreadyDecided, CustomerNotAuthorized,
                        CustomerNotFound, OrderAlreadyReturned,
                        RequestNotFound, RequestNotReviewable,
                        TargetOrderNotFound, UnresolvedLines)
from app.models import PendingRequest, Salesman
from app.schemas.api_in import AcceptIn, CallbackIn, RejectIn
from app.schemas.enums import RequestStatus
from app.services.activity_log import log as log_activity
from app.services.authorization import (NOT_AUTHORIZED_DETAIL,
                                        require_customer_ownership)
from app.services.catalog_client import CommitTransientError
from app.services.commit import OrderCommitService
from app.services.numbering import OrderNumberService

router = APIRouter(tags=["review"])

DECIDED = {RequestStatus.committed.value, RequestStatus.rejected.value,
          RequestStatus.committing.value}


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
           salesman: Salesman = Depends(get_current_salesman)):
    svc = OrderCommitService(s, OrderNumberService(s))
    try:
        h = svc.commit(req_id, body.order_type, body.lines, salesman.login_id,
                       body.removed_line_nbs, body.cust_nb,
                       body.target_order_nb, acting_is_admin=salesman.is_admin)
    except RequestNotFound:
        # Also covers a retried/duplicate accept on a request that's
        # already committed: its row no longer exists once its order does
        # (see OrderCommitService._finalize_committed), so there's no
        # "already committed as X" status left to report - just 404, same
        # as a request that never existed.
        raise HTTPException(404, "no such request")
    except CustomerNotFound:
        raise HTTPException(
            404, "request denied: customer number does not exist - please "
                "call again")
    except CustomerNotAuthorized:
        # The definitive, database-backed ownership check - see
        # catalog-service's POST /orders and app/services/commit.py. Zero
        # order/order-item/inventory rows survive this: commit() reverts
        # PendingRequest's own status back to whatever it was before this
        # attempt on any definitive failure, this one included.
        raise HTTPException(403, NOT_AUTHORIZED_DETAIL)
    except TargetOrderNotFound as e:
        raise HTTPException(
            404, f"no sales order {e.order_nb!r} to return against")
    except OrderAlreadyReturned as e:
        raise HTTPException(
            409, f"order {e.order_nb} has already been returned")
    except UnresolvedLines:
        raise HTTPException(422, "every line needs an item and a quantity")
    except RequestNotReviewable:
        raise HTTPException(409, "not reviewable")
    except CommitTransientError:
        # Left in "committing" for the worker's reconciliation sweep to
        # retry (app/worker.py) - the salesman/reviewer can also just try
        # Accept again in a bit.
        raise HTTPException(
            503, "could not reach the order service - this request is "
                "still pending, please try again shortly")
    return {"order_nb": h.order_nb, "order_type": h.order_type}


@router.post("/requests/{req_id}/reject")
def reject(req_id: int, body: RejectIn, s: Session = Depends(get_db),
           salesman: Salesman = Depends(get_current_salesman)):
    try:
        r = _load_for_decision(s, req_id)
    except AlreadyDecided as e:
        raise HTTPException(409, f"already decided ({e.status})"
                            + (f", order {e.order_nb}" if e.order_nb else ""))
    require_customer_ownership(r.cust_nb, salesman)
    operator = salesman.login_id
    r.status = RequestStatus.rejected.value
    r.decision_note = f"{body.reason}: {body.note or ''}"
    r.decided_by = operator
    r.decided_at = datetime.now(timezone.utc)
    log_activity(s, "request_rejected",
                f"request {r.id} rejected by {operator}: {body.reason}",
                request_id=r.id, cust_nb=r.cust_nb,
                details={"operator": operator, "reason": body.reason,
                        "primary_intent": r.primary_intent})
    return {"ok": True}


@router.post("/requests/{req_id}/callback")
def callback(req_id: int, body: CallbackIn, s: Session = Depends(get_db),
             salesman: Salesman = Depends(get_current_salesman)):
    try:
        r = _load_for_decision(s, req_id)
    except AlreadyDecided as e:
        raise HTTPException(409, f"already decided ({e.status})"
                            + (f", order {e.order_nb}" if e.order_nb else ""))
    require_customer_ownership(r.cust_nb, salesman)
    operator = salesman.login_id
    r.status = RequestStatus.callback.value
    r.decision_note = body.note
    r.decided_by = operator
    r.decided_at = datetime.now(timezone.utc)
    return {"ok": True}
