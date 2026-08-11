from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_mailer
from app.config import settings
from app.errors import (EmptyOrder, OrderCustomerMismatch, OrderNotFound,
                        SmtpNotConfigured)
from app.schemas.api_in import BillRequestIn
from app.services.activity_log import log as log_activity
from app.services.billing import BillService, render_html
from app.services.mailer import Mailer

router = APIRouter(tags=["bills"])


@router.post("/bills/request")
def request_bill(body: BillRequestIn, s: Session = Depends(get_db),
                 mailer: Mailer = Depends(get_mailer)):
    svc = BillService(s)
    try:
        bill = svc.build(body.cust_nb, body.order_nb, body.order_type)
    except OrderNotFound:
        raise HTTPException(404, "no such order")
    except OrderCustomerMismatch:
        raise HTTPException(403, "order does not belong to this customer")
    except EmptyOrder:
        raise HTTPException(422, "order has no lines to bill")

    html = render_html(bill)
    subject = f"Bill for order {bill.order_nb} - {bill.customer.customer_name}"
    try:
        mailer.send_html(settings.bill_recipient_email, subject, html)
    except SmtpNotConfigured:
        log_activity(s, "error",
                    f"bill for {body.cust_nb}/{body.order_nb} generated but "
                    "not sent: SMTP not configured", level="error",
                    cust_nb=body.cust_nb, order_nb=body.order_nb)
        return {"ok": False, "delivered": False,
               "reason": "SMTP is not configured (set SMTP_HOST/SMTP_USER/"
                         "SMTP_PASSWORD in .env) - bill was generated but "
                         "not sent",
               "total": str(bill.grand_total),
               "has_missing_prices": bill.has_missing_prices}

    log_activity(s, "email_sent",
                f"bill for {body.cust_nb}/{body.order_nb} emailed to "
                f"{settings.bill_recipient_email}", cust_nb=body.cust_nb,
                order_nb=body.order_nb,
                details={"to": settings.bill_recipient_email})
    return {"ok": True, "delivered": True,
           "sent_to": settings.bill_recipient_email,
           "total": str(bill.grand_total),
           "has_missing_prices": bill.has_missing_prices}
