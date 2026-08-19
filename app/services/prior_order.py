import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.models import OrderDetail, OrderHeader
from app.services.activity_log import log_standalone

_BUSINESS_TZ = ZoneInfo(settings.business_timezone)

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


def _order_header_for_date(session, cust_nb: str, on_date: date
                           ) -> OrderHeader | None:
    """The OrderHeader `cust_nb` placed on `on_date`, or None if zero or
    more than one exists - the shared query behind get_order_nb_from_date
    (below) and PriorOrderService.resolve_target_explicit's "date" mode.
    Split out so the latter can use the full row it needs directly instead
    of a second round trip to re-fetch by the order_nb the former already
    had in hand.
    """
    # Compare against the [start, end) UTC instant range of `on_date` as a
    # calendar day in the business's own timezone, rather than
    # func.date(created_at) == on_date - the latter truncates using the
    # database session's timezone setting (typically UTC, not otherwise
    # controlled by this app), which can disagree with the business-local
    # day the customer actually means, especially near midnight.
    day_start = datetime.combine(on_date, time.min, tzinfo=_BUSINESS_TZ)
    day_end = day_start + timedelta(days=1)
    rows = list(session.scalars(
        select(OrderHeader)
        .where(OrderHeader.cust_nb == cust_nb,
              OrderHeader.created_at >= day_start,
              OrderHeader.created_at < day_end)
        .order_by(OrderHeader.order_nb)).all())
    return rows[0] if len(rows) == 1 else None


def get_order_nb_from_date(session, cust_nb: str | None,
                           on_date: date | None) -> str | None:
    """The order number `cust_nb` placed on `on_date`, straight from the
    database - never invented, guessed, cached, or fabricated.

    Returns None (never raises, never guesses) when: cust_nb/on_date is
    missing, no order exists for that customer on that date, or more than
    one does. An ambiguous match is not resolved by picking one - that
    would be exactly the kind of invented answer this function must not
    give. Filtering by `OrderHeader.cust_nb == cust_nb` also guarantees an
    order returned for one customer can never belong to another.
    """
    if not cust_nb or not on_date:
        return None
    header = _order_header_for_date(session, cust_nb, on_date)
    return header.order_nb if header else None


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

    def find_by_order_nb(self, order_nb: str):
        """The OrderHeader for `order_nb`, regardless of customer - used by
        the return_order scripted command, which names an order number
        directly rather than a customer. None if it doesn't exist or (rare:
        the same order_nb reused across order_types) isn't unique -
        never guessed."""
        rows = list(self.s.scalars(
            select(OrderHeader).where(OrderHeader.order_nb == order_nb)))
        return rows[0] if len(rows) == 1 else None

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

    def resolve_target_explicit(self, cust_nb: str, mode: str,
                                value: str | None):
        """Resolve a reorder target from an explicitly-stated mode (spec:
        "3 enums" - last|date|order_nb), never inferred from what's absent
        the way resolve_target() above does for the free-form Gemini
        repeat_order path. Same (header, ambiguity_reason) contract:
        header is None (never guessed) whenever the target can't be
        resolved with certainty.
        """
        if mode == "last":
            header = self.last_order(cust_nb)
            return (header, None) if header else (None, "no_orders")

        if mode == "order_nb":
            if not value:
                return None, "no_order_reference"
            ref_text = value[:MAX_REFERENCE_LEN]
            if _SUSPICIOUS.search(ref_text):
                log_standalone(
                    "blocked_injection_attempt",
                    f"reorder order_nb for customer {cust_nb} looked "
                    "SQL-injection-shaped; refused", level="warn",
                    cust_nb=cust_nb, details={"reference": ref_text})
                return None, "invalid_reference"
            ref = "".join(ch for ch in ref_text if ch.isdigit())
            if not ref:
                return None, "invalid_reference"
            rows = list(self.s.scalars(select(OrderHeader).where(
                OrderHeader.cust_nb == cust_nb, OrderHeader.order_nb == ref)))
            if len(rows) != 1:
                return None, ("order_not_found" if not rows else
                             "multiple_order_types")
            return rows[0], None

        if mode == "date":
            if not value:
                return None, "no_date_reference"
            try:
                from dateutil import parser as date_parser
                on_date = date_parser.parse(value, fuzzy=True).date()
            except (ValueError, OverflowError):
                return None, "unparseable_date"
            # _order_header_for_date directly, not get_order_nb_from_date +
            # a second query by order_nb - it already fetched the full row.
            header = _order_header_for_date(self.s, cust_nb, on_date)
            return (header, None) if header else (None, "no_order_on_date")

        return None, "unknown_mode"
