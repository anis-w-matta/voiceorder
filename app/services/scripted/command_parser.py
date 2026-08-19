"""Fuzzy-anchor grammar parser for the scripted salesman commands
(place_order / return_order / reorder).

Responsibility is deliberately narrow (spec section 12): find the
structural boundaries only. It never resolves a catalog item, a customer,
or a quantity - it only produces raw text spans for match_customer.py /
match_qty_uom.py / match_item.py to resolve independently. ASR is
imperfect, so every boundary is found via RapidFuzz against a configurable
anchor-phrase list (config.ANCHOR_PHRASES), never exact string/regex
matching - but a boundary is never fabricated: if confidence is
insufficient the parser returns a ParseFailure instead of guessing.
"""
import re
import string

from rapidfuzz import fuzz

from app.services.scripted.config import (ANCHOR_PHRASES, COMMAND_TYPE_ORDER,
                                          FUZZY_DELIMITER_THRESHOLD,
                                          REORDER_MODE_ANCHORS, UOM_DICT)
from app.services.scripted.models import (ParsedItemSpan, ParsedPlaceOrder,
                                          ParsedReorder, ParsedReturnOrder,
                                          ParseError, ParseFailure)

_PUNCT = string.punctuation


def _tokenize(transcript: str) -> tuple[list[str], list[str]]:
    """(original-case tokens, lowercase-punctuation-stripped tokens),
    positionally aligned - spans are reconstructed by joining a slice of
    the original tokens, comparisons are done against the lowercase ones.
    """
    raw = transcript.split()
    low = [t.lower().strip(_PUNCT) for t in raw]
    return raw, low


def _window_score(window_tokens: list[str], phrase: str) -> float:
    return fuzz.ratio(" ".join(window_tokens), phrase)


def _find_anchor_from(low_tokens: list[str], phrases: list[str],
                      start: int, threshold: float
                      ) -> tuple[int, int, float] | None:
    """Leftmost, best-scoring occurrence of `phrases` in low_tokens[start:],
    searching window lengths of len(phrase)-1..+1 words to tolerate ASR
    dropping/adding a word. `phrases` is tried in the given order (most
    specific/longest first, per config.ANCHOR_PHRASES) and the first
    phrase that clears `threshold` at its best-scoring position wins -
    NOT a global argmax across phrases, so a more specific multi-word
    anchor ("place order for") is preferred over a shorter one it
    contains ("place order") rather than losing to it on raw ratio score.
    Returns (span_start, span_end, score) with span_end exclusive, or None
    if nothing clears `threshold`.
    """
    n = len(low_tokens)
    for phrase in phrases:
        plen = len(phrase.split())
        best: tuple[int, int, float] | None = None
        for wlen in sorted({max(1, plen - 1), plen, plen + 1}):
            for i in range(start, n - wlen + 1):
                score = _window_score(low_tokens[i:i + wlen], phrase)
                if score >= threshold and (best is None or score > best[2]
                                           or (score == best[2] and i < best[0])):
                    best = (i, i + wlen, score)
        if best is not None:
            return best
    return None


def _find_anchor_all(low_tokens: list[str], phrases: list[str],
                     start: int, end: int, threshold: float
                     ) -> list[tuple[int, int, float]]:
    """Every non-overlapping occurrence of `phrases` between [start, end),
    scanned left-to-right, greedily consuming a match before continuing -
    used to locate every quantity_marker inside an items span."""
    hits: list[tuple[int, int, float]] = []
    # `end` is invariant across the loop, so slice once rather than
    # rebuilding the same list copy on every iteration (once per quantity
    # marker found).
    windowed = low_tokens[:end]
    pos = start
    while pos < end:
        found = _find_anchor_from(windowed, phrases, pos, threshold)
        if found is None:
            break
        hits.append(found)
        pos = found[1]
    return hits


def _span_text(raw_tokens: list[str], start: int, end: int) -> str:
    return " ".join(raw_tokens[start:end]).strip()


