import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.models import Customer, Item, OrderDetail, OrderHeader
from app.services.numbering import OrderNumberService


def ensure_test_orders(session, minimum: int = 50) -> list[str]:
    """Spec: if the orders table is empty, add `minimum` valid test orders;
    if orders already exist, do nothing (never create duplicate/invalid
    records on top of real data).

    Every order is built strictly from customers and items that already
    exist as real rows in the database - this never fabricates a Customer
    or Item record, only OrderHeader/OrderDetail rows referencing real
    ones. Raises if there is nothing valid to attach an order to, rather
    than inventing a customer or item to make the numbers up.

    Returns the order numbers created (empty list when it was a no-op).
    """
    existing = session.execute(
        select(func.count()).select_from(OrderHeader)).scalar()
    if existing:
        return []

    customers = list(session.scalars(select(Customer)).all())
    items = list(session.scalars(select(Item)).all())
    if not customers or not items:
        raise ValueError(
            "cannot seed test orders: no customers/items exist in the "
            "database to attach them to - seed customers and items first "
            "rather than fabricating them here")

    numbering = OrderNumberService(session)
    now = datetime.now(timezone.utc)
    created: list[str] = []
    for i in range(minimum):
        cust = customers[i % len(customers)]
        order_nb = numbering.next()
        placed_at = now - timedelta(days=random.randint(0, 180),
                                    hours=random.randint(0, 23))
        session.add(OrderHeader(
            order_nb=order_nb, order_type="SO", cust_nb=cust.customer_number,
            status="open", source="test_seed", created_at=placed_at))

        line_count = min(random.randint(1, 3), len(items))
        for line_nb, item in enumerate(random.sample(items, k=line_count),
                                       start=1):
            session.add(OrderDetail(
                order_nb=order_nb, order_type="SO", line_nb=line_nb,
                item_nb=item.item_number, item_desc=item.item_desc,
                qty=Decimal(random.randint(1, 10)), uom="PCS",
                unit_price=item.unit_price, category=item.category))
        created.append(order_nb)

    session.flush()
    return created
