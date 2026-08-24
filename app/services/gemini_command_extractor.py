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
   Each item has a description and a quantity, usually with a unit - this
   business only orders in two units, "each" and "packets" (a close
   variant/abbreviation like "pkt", "pack", or "ea" means the same thing).
   Example shape: "place order for <customer> ... <item 1> quantity
   <number> <unit> ... <item 2> quantity <number> <unit> ... the end". A
   leading counter word before an item ("one", "two", "item one") is not
   part of the item description - strip it.

   The quantity always comes immediately BEFORE its unit, never after, and
   quantity_text must always resolve to an actual number. A number is
   sometimes misheard/transcribed as a similar-sounding word right before
   "each"/"packets" - e.g. "to each" is really "two each" ("to" is the
   number 2 here, not the preposition), the same way "for" can mean "four"
   and "won" can mean "one". When the word immediately before the unit is
   one of these, extract the number it actually sounds like, not the
   literal word transcribed.

   Every real item has an explicit unit word ("each"/"packets"/a variant)
   spoken somewhere in its own span. Product names are frequently followed
   by a trailing number or pack-size code with no unit attached to it at
   all (e.g. "tendrex adult large 12x4", "napkins 200x2") - that trailing
   code is part of item_text, never a second item. Only start a new item
   when you reach the next number that is immediately followed by
   "each"/"packets"/a variant; a bare number or number-x-number pattern
   with no unit word right after it is never itself an item boundary.

2. return_order: references a previous order to return - either by order
   number, or a description of which order (e.g. a date). Optionally
   followed by specific items being returned; if no items are named at
   all, the whole referenced order is being returned (leave items empty).

3. reorder: names a customer and says to repeat an order - either "the
   same order" / "last time" (mode=last), a specific past order number
   (mode=order_nb), or an order from a spoken date (mode=date). The
   salesman may also change the order while repeating it ("reorder the
   same thing but 4 each of X instead", "repeat my last order, add 2
   packets of Y", "reorder order 12345 but drop the Z") - when they do,
   extract each changed/added item the same way place_order items are
   extracted (item_text/quantity_text/uom_text). Leave items empty for a
   plain, unmodified repeat.

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
        "the spoken quantity, always immediately before its unit and "
        "always resolved to an actual number (digits or a number word) - "
        "if a homophone was transcribed right before the unit (e.g. 'to' "
        "before 'each'), extract the number it sounds like ('two'), not "
        "the literal word"))
    uom_text: str = Field(description=(
        "the unit of measure exactly as said, or empty string if none was "
        "said - this business only uses \"each\" and \"packets\" (or a "
        "close variant/abbreviation of one of those two)"))


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
        # Unlike place_order, an empty customer_text is not a parse
        # failure here: resolve_reorder/build_reorder already handle an
        # unresolved customer gracefully (same "flag it for review, don't
        # reject outright" path as an ambiguous/not-found customer match),
        # and a reorder that only gives an order number ("reorder order
        # 12345 but...") has enough to work with on its own.
        customer_text = result.customer_text.strip()
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
                             reference=reference, items=_to_items(result.items))

    return ParseFailure(
        ParseError.COMMAND_START_NOT_FOUND,
        "Gemini did not recognize a place_order/return_order/reorder "
        "command", transcript)