_DIGITS_RE = re.compile(r"^\d+$")

# Leading list-counter words ("item ONE tendrex...", "TWO medical
# underpad...") the salesman says to mark a new item in the list - not
# part of the product description itself (spec section 40's own worked
# example strips these from item_text). Stripped only when there's more
# text after the counter, so an item genuinely named "One" isn't mangled
# into an empty span.
_ITEM_COUNTER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten",
    "واحد", "اثنين", "تنين", "ثلاثة", "تلاتة", "أربعة", "اربعة", "خمسة",
    "ستة", "سبعة", "ثمانية", "تمانية", "تسعة", "عشرة", "عشره",
}


def _strip_leading_counter(raw_tokens: list[str], low_tokens: list[str],
                           start: int, end: int) -> int:
    if (end - start > 1 and start < len(low_tokens) and
       low_tokens[start] in _ITEM_COUNTER_WORDS):
        return start + 1
    return start


# Copula the salesman says between the quantity_marker word and the actual
# number ("quantity IS six carton") - a natural way to say the line that
# isn't a different word choice for the marker itself (that's what
# config.ANCHOR_PHRASES/quantity_marker is for), just filler between it and
# the value. Without skipping this, quantity_text captured the filler word
# itself ("is") instead of the number right after it.
_QTY_FILLER_WORDS = {"is", "are", "was", "of", "هي", "هو"}


def _skip_qty_filler(low_tokens: list[str], start: int, end: int) -> int:
    if start < end and start < len(low_tokens) and low_tokens[start] in _QTY_FILLER_WORDS:
        return start + 1
    return start


def _consume_uom(low_tokens: list[str], start: int, hard_end: int) -> int:
    """From `start`, greedily consume tokens that extend a known UOM_DICT
    phrase (closed vocabulary, same trick item_resolver.find_in_text uses
    for aliases), so a multi-word unit ("square feet") is captured without
    a fixed word-count guess absorbing the next item's text. Returns the
    end index (exclusive); consumes at least 1 token if nothing matches,
    since some uom_text is still better than none for match_qty_uom.py to
    report an explicit error on.
    """
    if start >= hard_end:
        return start
    best_end = min(start + 1, hard_end)
    for span_len in range(1, min(3, hard_end - start) + 1):
        candidate = " ".join(low_tokens[start:start + span_len])
        if candidate in UOM_DICT:
            best_end = start + span_len
    return best_end


def _detect_command_type(low_tokens: list[str]
                         ) -> tuple[str, int, int, float] | None:
    best: tuple[str, int, int, float] | None = None
    for ctype in COMMAND_TYPE_ORDER:
        anchors = ANCHOR_PHRASES[ctype]["command_start"]
        found = _find_anchor_from(low_tokens, anchors, 0, FUZZY_DELIMITER_THRESHOLD)
        if found and (best is None or found[2] > best[3]):
            best = (ctype, found[0], found[1], found[2])
    return best


def parse(transcript: str) -> ParsedPlaceOrder | ParsedReturnOrder | ParsedReorder | ParseFailure:
    raw_tokens, low_tokens = _tokenize(transcript)
    if not raw_tokens:
        return ParseFailure(ParseError.COMMAND_START_NOT_FOUND,
                            "empty transcript", transcript)

    detected = _detect_command_type(low_tokens)
    if detected is None:
        return ParseFailure(
            ParseError.COMMAND_START_NOT_FOUND,
            "no place_order/return_order/reorder start phrase recognized",
            transcript)
    command_type, _, cmd_end, _ = detected

    if command_type == "place_order":
        return _parse_place_order(raw_tokens, low_tokens, cmd_end, transcript)
    if command_type == "return_order":
        return _parse_return_order(raw_tokens, low_tokens, cmd_end, transcript)
    return _parse_reorder(raw_tokens, low_tokens, cmd_end, transcript)


