from app.models import Customer, OrderHeader
from app.services.prior_order import PriorOrderService

# ---- PriorOrderService.resolve_target edge cases -------------------------


def test_no_open_orders_for_a_customer_with_none(db_session):
    db_session.add(Customer(customer_number="ZRC002", customer_name="Empty Co"))
    db_session.flush()
    target, ambiguity = PriorOrderService(db_session).resolve_target(
        "ZRC002", None)
    assert target is None
    assert ambiguity == "no_open_orders"


def test_multiple_open_orders_is_ambiguous_not_a_guess(db_session):
    # C001 has three open orders (990000001-3) in the seeded fixtures.
    target, ambiguity = PriorOrderService(db_session).resolve_target(
        "C001", None)
    assert target is None
    assert ambiguity == "multiple_open_orders"


def test_reference_to_another_customers_order_is_ignored_not_leaked(db_session):
    # 990000004 belongs to C002 - referencing it while acting as C001 must
    # never resolve to C002's order; it should fall back to normal
    # same-customer resolution instead.
    target, ambiguity = PriorOrderService(db_session).resolve_target(
        "C001", "990000004")
    assert target is None or target.cust_nb == "C001"
    assert not (target is not None and target.order_nb == "990000004")


def test_resolve_target_for_nonexistent_customer_returns_no_open_orders(db_session):
    target, ambiguity = PriorOrderService(db_session).resolve_target(
        "ZRC_DOES_NOT_EXIST", "12345")
    assert target is None
    assert ambiguity == "no_open_orders"


def test_lines_of_never_returns_another_orders_lines(db_session):
    header = db_session.get(OrderHeader, ("990000001", "SO"))
    lines = PriorOrderService(db_session).lines_of(header)
    assert {l.item_nb for l in lines} == {"A100", "B200"}
    assert all(l.order_nb == "990000001" for l in lines)
