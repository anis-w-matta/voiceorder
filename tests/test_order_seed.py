import pytest
from sqlalchemy import delete, func, select

from app.models import Customer, Item, OrderDetail, OrderHeader
from app.services.order_seed import ensure_test_orders


def test_noop_when_orders_already_exist(db_session):
    before = db_session.execute(
        select(func.count()).select_from(OrderHeader)).scalar()
    assert before > 0  # seed_test.py fixtures are already present

    created = ensure_test_orders(db_session, minimum=50)

    assert created == []
    after = db_session.execute(
        select(func.count()).select_from(OrderHeader)).scalar()
    assert after == before  # no duplicate/extra records added


def test_seeds_minimum_orders_when_table_is_empty(db_session):
    # Clear orders inside this test's own (rolled-back-on-teardown)
    # transaction only - the real test-schema fixtures are untouched
    # outside this test.
    db_session.execute(delete(OrderDetail))
    db_session.execute(delete(OrderHeader))
    db_session.flush()

    real_customers = {c.customer_number
                      for c in db_session.scalars(select(Customer)).all()}
    real_items = {i.item_number for i in db_session.scalars(select(Item)).all()}

    created = ensure_test_orders(db_session, minimum=50)

    assert len(created) == 50
    assert len(set(created)) == 50  # no duplicate order numbers

    # Retrieved back through the app's normal ORM access layer.
    count = db_session.execute(
        select(func.count()).select_from(OrderHeader)).scalar()
    assert count == 50

    for order_nb in created:
        header = db_session.get(OrderHeader, (order_nb, "SO"))
        assert header is not None
        # Every order references a real, pre-existing customer - never a
        # fabricated one.
        assert header.cust_nb in real_customers

        lines = list(db_session.scalars(select(OrderDetail).where(
            OrderDetail.order_nb == order_nb,
            OrderDetail.order_type == "SO")))
        assert len(lines) >= 1
        for line in lines:
            # Every line references a real, pre-existing item.
            assert line.item_nb in real_items


def test_raises_rather_than_fabricate_a_customer(db_session):
    db_session.execute(delete(OrderDetail))
    db_session.execute(delete(OrderHeader))
    db_session.execute(delete(Customer))
    db_session.flush()

    with pytest.raises(ValueError):
        ensure_test_orders(db_session, minimum=50)

    # Confirms the ValueError path really did nothing rather than partially
    # seeding orders against a fabricated customer.
    count = db_session.execute(
        select(func.count()).select_from(OrderHeader)).scalar()
    assert count == 0
