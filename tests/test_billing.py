from decimal import Decimal

import pytest

from app.errors import EmptyOrder, OrderCustomerMismatch, OrderNotFound
from app.services.billing import BillService, render_html


def test_build_computes_grand_total(db_session):
    bill = BillService(db_session).build("C001", "990000001", "SO")
    assert bill.grand_total == Decimal("65.00")  # 3*19.50 + 2*3.25
    assert bill.has_missing_prices is False


def test_order_not_found(db_session):
    with pytest.raises(OrderNotFound):
        BillService(db_session).build("C001", "no-such-order", "SO")


def test_order_belongs_to_different_customer(db_session):
    # 990000004 belongs to C002 - C001 must not be able to pull its bill.
    with pytest.raises(OrderCustomerMismatch):
        BillService(db_session).build("C001", "990000004", "SO")


def test_empty_order_rejected(db_session):
    with pytest.raises(EmptyOrder):
        BillService(db_session).build("C001", "990000003", "SO")


def test_missing_item_price_excluded_from_total_but_flagged(db_session):
    bill = BillService(db_session).build("C001", "990000002", "SO")
    assert bill.has_missing_prices is True
    assert bill.grand_total == Decimal("0")
    assert bill.lines[0].line_total is None


def test_render_html_includes_customer_and_total():
    from app.models import Customer
    from app.services.billing import Bill, BillLine

    b = Bill(customer=Customer(customer_number="C001",
                               customer_name="Test Trading"),
            order_nb="990000001", order_type="SO",
            lines=[BillLine(1, "A100", "Blue Paint 5L", Decimal("3"),
                            "PCS", Decimal("19.50"))])
    html = render_html(b)
    assert "Test Trading" in html
    assert "990000001" in html
    assert "58.50" in html


def test_render_html_flags_missing_price_line():
    from app.models import Customer
    from app.services.billing import Bill, BillLine

    b = Bill(customer=Customer(customer_number="C003",
                               customer_name="Zahle Paint Supply"),
            order_nb="990000002", order_type="SO",
            lines=[BillLine(1, "I999", "Discontinued Sample Item",
                            Decimal("1"), "PCS", None)])
    html = render_html(b)
    assert "n/a" in html
    assert "no price on file" in html
