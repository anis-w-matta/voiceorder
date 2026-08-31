"""PriorOrderService now delegates to catalog-service (order_header/
order_details moved there - see app/services/catalog_client.py) instead
of querying locally. The constructor still accepts a `session` argument
purely so every existing call site (draft_builder.py, pipeline.py, tests)
keeps working unchanged - it's never used internally any more.
"""
from app.services import catalog_client


class PriorOrderService:
    def __init__(self, session=None):
        self.s = session

    def open_orders(self, cust_nb: str):
        """No longer called anywhere directly - open-order disambiguation
        now happens inside catalog-service's resolve_target. Kept only so
        an old direct caller doesn't get an AttributeError; delegates to
        resolve_target's ambiguity contract isn't meaningful here, so this
        just isn't expected to be called."""
        raise NotImplementedError(
            "open_orders is now internal to catalog-service - use "
            "resolve_target/resolve_target_explicit instead")

    def find_so_by_order_nb(self, order_nb: str | None):
        return catalog_client.find_so_by_order_nb(order_nb)

    def lines_of(self, header):
        """`header` is a catalog_client.OrderRef, which already carries its
        lines (every catalog-service order-lookup endpoint returns them
        inline) - no second round trip needed."""
        return header.lines if header is not None else []

    def resolve_target(self, cust_nb: str, reference: str | None):
        return catalog_client.resolve_target(cust_nb, reference)

    def resolve_target_explicit(self, cust_nb: str, mode: str,
                                value: str | None):
        return catalog_client.resolve_target_explicit(cust_nb, mode, value)
