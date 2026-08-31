import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.errors import RequestNotFound, RequestNotReviewable
from app.models import PendingLine, PendingRequest
from app.schemas.enums import RequestStatus
from app.services import catalog_client
from app.services.activity_log import log as log_activity
from app.services.activity_log import log_standalone
from app.services.catalog_client import CommitTransientError

# JSON-safe (de)serialization for catalog_client.LineIn/LineEditIn, so the
# commit saga's replay payload (PendingRequest.raw_model_output.
# commit_request, see commit() below) can round-trip through JSONB -
# shared with app/worker.py's reconcile_stuck_commits, which rebuilds the
# same dataclasses from this same shape to resend an interrupted commit.

def _line_dict(l: "catalog_client.LineIn") -> dict:
    return {"line_nb": l.line_nb, "item_nb": l.item_nb,
           "item_desc": l.item_desc,
           "qty": str(l.qty) if l.qty is not None else None, "uom": l.uom}


def line_from_dict(d: dict) -> "catalog_client.LineIn":
    from decimal import Decimal
    return catalog_client.LineIn(
        line_nb=d["line_nb"], item_nb=d["item_nb"], item_desc=d["item_desc"],
        qty=Decimal(d["qty"]) if d["qty"] is not None else None, uom=d["uom"])


def _line_edit_dict(e: "catalog_client.LineEditIn") -> dict:
    return {"line_nb": e.line_nb, "item_nb": e.item_nb,
           "item_desc": e.item_desc,
           "qty": str(e.qty) if e.qty is not None else None, "uom": e.uom}


def line_edit_from_dict(d: dict) -> "catalog_client.LineEditIn":
    from decimal import Decimal
    return catalog_client.LineEditIn(
        line_nb=d["line_nb"], item_nb=d["item_nb"], item_desc=d["item_desc"],
        qty=Decimal(d["qty"]) if d["qty"] is not None else None, uom=d["uom"])


