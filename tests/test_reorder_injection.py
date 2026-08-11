from sqlalchemy import select

from app.models import ActivityLog, OrderHeader
from app.services.prior_order import PriorOrderService


def _injection_logs(session):
    return list(session.scalars(select(ActivityLog).where(
        ActivityLog.event_type == "blocked_injection_attempt")))


def test_legit_digit_reference_resolves_normally(db_session):
    target, ambiguity = PriorOrderService(db_session).resolve_target(
        "C001", "please fetch order 990000001")
    assert target is not None
    assert target.order_nb == "990000001"
    assert ambiguity is None


def test_sql_keyword_payload_falls_back_safely_and_is_logged(db_session):
    before = len(_injection_logs(db_session))

    target, ambiguity = PriorOrderService(db_session).resolve_target(
        "C001", "1; DROP TABLE order_header;--")

    # No crash, no order resolved from the payload, no table dropped.
    assert target is None
    assert ambiguity == "multiple_open_orders"  # C001 has 3 open test orders
    assert db_session.scalar(select(OrderHeader).limit(1)) is not None
    assert len(_injection_logs(db_session)) == before + 1


def test_quote_based_payload_does_not_crash_or_mutate(db_session):
    # Classic tautology injection - contains no keyword from the heuristic,
    # so this exercises the underlying safety (digit-only extraction +
    # parameterised lookup), not the detector.
    target, ambiguity = PriorOrderService(db_session).resolve_target(
        "C001", "' OR '1'='1")
    assert target is None
    assert ambiguity == "multiple_open_orders"


def test_update_order_reference_is_not_flagged_as_injection(db_session):
    # "update" is ordinary language for the update_order intent this
    # service is reached from - must not be treated as SQL-injection-shaped.
    before = len(_injection_logs(db_session))
    target, ambiguity = PriorOrderService(db_session).resolve_target(
        "C001", "update order 990000001")
    assert target is not None
    assert target.order_nb == "990000001"
    assert len(_injection_logs(db_session)) == before


def test_long_payload_is_capped(db_session):
    payload = "9" * 500 + "; DROP TABLE order_header;--"
    target, ambiguity = PriorOrderService(db_session).resolve_target(
        "C001", payload)
    assert target is None
    assert ambiguity in ("multiple_open_orders", "no_open_orders")
