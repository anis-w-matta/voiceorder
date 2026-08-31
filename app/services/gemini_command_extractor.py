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

PROMPT = """You are listening to a wholesale salesman's spoken order
command, transcribed from mixed English, Arabic script, and Arabizi
(Latin-transliterated Arabic) speech - extract text spans exactly as they
appear in the transcript, do not translate them. Real speech is messy:
words get repeated, salesmen switch languages mid-sentence, correct
themselves, add filler words, and say things in whatever order feels
natural - not necessarily "who, then what, then how much." Understand
what was actually meant, the way a human dispatcher who speaks all three
languages would - don't look for one fixed sentence shape.

Every command is trying to do exactly one of these three things, or none
of them:

- place_order: start a new order for a customer, with one or more items
  and quantities.
- return_order: send back all or part of a previous order (identified by
  order number, or a description like a date). If no items are named at
  all, the whole referenced order is being returned (leave items empty).
- reorder: repeat a previous order, identified by its order number
  (mode=order_nb - the only mode there is; a reorder always needs an
  explicit order number, never "my last order" or a spoken date) -
  optionally with some items changed ("reorder order 12345 but 4 each of
  X instead", "reorder order 12345 but drop the Z"). Extract only the
  changed/added items; leave items empty for a plain, unmodified repeat.
- none: small talk, an unrelated question, silence, or anything else -
  never force-fit one of the three shapes onto unrelated speech.

For whichever shape applies, extract:
- customer_text (place_order/reorder): the customer's name or number,
  exactly as spoken, never corrected or guessed at.
- order_reference (return_order/reorder): the referenced order - a
  number or a spoken description like a date - exactly as spoken.
- items: one (item_text, quantity_text, uom_text) triple per item
  actually ordered, in whatever order they were said. item_text is the
  product description exactly as spoken - do NOT correct it, resolve it
  to what you think is the "real" product name, or invent one that fits
  better; a separate system matches this text against the actual
  catalogue, so your only job is finding the boundary of what was said.
  A leading counter word before an item ("one", "two", "item one") is
  not part of the item description - strip it (see judgment call 3
  below for the different case of "item"/"code" followed by the
  product's actual catalogue number). quantity_text is the
  spoken number (digits or a number word) exactly as said, resolved to
  an actual number - a number is sometimes misheard/transcribed as a
  similar-sounding word right next to its unit (e.g. "to each" is really
  "two each", the same way "for" can mean "four" and "won" can mean
  "one"); when that happens, extract the number it actually sounds like,
  not the literal word transcribed. uom_text is the spoken unit word
  exactly as said, or empty string if none was mentioned - this business
  only orders in "each" or "packets" (a close variant/abbreviation of
  either counts).

Two judgment calls come up often enough to call out:

1. A bare number attached to a customer-referring word ("for", "to",
   Arabic "la"/"ل-") almost always identifies the CUSTOMER, not an
   order. A number attached to "order"/"reorder"/"repeat" (English or
   Arabic) almost always identifies an ORDER being referenced or
   repeated. The same digits can show up in a transcript meaning either
   one - decide from the word they're attached to, not from "order"
   merely appearing somewhere nearby. When nothing in the sentence
   actually signals "this is a repeat/return of something" (no
   "again"/"same"/"reorder"/"return"/reference to a past order), treat
   it as a new place_order rather than guessing reorder or return - a
   new order is the default case; a repeat or return needs its own real
   signal, not just the presence of the word "order".

2. Item and quantity don't have to appear in "item, then quantity+unit"
   order - they're often said the other way round, or split apart by
   filler or a self-correction. Match each quantity+unit to whichever
   item it actually describes by meaning, not by position in the
   sentence, and use a corrected value over whatever was said first.

3. "Item"/"code"/"number"/"SKU" (English or Arabic, e.g. "رقم") followed
   by a number is ambiguous the same way customer numbers are, and
   splits into two different things depending on what the number looks
   like. A small number that reads as a position in the list ("item
   one", "item two") is the leading-counter case already covered above -
   strip it. A longer number that reads as the product's own catalogue number
   ("item 165227", "code 165227") is the product identifier itself -
   extract just the number as item_text, with the wrapper word stripped,
   the same way a customer number gets extracted without "for"/"la"
   attached. Don't keep "item"/"code"/"el item" glued to the digits -
   the system matching item_text against the catalogue looks up a bare
   number as an exact product code first, and a leftover wrapper word
   breaks that lookup.

Also rate your own confidence (0.0-1.0): how sure you are you understood
the command correctly, not whether the order itself makes sense. When you
had to make a judgment call like the ones above with little to go on,
that should pull your confidence down.

The examples below show how to reason about messy input, not phrases to
pattern-match against - real transcripts will rarely look exactly like
these:

- "place order for Sara Supermarket, two boxes of chips each, five
  packets of napkins" -> place_order, customer "Sara Supermarket",
  items: [chips/2/each, napkins/5/packets].

- "بدي حط أوردر لـ 45000، فيها 3 عبوات من الشيبس الكبير" ("I want to
  place an order for 45000, in it 3 packets of the big chips") ->
  place_order, customer_text "45000" (attached to "la", so it's the
  customer, not an order reference), items: [item_text "الشيبس الكبير",
  quantity_text "3", uom_text "عبوات"] - the quantity+unit was said
  before the item description, but it still belongs to that one item.

- "reorder order number 12345 but add 2 each of Pepsi" -> reorder, mode
  "order_nb", order_reference "12345", items: [Pepsi/2/each] - here
  "order number" is the explicit repeat signal that's missing in the
  example below.

- "hot order la 58466 fiyo 7 packets men el item 165227" -> nothing here
  says "repeat"/"reorder"/"same as before" - "order la 58466" reads as
  "an order for 58466", not a reference to an existing order, so 58466
  is the customer, not an order number to look up: place_order,
  customer_text "58466", items: [item_text "165227" (judgment call 3 -
  165227 reads as the product's own catalogue number, not a position in
  a list, so "el item" is stripped and just the number is kept),
  quantity_text "7", uom_text "packets"] (confidence should be on the
  lower side here since the item was identified only by a bare number,
  not a product name - not because the command shape itself is
  unclear).

- "return order 9821, the customer doesn't want it" -> return_order,
  order_reference "9821", items: [].

- "place order for customer sixty triple two, uh two packs - no wait,
  three packs of the red juice" -> place_order, customer_text "customer
  sixty triple two", items: [red juice/3/packs] - the corrected
  quantity, not the first one said.

- "hey what time do we close today" -> none, every other field empty.

Return only the structured result in the requested format - no
explanations."""


