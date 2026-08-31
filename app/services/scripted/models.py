"""Shared dataclasses/enums for the scripted-command intake pipeline
(app/services/scripted/*). Deliberately plain dataclasses, not pydantic -
this layer is internal wiring between deterministic Python functions, not
an API boundary that needs (de)serialization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Literal


class ParseError(str, Enum):
    """Machine-readable parse-failure taxonomy - see spec section 28.
    Every one of these means "asked for a retry", never "guessed"."""
    COMMAND_START_NOT_FOUND = "COMMAND_START_NOT_FOUND"
    CUSTOMER_DELIMITER_NOT_FOUND = "CUSTOMER_DELIMITER_NOT_FOUND"
    ITEMS_DELIMITER_NOT_FOUND = "ITEMS_DELIMITER_NOT_FOUND"
    COMMAND_END_NOT_FOUND = "COMMAND_END_NOT_FOUND"
    NO_ITEMS_FOUND = "NO_ITEMS_FOUND"
    ITEM_QUANTITY_NOT_FOUND = "ITEM_QUANTITY_NOT_FOUND"
    ORDER_REFERENCE_NOT_FOUND = "ORDER_REFERENCE_NOT_FOUND"
    REORDER_MODE_NOT_FOUND = "REORDER_MODE_NOT_FOUND"


class MatchStatus(str, Enum):
    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


@dataclass
class ParseFailure:
    error: ParseError
    detail: str
    raw_transcript: str


@dataclass
class ParsedItemSpan:
    item_text: str
    quantity_text: str
    uom_text: str


@dataclass
class ParsedPlaceOrder:
    command_type: Literal["place_order"] = "place_order"
    customer_text: str = ""
    items: list[ParsedItemSpan] = field(default_factory=list)


@dataclass
class ParsedReturnOrder:
    command_type: Literal["return_order"] = "return_order"
    order_reference: str = ""
    items: list[ParsedItemSpan] = field(default_factory=list)

    @property
    def is_full_return(self) -> bool:
        return not self.items


@dataclass
class ParsedReorder:
    command_type: Literal["reorder"] = "reorder"
    customer_text: str = ""
    # "order_nb" is the only mode left - a reorder always needs an
    # explicit order number now (order_header dropped created_at, so
    # "same as last time"/"the order from date X" have nothing to resolve
    # against any more).
    mode: Literal["order_nb"] = "order_nb"
    reference: str | None = None  # the referenced order number
    # A reorder can also change the order while repeating it ("reorder the
    # same thing but 4 each of X instead") - empty means a plain repeat.
    items: list[ParsedItemSpan] = field(default_factory=list)


ParsedCommand = ParsedPlaceOrder | ParsedReturnOrder | ParsedReorder


@dataclass
class CustomerMatch:
    customer_number: str | None
    customer_name: str | None
    score: float
    status: MatchStatus
    candidates: list[tuple[str, str, float]] = field(default_factory=list)


@dataclass
class QuantityUOM:
    quantity: Decimal | None
    uom: str | None
    raw_text: str
    status: Literal["matched", "error"]
    reason: str | None = None


@dataclass
class ItemCandidate:
    item_number: str
    item_description: str
    item_family: str | None
    score: float
    numeric_compatible: bool
    numeric_conflict_reason: str | None = None


@dataclass
class ItemMatchResult:
    item_number: str | None
    item_description: str | None
    item_family: str | None
    status: MatchStatus
    score: float | None
    method: str  # "exact" | "fuzzy" | "llm"
    candidates: list[ItemCandidate] = field(default_factory=list)
    explanation: str = ""
    llm_used: bool = False


@dataclass
class ResolvedOrderLine:
    raw_item_text: str
    qty: QuantityUOM
    match: ItemMatchResult


@dataclass
class ScriptedOrderResult:
    status: Literal["success", "needs_confirmation", "parse_error"]
    command_type: str | None = None
    customer: CustomerMatch | None = None
    lines: list[ResolvedOrderLine] = field(default_factory=list)
    order_reference: str | None = None
    reorder_mode: str | None = None
    full_return: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
