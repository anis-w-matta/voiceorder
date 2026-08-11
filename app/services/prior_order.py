import re

from sqlalchemy import select

from app.models import OrderDetail, OrderHeader
from app.services.activity_log import log_standalone

# Reorders are read-only by construction: every query below is either an
# ORM `select().where(Model.col == value)` or (in item_resolver.py) a
# `text()` query with named bound parameters - there is no string-built SQL
# anywhere on this path for a payload to inject into. This pattern is a
# best-effort *detector*, not the actual defence: it exists so an attempted
# injection still gets refused and logged (spec: "abort immediately, fall
# back safely, and return an error") instead of just silently failing to
# match anything, which would look identical to an ordinary typo.
#
# Deliberately excludes UPDATE/ALTER even though they're classic SQL
# keywords: "update"/"alter" are ordinary words a customer might actually
# say about their own order ("update order 12345"), and this service is
# reached from the update_order intent - flagging those would misfire on
# legitimate requests far more often than it would catch anything real.
# DROP/DELETE/INSERT/TRUNCATE/UNION/EXEC have no such legitimate use here.
_SUSPICIOUS = re.compile(
    r";|--|/\*|\bDROP\b|\bDELETE\b|\bINSERT\b|\bUNION\b|"
    r"\bTRUNCATE\b|\bEXEC(UTE)?\b",
    re.IGNORECASE)
MAX_REFERENCE_LEN = 200


class PriorOrderService:
    def __init__(self, session):
        self.s = session

    def last_order(self, cust_nb: str):
        return self.s.scalars(
            select(OrderHeader).where(OrderHeader.cust_nb == cust_nb)
            .order_by(OrderHeader.created_at.desc()).limit(1)).first()

    def open_orders(self, cust_nb: str):
        return list(self.s.scalars(
            select(OrderHeader)
            .where(OrderHeader.cust_nb == cust_nb,
                   OrderHeader.status == "open")
            .order_by(OrderHeader.created_at.desc())).all())

    def lines_of(self, header):
        return list(self.s.scalars(
            select(OrderDetail)
            .where(OrderDetail.order_nb == header.order_nb,
                   OrderDetail.order_type == header.order_type)
            .order_by(OrderDetail.line_nb)).all())

    def resolve_target(self, cust_nb: str, reference: str | None):
        if reference:
            ref_text = reference[:MAX_REFERENCE_LEN]
            if _SUSPICIOUS.search(ref_text):
                # Abort using this reference entirely rather than salvaging
                # digits out of it - a payload that trips the heuristic is
                # untrusted in full, not just in the parts that don't look
                # like digits.
                log_standalone(
                    "blocked_injection_attempt",
                    f"reorder reference for customer {cust_nb} looked "
                    "SQL-injection-shaped; ignored, falling back to normal "
                    "open-order resolution",
                    level="warn", cust_nb=cust_nb,
                    details={"reference": ref_text})
            else:
                ref = "".join(ch for ch in ref_text if ch.isdigit())
                if ref:
                    h = self.s.scalars(select(OrderHeader).where(
                        OrderHeader.cust_nb == cust_nb,
                        OrderHeader.order_nb == ref)).first()
                    if h:
                        return h, None
        opens = self.open_orders(cust_nb)
        if len(opens) == 1:
            return opens[0], None
        if not opens:
            return None, "no_open_orders"
        return None, "multiple_open_orders"