class _GeminiItem(BaseModel):
    item_text: str = Field(description=(
        "the item/product description exactly as spoken - never "
        "corrected, resolved, or invented"))
    quantity_text: str = Field(description=(
        "the spoken quantity for this item, wherever it was said relative "
        "to the item description - always resolved to an actual number "
        "(digits or a number word). If a homophone was transcribed right "
        "next to the unit (e.g. 'to each'), extract the number it sounds "
        "like ('two'), not the literal word"))
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
        "or reorder's order number"))
    items: list[_GeminiItem] = Field(default_factory=list)
    # Gemini's structured-output schema rejects an empty string as an enum
    # member ("enum[n]: cannot be empty") - null (not "") is how "not a
    # reorder, or no order number was stated" is represented.
    reorder_mode: Literal["order_nb"] | None = None
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
        reference = result.order_reference.strip() or None
        if result.reorder_mode != "order_nb" or not reference:
            return ParseFailure(ParseError.REORDER_MODE_NOT_FOUND,
                                "no reorder order number extracted", transcript)
        return ParsedReorder(customer_text=customer_text, mode="order_nb",
                             reference=reference, items=_to_items(result.items))

    return ParseFailure(
        ParseError.COMMAND_START_NOT_FOUND,
        "Gemini did not recognize a place_order/return_order/reorder "
        "command", transcript)