def _find_end_marker(low_tokens: list[str], start: int, ctype: str
                     ) -> int:
    """End of the relevant span: the command_end anchor if confidently
    found after `start`, else the end of the transcript (trailing ASR
    noise past a genuine "the end" is deliberately swallowed by this
    fallback rather than causing a hard failure - spec section 14 Step 4).
    """
    anchors = ANCHOR_PHRASES[ctype]["command_end"]
    found = _find_anchor_from(low_tokens, anchors, start, FUZZY_DELIMITER_THRESHOLD)
    return found[0] if found else len(low_tokens)


def _find_implicit_qty_markers(low_tokens: list[str], start: int, end: int
                               ) -> list[tuple[int, int, float]]:
    """Fallback for a salesman who never says a quantity_marker word at all
    ("tendrex adult large 5 carton medica pull ups 3 dozen the end"): every
    bare digit immediately followed by a known UOM_DICT word/phrase, found
    left-to-right same as _find_anchor_all. UOM_DICT is a closed,
    unambiguous vocabulary, so "<digit> <uom>" is a safe signal with no
    marker word needed - zero-width hits (m_start == m_end == the digit's
    own position), so the preceding item text runs right up to it.

    Only ever called when _find_anchor_all found zero explicit markers in
    the whole items span (see _parse_items_span) - never mixed with
    explicit-marker parsing within the same command. That matters: a few
    real catalogue descriptions embed their own "<number> <uom>" (e.g.
    "ALUMINUM CLASSIC 25 SQFT"), which this would misidentify as the
    order's quantity if it ran on every command - restricting it to
    all-or-nothing keeps that misfire from ever landing mid-list on a
    command that also uses explicit "quantity" markers for its other
    items, which is the common case even for salesmen who sometimes drop
    the marker.
    """
    hits: list[tuple[int, int, float]] = []
    pos = start
    while pos < end:
        found = None
        for i in range(pos, end):
            if not low_tokens[i].isdigit():
                continue
            for span_len in range(1, min(3, end - i - 1) + 1):
                if " ".join(low_tokens[i + 1:i + 1 + span_len]) in UOM_DICT:
                    found = i
                    break
            if found is not None:
                break
        if found is None:
            break
        hits.append((found, found, 100.0))
        pos = found + 1
    return hits


def _parse_items_span(raw_tokens: list[str], low_tokens: list[str],
                      start: int, end: int, ctype: str
                      ) -> list[ParsedItemSpan] | ParseFailure:
    qty_anchors = ANCHOR_PHRASES[ctype]["quantity_marker"]
    markers = _find_anchor_all(low_tokens, qty_anchors, start, end,
                               FUZZY_DELIMITER_THRESHOLD)
    if not markers:
        markers = _find_implicit_qty_markers(low_tokens, start, end)
    if not markers:
        return ParseFailure(ParseError.ITEM_QUANTITY_NOT_FOUND,
                            "no quantity marker found inside items span",
                            " ".join(raw_tokens))

    items: list[ParsedItemSpan] = []
    item_start = start
    for i, (m_start, m_end, _score) in enumerate(markers):
        item_text_start = _strip_leading_counter(raw_tokens, low_tokens,
                                                 item_start, m_start)
        item_text = _span_text(raw_tokens, item_text_start, m_start)
        if not item_text:
            return ParseFailure(
                ParseError.NO_ITEMS_FOUND,
                f"quantity marker at token {m_start} has no preceding "
                "item text", " ".join(raw_tokens))
        qty_start = _skip_qty_filler(low_tokens, m_end, end)
        qty_end = min(qty_start + 1, end)
        quantity_text = _span_text(raw_tokens, qty_start, qty_end)
        uom_end = _consume_uom(low_tokens, qty_end, end)
        uom_text = _span_text(raw_tokens, qty_end, uom_end)
        items.append(ParsedItemSpan(item_text=item_text,
                                    quantity_text=quantity_text,
                                    uom_text=uom_text))
        item_start = uom_end
    return items


