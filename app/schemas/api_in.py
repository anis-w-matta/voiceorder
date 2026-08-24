from decimal import Decimal

from pydantic import BaseModel, Field


class LineEditIn(BaseModel):
    line_nb: int
    item_nb: str | None = None
    item_desc: str | None = None
    qty: Decimal | None = None
    uom: str | None = None
    # When true and item_nb differs from what the resolver originally
    # suggested, the correction is saved as a new item_alias row (source
    # "human_correction") so the same spoken phrase resolves correctly next
    # time. See app/services/alias_learning.py.
    remember_alias: bool = False


class AcceptIn(BaseModel):
    order_type: str
    lines: list[LineEditIn]
    # Lines the operator explicitly deleted. Removal has to be stated here:
    # a line simply missing from `lines` is left untouched, so a partial or
    # buggy caller can no longer silently drop items from the order.
    removed_line_nbs: list[int] = Field(default_factory=list)
    note: str | None = None
    # An operator's manual customer pick, for requests the voice pipeline
    # couldn't identify a customer for. commit.py still validates this
    # against the customer table before committing - this is a proposal,
    # not a bypass of that check.
    cust_nb: str | None = None
    # A return's operator-supplied/corrected order reference. For a RETURN,
    # cust_nb is pulled from this order rather than picked independently
    # (see commit.py) - only meaningful when order_type == "RETURN".
    target_order_nb: str | None = None


class RejectIn(BaseModel):
    reason: str
    note: str | None = None


class CallbackIn(BaseModel):
    note: str | None = None
