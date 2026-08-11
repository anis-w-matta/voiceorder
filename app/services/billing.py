from dataclasses import dataclass, field
from decimal import Decimal
from html import escape

from sqlalchemy import select

from app.errors import EmptyOrder, OrderCustomerMismatch, OrderNotFound
from app.models import Customer, OrderDetail, OrderHeader


@dataclass
class BillLine:
    line_nb: int
    item_nb: str
    item_desc: str
    qty: Decimal
    uom: str
    unit_price: Decimal | None

    @property
    def line_total(self) -> Decimal | None:
        if self.unit_price is None:
            return None
        return (self.qty * self.unit_price).quantize(Decimal("0.01"))


@dataclass
class Bill:
    customer: Customer
    order_nb: str
    order_type: str
    lines: list[BillLine] = field(default_factory=list)

    @property
    def grand_total(self) -> Decimal:
        return sum((l.line_total or Decimal("0")) for l in self.lines)

    @property
    def has_missing_prices(self) -> bool:
        return any(l.unit_price is None for l in self.lines)


def find_matching_order(session, cust_nb: str, order_nb: str,
                        order_type: str = "SO") -> OrderHeader:
    """The shared cust_nb/order_nb match check: an order exists and belongs
    to the customer asking about it. Used both by the manually-triggered
    POST /bills/request (BillService.build below) and by the automatic
    get_bill notification (app.services.bill_request)."""
    header = session.get(OrderHeader, (order_nb, order_type))
    if header is None:
        raise OrderNotFound(order_nb, order_type)
    if header.cust_nb != cust_nb:
        raise OrderCustomerMismatch(order_nb, cust_nb)
    return header


class BillService:
    def __init__(self, session):
        self.s = session

    def build(self, cust_nb: str, order_nb: str,
              order_type: str = "SO") -> Bill:
        customer = self.s.get(Customer, cust_nb)
        header = find_matching_order(self.s, cust_nb, order_nb, order_type)

        details = list(self.s.scalars(
            select(OrderDetail)
            .where(OrderDetail.order_nb == order_nb,
                   OrderDetail.order_type == order_type)
            .order_by(OrderDetail.line_nb)))
        if not details:
            raise EmptyOrder(order_nb)

        lines = [BillLine(line_nb=d.line_nb, item_nb=d.item_nb,
                          item_desc=d.item_desc, qty=d.qty, uom=d.uom,
                          unit_price=d.unit_price) for d in details]
        return Bill(customer=customer, order_nb=order_nb,
                    order_type=order_type, lines=lines)


def render_html(bill: Bill) -> str:
    cust_name = escape(bill.customer.customer_name) if bill.customer else \
        escape(bill.order_nb)
    rows = []
    for l in bill.lines:
        price = f"{l.unit_price:.2f}" if l.unit_price is not None else \
            "<em>n/a</em>"
        total = f"{l.line_total:.2f}" if l.line_total is not None else \
            "<em>n/a</em>"
        rows.append(
            f"<tr><td>{l.line_nb}</td><td>{escape(l.item_nb)}</td>"
            f"<td>{escape(l.item_desc)}</td><td>{l.qty}</td>"
            f"<td>{escape(l.uom)}</td><td>{price}</td><td>{total}</td></tr>")
    note = ("<p><em>One or more items had no price on file - those lines "
            "are marked n/a and excluded from the total below.</em></p>"
            if bill.has_missing_prices else "")
    return f"""\
<html><body style="font-family: sans-serif;">
<h2>Bill for order {escape(bill.order_nb)}</h2>
<p><strong>Customer:</strong> {cust_name} ({escape(bill.customer.customer_number)
    if bill.customer else 'unknown'})</p>
{note}
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse;">
<tr><th>#</th><th>Item</th><th>Description</th><th>Qty</th><th>UoM</th>
<th>Unit price</th><th>Line total</th></tr>
{''.join(rows)}
</table>
<p><strong>Total: {bill.grand_total:.2f}</strong></p>
</body></html>"""
