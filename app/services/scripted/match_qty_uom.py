"""Deterministic quantity/UOM resolution for a ParsedItemSpan's
quantity_text/uom_text. No LLM involved anywhere in this
module (spec section 17) - a number is either recognizable or it isn't.
"""
from decimal import Decimal, InvalidOperation

from app.services.quantity_uom import canonical_uom
from app.services.scripted.config import UOM_DICT
from app.services.scripted.models import QuantityUOM

# English + Lebanese Arabic (both script and Arabizi transliteration) +
# French spoken number words, 1-20. Arabic-script and Arabizi entries are
# kept side by side since ASR may transcribe the same spoken word either
# way depending on the utterance/model.
_NUMBER_WORDS: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20,
    "wa7de": 1, "wehde": 1, "wahde": 1, "واحد": 1, "وحدة": 1,
    "tinten": 2, "tnein": 2, "tinein": 2, "اثنين": 2, "تنين": 2,
    "tlet": 3, "tlete": 3, "ثلاثة": 3, "تلاتة": 3,
    "arba3a": 4, "arbaa": 4, "أربعة": 4, "اربعة": 4,
    "khamse": 5, "خمسة": 5,
    "sitte": 6, "ستة": 6, "سته": 6,
    "sabe3a": 7, "sabaa": 7, "سبعة": 7,
    "tmene": 8, "tmenye": 8, "ثمانية": 8, "تمانية": 8,
    "tese3a": 9, "tesaa": 9, "تسعة": 9,
    "3ashra": 10, "aashra": 10, "عشرة": 10, "عشره": 10,
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "sept": 7, "huit": 8, "neuf": 9, "dix": 10,
}


def _parse_number(word: str) -> Decimal | None:
    word = (word or "").strip().lower()
    if not word:
        return None
    try:
        parsed = Decimal(word)
    except InvalidOperation:
        parsed = None
    else:
        # Decimal("nan")/Decimal("infinity") parse without raising
        # InvalidOperation, but neither is a real quantity: NaN blows up
        # the qty <= 0 comparison downstream with an uncaught
        # InvalidOperation (this module's contract is "never guess, always
        # report status=error", not "sometimes crash"), and Infinity would
        # silently pass that comparison and could reach a Numeric(12,3) DB
        # column. Reject both here so they fall through to the word-lookup
        # below (where they'll correctly resolve to "unrecognized").
        if parsed.is_finite():
            return parsed
        parsed = None
    return Decimal(_NUMBER_WORDS[word]) if word in _NUMBER_WORDS else None


def _canonical_uom(text_val: str) -> str | None:
    return canonical_uom(text_val, UOM_DICT)


def parse_quantity_uom_span(quantity_text: str, uom_text: str) -> QuantityUOM:
    """Resolve a parser-produced (quantity_text, uom_text) span pair.
    Never guesses: an unrecognized quantity word or UOM word is reported
    as status="error" with the offending raw text preserved, not silently
    dropped or defaulted.
    """
    raw = f"{quantity_text} {uom_text}".strip()
    qty = _parse_number(quantity_text)
    if qty is None:
        return QuantityUOM(quantity=None, uom=_canonical_uom(uom_text),
                           raw_text=raw, status="error",
                           reason=f"unrecognized quantity {quantity_text!r}")
    if qty <= 0:
        return QuantityUOM(quantity=qty, uom=_canonical_uom(uom_text),
                           raw_text=raw, status="error",
                           reason="quantity must be positive")

    uom = _canonical_uom(uom_text)
    if uom is None:
        return QuantityUOM(quantity=qty, uom=None, raw_text=raw,
                           status="error",
                           reason=f"unrecognized unit {uom_text!r}")

    return QuantityUOM(quantity=qty, uom=uom, raw_text=raw, status="matched")
