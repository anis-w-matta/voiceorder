import re
from dataclasses import dataclass
from decimal import Decimal

from app.services.normalization import NUMBER_RE as _PURE_NUMBER_RE

# The business only orders in two units: "each" (single/individual items)
# and "packets". Extend here, never inline a second unit vocabulary in the
# resolver/classifier/draft_builder. No Arabic-script/Arabizi synonyms are
# included for these two - unlike the size/color vocabulary elsewhere,
# guessing the wrong Lebanese Arabic word for "each"/"packets" would
# silently mismatch rather than just fail to match, so that's left for a
# native speaker to add correctly rather than guessed at here.
UOM_SYNONYMS: dict[str, str] = {
    "each": "EACH", "eaches": "EACH", "ea": "EACH",
    "packet": "PKT", "packets": "PKT", "pkt": "PKT", "pkts": "PKT",
}

# A number followed by AT LEAST ONE space and a word - used to recover a
# "100 packets"-shaped compound that landed whole in uom/raw_text instead
# of being split into qty=100/uom="packets" by the extractor. The space is
# required (not \s*) so a fused digit-letter word with no unit match (e.g.
# an Arabizi word using a leading digit as a consonant substitution) falls
# through to "not recoverable" instead of being wrongly split into a
# quantity + an unrecognized unit - that whole-word-vs-split ambiguity is
# exactly why parse_quantity_uom() below checks canonical_uom() on the
# whole string first, before ever reaching this regex.
_NUMBER_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?)\s+([a-zA-Z؀-ۿ3-9]+)")

# Splits text into whitespace/punctuation-delimited tokens (keeping "."
# attached, so decimals survive as one token) for _standalone_numbers below.
_TOKEN_SPLIT_RE = re.compile(r"[^\w.]+", re.UNICODE)
# _PURE_NUMBER_RE imported from normalization.NUMBER_RE above - not
# redefined here, so this and transcript_quality.py's identical "what counts
# as a number" pattern can't quietly drift apart.


@dataclass
class QuantityParse:
    qty: Decimal | None
    uom: str | None
    ok: bool


# canonical_uom() is called once per parsed quantity/UOM word - cheap on
# its own, but set(table.values()) was being rebuilt from scratch on every
# single call. table is always one of a handful of module-level constant
# dicts (UOM_SYNONYMS, scripted.config.UOM_DICT) that are never mutated
# after definition, so caching the value-set per table object is safe.
_uom_value_set_cache: dict[int, set[str]] = {}


def canonical_uom(word: str | None, table: dict[str, str] = UOM_SYNONYMS
                  ) -> str | None:
    """Case-normalize `word` to its canonical unit code via `table` (a
    synonym dict shaped like UOM_SYNONYMS: lowercase synonym -> canonical
    code). Shared - not reimplemented - by every module resolving a UOM
    word, including app/services/scripted/match_qty_uom.py, whose UOM_DICT
    is itself a superset of UOM_SYNONYMS (see scripted/config.py); keeping
    the canonicalization algorithm itself in one place means the two tables
    can differ in vocabulary without the lookup logic drifting apart too.
    """
    if not word:
        return None
    low = word.strip().lower()
    values = _uom_value_set_cache.get(id(table))
    if values is None:
        values = set(table.values())
        _uom_value_set_cache[id(table)] = values
    if low.upper() in values:
        return low.upper()
    return table.get(low)


def _canonical_uom(word: str | None) -> str | None:
    return canonical_uom(word, UOM_SYNONYMS)


def _standalone_numbers(text_val: str | None) -> list[Decimal]:
    """Numbers that stand entirely on their own as a whole token - never a
    digit run merely embedded in a larger alphanumeric token. This matters
    for two real, distinct shapes: Arabizi consonant-substitution letters
    reuse plain digits ("wa7de" is the word "one", not "wa" + the number
    7; "3ilbe" is the word "box", not "3" + "ilbe"), and real catalogue-
    style SKU/size codes glue digits directly onto letters ("12X4" is a
    pack-count code, not the number 12 or 4).

    A lookaround regex checking only immediate neighbours is NOT enough
    here: greedy \\d+ backtracks on a token like "12X4" (full "12" fails
    the letter-lookahead against "X", so it backtracks to just "1", whose
    next character "2" is a digit, not a letter - satisfying a naive
    lookahead and producing a wrong partial match "1"). Tokenizing first
    and requiring the WHOLE token to be purely numeric avoids that
    backtracking trap entirely.
    """
    numbers = []
    for token in _TOKEN_SPLIT_RE.split(text_val or ""):
        token = token.strip(".")
        if token and _PURE_NUMBER_RE.fullmatch(token):
            numbers.append(Decimal(token))
    return numbers


def parse_quantity_uom(raw_qty: float | None, raw_uom: str | None,
                       raw_text: str) -> QuantityParse:
    """Deterministic pass after Gemini extraction, so a compound quantity
    the extractor left un-split ("100 meters" dumped whole into uom, qty
    left null) still ends up as qty=100/uom=MTR instead of being lost, and
    so a qty the extractor DID separate out is cross-checked against what
    was actually spoken rather than trusted unconditionally - a
    hallucinated quantity (20 misheard/misextracted as 200) is exactly the
    kind of confidently-wrong output this project's safety rule exists to
    catch.

    Only sets ok=False (never guesses, always preserves the original raw
    qty/uom so nothing is silently lost) when: the qty is not a placeable
    positive quantity; the qty contradicts every standalone number actually
    present in raw_text; or a compound recovery is genuinely ambiguous
    (more than one distinct NUMBER-UNIT pair found).
    """
    if raw_qty is not None:
        qty_dec = Decimal(str(raw_qty))
        canon = _canonical_uom(raw_uom)
        uom = canon if canon else raw_uom

        if qty_dec <= 0:
            return QuantityParse(qty=qty_dec, uom=uom, ok=False)

        spoken_numbers = _standalone_numbers(raw_text)
        if spoken_numbers and qty_dec not in spoken_numbers:
            # The extractor's qty doesn't match any number actually spoken
            # in this line - never silently trust it, even though we don't
            # know the "right" value either.
            return QuantityParse(qty=qty_dec, uom=uom, ok=False)

        return QuantityParse(qty=qty_dec, uom=uom, ok=True)

    if raw_uom:
        # A bare word that's already a recognized unit on its own (e.g.
        # Arabizi "3ilbe" = "box") must be treated as such BEFORE trying to
        # split it into number+unit - otherwise the leading Arabizi
        # consonant-substitution digit gets misread as a spoken quantity
        # that was never actually said.
        whole = _canonical_uom(raw_uom)
        if whole:
            return QuantityParse(qty=None, uom=whole, ok=True)

        # qty is null but uom looks compound ("100 meters") - try to split it.
        matches = _NUMBER_UNIT_RE.findall(raw_uom)
        if len(matches) == 1:
            num, unit = matches[0]
            canon = _canonical_uom(unit)
            # Even when the unit word itself isn't in UOM_SYNONYMS, the
            # number is still real information extracted from a clear
            # NUMBER+UNIT pattern - keep it (with the unit as-is) rather
            # than discarding a perfectly good quantity just because the
            # accompanying unit word is unmapped.
            return QuantityParse(qty=Decimal(num), uom=canon or unit, ok=True)
        if len(matches) > 1:
            return QuantityParse(qty=None, uom=raw_uom, ok=False)

        # No number found anywhere and the whole string isn't a known unit
        # either - not recoverable, but not silently guessed at.
        return QuantityParse(qty=None, uom=raw_uom, ok=False)

    return QuantityParse(qty=None, uom=None, ok=True)