class OrderCommitService:
    def __init__(self, session, numbering=None):
        self.s = session

    def commit(self, request_id: int, order_type: str, line_edits,
               operator: str, removed_line_nbs=None,
               cust_nb_override: str | None = None,
               target_order_nb_override: str | None = None,
               acting_is_admin: bool = False):
        """Commits a request as an order - the backend side of the commit
        saga (see catalog-service's app/services/orders.py for the other
        half). What stays here: PendingRequest status checks, generating
        and durably persisting the commit_intent_id *before* calling
        catalog-service (so a crash mid-call leaves a recoverable trace -
        see app/worker.py's reconcile_stuck_commits), and the final "mark
        committed" write. Everything else - RETURN/reorder target
        resolution, customer/already-returned validation, line editing,
        QRA application, order number allocation, OrderHeader/OrderDetail
        creation - now happens inside catalog-service's POST /orders,
        atomically, since that's where the data it touches lives.
        """
        req = self.s.execute(
            select(PendingRequest).where(PendingRequest.id == request_id)
            .with_for_update()).scalar_one_or_none()
        if req is None:
            # Covers both "never existed" and "already committed": a
            # committed request's row is deleted once its order exists
            # (see _finalize_committed below), so a retried/duplicate
            # accept() on it lands here rather than an AlreadyCommitted
            # branch - there's no row left to report a status on.
            raise RequestNotFound(request_id)

        if req.status in (RequestStatus.rejected.value,
                         RequestStatus.committing.value):
            log_standalone("update_rejected",
                          f"update attempt on request {req.id} refused: "
                          f"status is {req.status!r}, not in buffer state",
                          level="warn", request_id=req.id, cust_nb=req.cust_nb)
            raise RequestNotReviewable()

        # Snapshot req.lines BEFORE applying operator edits locally -
        # catalog-service needs the *original* lines/line_edits/
        # removed_line_nbs, in that shape, to reproduce the same ordering
        # the old single-process commit() had: RETURN/reorder target
        # resolution (and any full-return/baseline-merge backfill) must
        # see whether line_edits was genuinely empty, which collapsing
        # everything into one already-edited line list here would hide.
        # _apply_edits still runs locally too, so PendingLine itself keeps
        # reflecting the operator's edits (match_method="manual" etc.)
        # for the dashboard/audit trail - catalog-service repeats the same
        # deterministic edit application on its own copy.
        lines_snapshot = [catalog_client.LineIn(
            line_nb=l.line_nb, item_nb=l.item_nb, item_desc=l.item_desc,
            qty=l.qty, uom=l.uom) for l in req.lines]
        line_edits_snapshot = [catalog_client.LineEditIn(
            line_nb=e.line_nb, item_nb=e.item_nb, item_desc=e.item_desc,
            qty=e.qty, uom=e.uom) for e in line_edits]
        removed_snapshot = list(removed_line_nbs or [])

        self._apply_edits(req, line_edits, removed_line_nbs or [])

        full_return = bool(req.raw_model_output and
                          req.raw_model_output.get("full_return"))

        # Durable, own transaction: if the process crashes during the
        # catalog-service call below, commit_intent_id plus this replay
        # payload (order_type/overrides/the line snapshots - everything
        # create_order() needs that ISN'T already a stable column on
        # PendingRequest) is what lets the reconciliation sweep
        # (app/worker.py's reconcile_stuck_commits) resend the exact same
        # call rather than losing track of what was being committed.
        # original_status is what a definitive validation failure below
        # reverts to - the original single-process commit() never
        # actually changed status on a failed validation (the whole
        # transaction rolled back), so this preserves that.
        original_status = req.status
        commit_intent_id = str(uuid.uuid4())
        req.status = RequestStatus.committing.value
        req.commit_intent_id = commit_intent_id
        req.raw_model_output = {
            **(req.raw_model_output or {}),
            "commit_request": {
                "order_type": order_type,
                "cust_nb_override": cust_nb_override,
                "target_order_nb_override": target_order_nb_override,
                "full_return": full_return,
                "lines": [_line_dict(l) for l in lines_snapshot],
                "line_edits": [_line_edit_dict(e) for e in line_edits_snapshot],
                "removed_line_nbs": removed_snapshot,
            },
        }
        # Doubles as "when did committing start", so the worker's
        # reconciliation sweep can tell a request that's still within its
        # own synchronous accept() call apart from one that crashed
        # mid-call - overwritten below with the real decision time on
        # success (or cleared on a definitive failure).
        req.decided_at = datetime.now(timezone.utc)
        self.s.commit()
        try:
            result = catalog_client.create_order(
                commit_intent_id=commit_intent_id, order_type=order_type,
                cust_nb=req.cust_nb, cust_nb_override=cust_nb_override,
                target_order_nb_override=target_order_nb_override,
                primary_intent=req.primary_intent, full_return=full_return,
                lines=lines_snapshot, line_edits=line_edits_snapshot,
                removed_line_nbs=removed_snapshot,
                is_return=(order_type == "RETURN"),
                acting_salesman_id=operator, acting_is_admin=acting_is_admin)
        except CommitTransientError:
            # Left in "committing" - the reconciliation sweep will retry
            # with the same commit_intent_id (idempotent on catalog-
            # service's side) rather than this request silently vanishing
            # from the queue forever.
            raise
        except Exception:
            # A definitive validation failure (CustomerNotFound etc.) -
            # not transient, retrying with the same input won't help.
            # Revert to whatever this request's status was before this
            # attempt, so it's reviewable again rather than stuck.
            req.status = original_status
            req.commit_intent_id = None
            req.decided_at = None
            self.s.commit()
            raise

        # Re-fetch: the commit() above ended the transaction that held
        # the row lock, so this is a fresh one.
        req = self.s.execute(
            select(PendingRequest).where(PendingRequest.id == request_id)
            .with_for_update()).scalar_one()
        _finalize_committed(self.s, req, result, operator, order_type)
        return result

    def _apply_edits(self, req, line_edits, removed_line_nbs):
        """Applies the operator's line edits to req.lines (PendingLine
        rows) before they're sent to catalog-service - unchanged from
        before the split, since this only ever touched PendingLine, which
        stayed in the backend. item_nb is no longer re-validated against
        the catalogue here (it never was, functionally - see the split
        plan); catalog-service's UnresolvedLines check still requires a
        non-null item_nb/qty/valid uom on every line.
        """
        by_nb = {l.line_nb: l for l in req.lines}
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
            if e.item_desc is not None:
                line.item_desc = e.item_desc
            if e.qty is not None and e.qty != line.qty:
                line.qty = e.qty
                line.operator_edited = True
            if e.uom is not None:
                line.uom = e.uom


