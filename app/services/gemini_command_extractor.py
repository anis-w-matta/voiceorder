"""Gemini-backed classification + field extraction for the salesman's
scripted-command intake (place_order / return_order / reorder), replacing
the deterministic anchor-phrase grammar that used to live in
command_parser.py.

This module only finds *where* the customer/item/quantity spans are in the
transcript - it never resolves them against the catalogue/customer table.
That stays entirely with match_customer.py / match_item.py /
item_resolver.py / match_qty_uom.py, unchanged: a hallucinated item or
customer is still structurally impossible, since those still do real DB
lookups/fuzzy matching on whatever raw text this module extracts. This
module's only job is "where is the customer name, an item, a quantity" -
not "does this item/customer exist".
"""
import logging
from typing import Literal

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app.config import settings
from app.services.gemini_retry import gemini_retry
from app.services.rate_limiter import gemini_rate_limiter
from app.services.scripted.models import (ParsedItemSpan, ParsedPlaceOrder,
                                          ParsedReorder, ParsedReturnOrder,
                                          ParseError, ParseFailure)

_log = logging.getLogger(__name__)

PROMPT = """You are extracting the structure of a wholesale salesman's
spoken order command from its transcript. The transcript may mix English,
Arabic script, and Arabizi (Latin-transliterated Arabic) - extract text
spans exactly as they appear in the transcript, do not translate them.

The salesman's speech is always one of exactly three command shapes, or
none of them:

1. place_order: names a customer, then lists one or more items to order.
   Each item has a description and a quantity, usually with a unit
   (carton, piece, dozen, etc). Example shape: "place order for <customer>
   ... <item 1> quantity <number> <unit> ... <item 2> quantity <number>
   <unit> ... the end". A leading counter word before an item ("one",
   "two", "item one") is not part of the item description - strip it.

2. return_order: references a previous order to return - either by order
   number, or a description of which order (e.g. a date). Optionally
   followed by specific items being returned; if no items are named at
   all, the whole referenced order is being returned (leave items empty).

3. reorder: names a customer and says to repeat an order - either "the
   same order" / "last time" (mode=last), a specific past order number
   (mode=order_nb), or an order from a spoken date (mode=date).

Extract each item as a separate (item_text, quantity_text, uom_text)
triple. item_text is the product description exactly as spoken - do NOT
correct it, resolve it to what you think is the "real" product name, or
invent one that fits better; a separate system matches this text against
the actual catalogue, so your only job is finding the boundary of what was
said. quantity_text is the spoken number (digits or a number word) exactly
as said. uom_text is the spoken unit word exactly as said, or an empty
string if no unit was mentioned.

customer_text (place_order/reorder) and order_reference (return_order's
referenced order, or reorder's order_nb/date reference) are likewise
extracted exactly as spoken, never corrected or guessed at.

If the transcript does not describe any of these three command shapes at
all (e.g. small talk, an unrelated question, silence), return
command_type="none" and leave every other field empty - never force-fit
one of the three shapes onto unrelated speech.

Also rate your own confidence (0.0-1.0) in this extraction, based on how
clearly the command structure could be identified - not on whether the
order itself makes sense.

Return only the structured result in the requested format - no
explanations."""


class _GeminiItem(BaseModel):
    item_text: str = Field(description=(
        "the item/product description exactly as spoken - never "
        "corrected, resolved, or invented"))
    quantity_text: str = Field(description=(
        "the spoken quantity (digits or number word) exactly as said"))
    uom_text: str = Field(description=(
        "the spoken unit of measure exactly as said, or empty string if "
        "none was said"))


class _GeminiCommand(BaseModel):
    command_type: Literal["place_order", "return_order", "reorder", "none"]
    customer_text: str = Field(default="", description=(
        "customer name/number exactly as spoken - place_order/reorder only"))
    order_reference: str = Field(default="", description=(
        "the referenced order (number or spoken date) for return_order, "
        "or reorder's order_nb/date reference"))
    items: list[_GeminiItem] = Field(default_factory=list)
    # Gemini's structured-output schema rejects an empty string as an enum
    # member ("enum[n]: cannot be empty") - null (not "") is how "not a
    # reorder, or reorder mode wasn't stated" is represented.
    reorder_mode: Literal["last", "date", "order_nb"] | None = None
    confidence: float = Field(default=0.0, description=(
        "0.0-1.0 self-rated confidence in this extraction"))


