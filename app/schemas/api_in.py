from decimal import Decimal

from pydantic import BaseModel, Field


class LineEditIn(BaseModel):
    line_nb: int
    item_nb: str | None = None
    item_desc: str | None = None
    qty: Decimal | None = None
    uom: str | None = None


class AcceptIn(BaseModel):
    order_type: str
    lines: list[LineEditIn]
    # Lines the operator explicitly deleted. Removal has to be stated here:
    # a line simply missing from `lines` is left untouched, so a partial or
    # buggy caller can no longer silently drop items from the order.
    removed_line_nbs: list[int] = Field(default_factory=list)
    note: str | None = None


class RejectIn(BaseModel):
    reason: str
    note: str | None = None


class CallbackIn(BaseModel):
    note: str | None = None


class BillRequestIn(BaseModel):
    cust_nb: str
    order_nb: str
    order_type: str = "SO"