def _finalize_committed(session, req, result, operator, order_type):
    """Shared tail of a successful commit - used by both
    OrderCommitService.commit()'s normal path and
    reconcile_stuck_commit()'s crash-recovery path below, since both end
    the same way once catalog-service has confirmed the order exists.

    The PendingRequest - and its PendingLine rows, via
    pending_request_line's ondelete=CASCADE - is deleted here rather than
    marked "committed" and kept around: once an order is durably real in
    catalog-service's order_header/order_details, that's the single source
    of truth for it, not a lingering buffer row. activity_log keeps its
    own independent snapshot (no FK to pending_request, so nothing here
    touches the audit trail), and GET /orders/recent now reads committed
    orders straight from catalog-service instead of this table - see
    app/api/orders.py.
    """
    req_id = req.id
    cust_nb = result.cust_nb or req.cust_nb
    log_activity(session, "order_committed",
                f"request {req_id} committed as order {result.order_nb}",
                request_id=req_id, cust_nb=cust_nb,
                order_nb=result.order_nb,
                details={"operator": operator, "order_type": order_type,
                         "reconciled": operator == "worker-reconciliation"})
    session.delete(req)
    session.flush()


def reconcile_stuck_commit(session, req_id: int) -> bool:
    """Re-drives a PendingRequest stuck in "committing" - the process
    that generated its commit_intent_id crashed (or is taking unusually
    long) between persisting that durably and recording the result.
    Resends the exact same POST /orders call catalog-service already saw
    (or is seeing for the first time, if the crash happened before that
    call ever went out) - idempotent on commit_intent_id there either
    way, so this never creates a duplicate order.

    Caller (app/worker.py) is expected to have already locked `req_id`
    with FOR UPDATE SKIP LOCKED. Returns True if the request is now
    resolved (committed, or reverted to reviewable on a definitive
    failure), False if it's still transiently stuck (left for the next
    sweep).
    """
    req = session.get(PendingRequest, req_id)
    if req is None or req.status != RequestStatus.committing.value:
        return True  # already resolved by a concurrent sweep/retry

    payload = (req.raw_model_output or {}).get("commit_request")
    if not payload:
        # No replay payload (shouldn't happen - written in the same
        # transaction as commit_intent_id) - nothing to safely retry.
        return False

    try:
        result = catalog_client.create_order(
            commit_intent_id=req.commit_intent_id,
            order_type=payload["order_type"], cust_nb=req.cust_nb,
            cust_nb_override=payload["cust_nb_override"],
            target_order_nb_override=payload["target_order_nb_override"],
            primary_intent=req.primary_intent,
            full_return=payload["full_return"],
            lines=[line_from_dict(d) for d in payload["lines"]],
            line_edits=[line_edit_from_dict(d) for d in payload["line_edits"]],
            removed_line_nbs=payload["removed_line_nbs"],
            is_return=(payload["order_type"] == "RETURN"),
            # This resends the exact commit_intent_id a real accept() call
            # already got past the ownership check with - catalog-service's
            # commit_intent_id idempotency short-circuit (see create_order())
            # returns the already-created order without re-checking anything
            # if it succeeded the first time; acting_is_admin only matters
            # for the rarer case where the original call never actually
            # reached catalog-service before the crash, and this is
            # completing an attempt that was already authorized once, not a
            # fresh actor bypassing the check.
            acting_salesman_id="worker-reconciliation", acting_is_admin=True)
    except CommitTransientError:
        return False
    except Exception:
        # Definitive failure on retry too (e.g. the customer number really
        # doesn't exist) - revert to a reviewable state rather than
        # leaving it stuck forever. original per-request status isn't
        # recoverable across a crash, so this falls back to "new" rather
        # than whatever in_review/callback state it may have been in.
        log_standalone(
            "commit_reconciliation_failed",
            f"request {req.id} could not be reconciled after being stuck "
            "in committing - reverted to new for manual re-review",
            level="error", request_id=req.id, cust_nb=req.cust_nb)
        req.status = RequestStatus.new.value
        req.commit_intent_id = None
        req.decided_at = None
        session.commit()
        return True

    _finalize_committed(session, req, result, "worker-reconciliation",
                        payload["order_type"])
    session.commit()
    return True
