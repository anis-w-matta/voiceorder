import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.errors import OrderCustomerMismatch, OrderNotFound, SmtpNotConfigured
from app.models import BillEmailLog
from app.services.activity_log import log as log_activity
from app.services.billing import find_matching_order
from app.services.mailer import Mailer

_DIGITS = re.compile(r"\d+")


def order_nb_from_reference(reference: str | None) -> str | None:
    """Best-effort order number out of a free-text reference, same approach
    as PriorOrderService.resolve_target: pull the digit run out, ignore
    everything else. Returns None rather than guessing on ambiguous input
    (more than one digit run, or none at all)."""
    if not reference:
        return None
    runs = _DIGITS.findall(reference)
    return runs[0] if len(runs) == 1 else None


def maybe_send_bill_notification(session, mailer: Mailer, *, cust_nb: str,
                                 order_reference: str | None,
                                 voice_message_id: int,
                                 request_id: int) -> None:
    """Spec: on a get_bill request, send a fixed-text notification email
    only when cust_nb + the referenced order_nb match the same order record,
    and only once per (cust_nb, order_nb) pair. Anything else - no
    reference, ambiguous reference, no such order, wrong customer, or a
    repeat of an already-sent pair - sends nothing and leaves the request in
    the review queue exactly as it already would without this feature."""
    order_nb = order_nb_from_reference(order_reference)
    if not order_nb:
        log_activity(session, "bill_validation",
                    "get_bill request has no unambiguous order number to "
                    "validate; left for manual review",
                    level="info", voice_message_id=voice_message_id,
                    request_id=request_id, cust_nb=cust_nb)
        return

    order_type = "SO"
    try:
        find_matching_order(session, cust_nb, order_nb, order_type)
    except (OrderNotFound, OrderCustomerMismatch) as e:
        log_activity(session, "bill_validation",
                    f"get_bill request for {cust_nb}/{order_nb} failed "
                    f"validation ({type(e).__name__}); left for manual "
                    "review", level="info", voice_message_id=voice_message_id,
                    request_id=request_id, cust_nb=cust_nb, order_nb=order_nb)
        return

    already = session.scalars(select(BillEmailLog).where(
        BillEmailLog.cust_nb == cust_nb, BillEmailLog.order_nb == order_nb,
        BillEmailLog.order_type == order_type)).first()
    if already:
        log_activity(session, "bill_validation",
                    f"get_bill request for {cust_nb}/{order_nb} already "
                    "notified once; not sending again", level="info",
                    voice_message_id=voice_message_id, request_id=request_id,
                    cust_nb=cust_nb, order_nb=order_nb)
        return

    body = f"I am requesting bill for {cust_nb}, {order_nb}"
    try:
        mailer.send_text(settings.bill_request_notify_email,
                         f"Bill request: {cust_nb}, {order_nb}", body)
    except SmtpNotConfigured:
        log_activity(session, "error",
                    f"could not send bill-request notification for "
                    f"{cust_nb}/{order_nb}: SMTP not configured",
                    level="error", voice_message_id=voice_message_id,
                    request_id=request_id, cust_nb=cust_nb, order_nb=order_nb)
        return
    except Exception as e:
        log_activity(session, "error",
                    f"could not send bill-request notification for "
                    f"{cust_nb}/{order_nb}: {e}", level="error",
                    voice_message_id=voice_message_id, request_id=request_id,
                    cust_nb=cust_nb, order_nb=order_nb)
        return

    try:
        # A savepoint, not the outer transaction: this call runs inside
        # pipeline.process()'s single session_scope() alongside the
        # transcript/classification/PendingRequest writes for this voice
        # message. A plain session.rollback() on IntegrityError would wipe
        # all of that out too - begin_nested() confines the rollback to
        # just this insert if the unique constraint trips.
        with session.begin_nested():
            session.add(BillEmailLog(cust_nb=cust_nb, order_nb=order_nb,
                                     order_type=order_type))
            session.flush()
    except IntegrityError:
        # Lost a race against another request for the same pair: the email
        # already went out from the other path, nothing more to do here.
        return

    log_activity(session, "email_sent",
                f"bill-request notification sent to "
                f"{settings.bill_request_notify_email} for {cust_nb}/"
                f"{order_nb}", voice_message_id=voice_message_id,
                request_id=request_id, cust_nb=cust_nb, order_nb=order_nb,
                details={"to": settings.bill_request_notify_email,
                        "body": body})