def _parse_place_order(raw_tokens, low_tokens, cmd_end, transcript):
    items_anchors = ANCHOR_PHRASES["place_order"]["items_start"]
    items_hit = _find_anchor_from(low_tokens, items_anchors, cmd_end,
                                  FUZZY_DELIMITER_THRESHOLD)
    if items_hit is None:
        return ParseFailure(ParseError.ITEMS_DELIMITER_NOT_FOUND,
                            "no 'items' delimiter found after customer",
                            transcript)
    customer_text = _span_text(raw_tokens, cmd_end, items_hit[0])
    if not customer_text:
        return ParseFailure(ParseError.CUSTOMER_DELIMITER_NOT_FOUND,
                            "no customer text between command start and "
                            "items delimiter", transcript)

    end_idx = _find_end_marker(low_tokens, items_hit[1], "place_order")
    items = _parse_items_span(raw_tokens, low_tokens, items_hit[1], end_idx,
                              "place_order")
    if isinstance(items, ParseFailure):
        return items
    return ParsedPlaceOrder(customer_text=customer_text, items=items)


def _parse_return_order(raw_tokens, low_tokens, cmd_end, transcript):
    items_anchors = ANCHOR_PHRASES["return_order"]["items_start"]
    items_hit = _find_anchor_from(low_tokens, items_anchors, cmd_end,
                                  FUZZY_DELIMITER_THRESHOLD)
    ref_end = items_hit[0] if items_hit else _find_end_marker(
        low_tokens, cmd_end, "return_order")
    order_reference = _span_text(raw_tokens, cmd_end, ref_end)
    if not order_reference:
        return ParseFailure(ParseError.ORDER_REFERENCE_NOT_FOUND,
                            "no order number found after 'return order'",
                            transcript)

    if items_hit is None:
        return ParsedReturnOrder(order_reference=order_reference, items=[])

    end_idx = _find_end_marker(low_tokens, items_hit[1], "return_order")
    items = _parse_items_span(raw_tokens, low_tokens, items_hit[1], end_idx,
                              "return_order")
    if isinstance(items, ParseFailure):
        return items
    return ParsedReturnOrder(order_reference=order_reference, items=items)


def _parse_reorder(raw_tokens, low_tokens, cmd_end, transcript):
    marker_anchors = ANCHOR_PHRASES["reorder"]["same_order_marker"]
    marker_hit = _find_anchor_from(low_tokens, marker_anchors, cmd_end,
                                   FUZZY_DELIMITER_THRESHOLD)
    if marker_hit is None:
        return ParseFailure(ParseError.ITEMS_DELIMITER_NOT_FOUND,
                            "no 'same order' marker found after customer",
                            transcript)
    customer_text = _span_text(raw_tokens, cmd_end, marker_hit[0])
    if not customer_text:
        return ParseFailure(ParseError.CUSTOMER_DELIMITER_NOT_FOUND,
                            "no customer text between 'reorder for' and "
                            "'same order'", transcript)

    end_idx = _find_end_marker(low_tokens, marker_hit[1], "reorder")
    mode_text = _span_text(raw_tokens, marker_hit[1], end_idx)
    if not mode_text:
        return ParseFailure(ParseError.REORDER_MODE_NOT_FOUND,
                            "nothing said after 'same order'", transcript)

    last_hit = _find_anchor_from(low_tokens[marker_hit[1]:end_idx],
                                 REORDER_MODE_ANCHORS["last"], 0,
                                 FUZZY_DELIMITER_THRESHOLD)
    if last_hit is not None:
        return ParsedReorder(customer_text=customer_text, mode="last",
                             reference=None)

    first_token_digits = re.sub(r"\D", "", mode_text.split()[0])
    if len(first_token_digits) >= 3:
        return ParsedReorder(customer_text=customer_text, mode="order_nb",
                             reference=first_token_digits)

    return ParsedReorder(customer_text=customer_text, mode="date",
                         reference=mode_text)