class GeminiCommandExtractor:
    """Implements the command_parser.parse(transcript) -> ParsedPlaceOrder |
    ParsedReturnOrder | ParsedReorder | ParseFailure contract
    (app/services/scripted/models.py), so resolve_order.py and everything
    downstream of it need no changes - only where the parsed spans come
    from is different.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.client = genai.Client(api_key=api_key or settings.gemini_api_key)
        self.model = model or settings.gemini_model

    @gemini_retry()
    def _generate_once(self, transcript: str) -> _GeminiCommand:
        with gemini_rate_limiter:
            resp = self.client.models.generate_content(
                model=self.model,
                contents=[PROMPT, transcript],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_GeminiCommand,
                    temperature=0.0,
                ),
            )
        if isinstance(resp.parsed, _GeminiCommand):
            return resp.parsed
        if resp.parsed is not None:
            return _GeminiCommand.model_validate(resp.parsed)
        return _GeminiCommand.model_validate_json(resp.text)

    def extract(self, transcript: str
               ) -> ParsedPlaceOrder | ParsedReturnOrder | ParsedReorder | ParseFailure:
        text = (transcript or "").strip()
        if not text:
            return ParseFailure(ParseError.COMMAND_START_NOT_FOUND,
                                "empty transcript", transcript)
        try:
            result = self._generate_once(text)
        except Exception as e:
            # A permanent failure here (malformed response, API error after
            # retries exhausted) must still produce the same "ask for a
            # retry, never guess" outcome as a grammar miss did - never
            # raise out of the pipeline and never fabricate a command.
            _log.warning("Gemini command extraction failed", exc_info=True)
            return ParseFailure(ParseError.COMMAND_START_NOT_FOUND,
                                f"extraction failed: {e}", transcript)
        return _to_parsed(result, transcript)


def _to_items(items: list[_GeminiItem]) -> list[ParsedItemSpan]:
    return [ParsedItemSpan(item_text=i.item_text.strip(),
                           quantity_text=i.quantity_text.strip(),
                           uom_text=i.uom_text.strip())
           for i in items]


def _to_parsed(result: _GeminiCommand, transcript: str
              ) -> ParsedPlaceOrder | ParsedReturnOrder | ParsedReorder | ParseFailure:
    if result.command_type == "place_order":
        customer_text = result.customer_text.strip()
        if not customer_text:
            return ParseFailure(ParseError.CUSTOMER_DELIMITER_NOT_FOUND,
                                "no customer extracted", transcript)
        return ParsedPlaceOrder(customer_text=customer_text,
                                items=_to_items(result.items))

    if result.command_type == "return_order":
        order_reference = result.order_reference.strip()
        if not order_reference:
            return ParseFailure(ParseError.ORDER_REFERENCE_NOT_FOUND,
                                "no order reference extracted", transcript)
        return ParsedReturnOrder(order_reference=order_reference,
                                 items=_to_items(result.items))

    if result.command_type == "reorder":
        customer_text = result.customer_text.strip()
        if not customer_text:
            return ParseFailure(ParseError.CUSTOMER_DELIMITER_NOT_FOUND,
                                "no customer extracted", transcript)
        mode = result.reorder_mode
        if mode not in ("last", "date", "order_nb"):
            return ParseFailure(ParseError.REORDER_MODE_NOT_FOUND,
                                "no reorder mode extracted", transcript)
        reference = result.order_reference.strip() or None
        if mode != "last" and not reference:
            return ParseFailure(ParseError.REORDER_MODE_NOT_FOUND,
                                f"reorder mode {mode!r} requires a reference",
                                transcript)
        return ParsedReorder(customer_text=customer_text, mode=mode,
                             reference=reference)

    return ParseFailure(
        ParseError.COMMAND_START_NOT_FOUND,
        "Gemini did not recognize a place_order/return_order/reorder "
        "command", transcript)
