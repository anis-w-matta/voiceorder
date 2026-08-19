"""normalize_item_text(): the single normalization pipeline shared by both
catalog descriptions (catalog.py) and salesman item spans (match_item.py),
so the two sides of every comparison are normalized identically. Built on
top of app.services.normalization.normalize_text (lowercase/NFC/whitespace/
punctuation, already shared with the rest of the app) rather than
reimplementing it.

extract_numeric_tokens(): pulls out pack/size numeric codes (12X4, 80x60)
in a canonical form so match_item.py can penalize/reject a candidate whose
numeric tokens conflict with what was actually spoken - fuzzy text
similarity alone would happily treat "12X4" and "20X4" as near-identical.
"""
import re

from app.services.normalization import normalize_text
from app.services.scripted.config import ABBREVIATION_DICT, LEBANESE_DICT

# NxN / NxN(cm) pack-size codes: "20x4", "20 X 4", "80x60cm", "20X 4".
# Deliberately requires digits on both sides of the x/× separator - this is
# what distinguishes a real pack code from an ordinary word containing "x".
_NUMERIC_PACK_RE = re.compile(r"(\d+)\s*[xX×]\s*(\d+)")


def extract_numeric_tokens(text_val: str) -> set[str]:
    """Canonical "NxM" tokens found in `text_val`, spacing/case-normalized
    so "20x4", "20 X 4", "20X4cm" all collapse to the same token "20x4".
    Never touches digits that aren't part of an NxM pattern (a bare pack
    count like a lone "4" is not distinctive enough to gate matching on).
    """
    return {f"{a}x{b}" for a, b in _NUMERIC_PACK_RE.findall(text_val or "")}


_GLUED_RE = re.compile(r"[a-z]+|\d+(?:x\d+)?")


def _split_glued(token: str) -> list[str]:
    """Real catalog descriptions routinely glue a size word straight onto
    a trailing pack-count digit with no separator ("MEDICA ADULT DIAPER
    MED20X4", "MEDICA PULL UPS MEDIUM14X6") - a plain whitespace split
    leaves "med20x4"/"medium14x6" as one token, which a dictionary lookup
    for "med"/"medium" then never matches, silently losing the size
    signal (spec section 21: size is a strong signal, must not be
    dropped). Splits on alpha-run/digit-run boundaries so "medium14x6" ->
    also yields "medium" and "14x6" as extra tokens for dictionary
    matching - additive, the original glued token is kept too.
    """
    parts = _GLUED_RE.findall(token)
    return parts if len(parts) > 1 else []


def _expand_dict(tokens: list[str], mapping: dict[str, str]) -> list[str]:
    """Append the canonical form for any WHOLE token or exact multi-word
    phrase found in `mapping`, keeping the original tokens too - additive,
    never destructive, so information is never erased (spec section 8:
    "must not erase information useful for distinguishing catalog items").

    Matches on token boundaries only (never a raw substring of the joined
    string) - a substring check would fire "ad" -> "adult" inside
    "undrpad", silently injecting a wrong signal into the normalized text.
    """
    out = list(tokens)
    present = set(tokens)
    joined = " ".join(tokens)
    for phrase, canon in mapping.items():
        phrase_tokens = phrase.split(" ")
        hit = (phrase in present if len(phrase_tokens) == 1
              else f" {phrase} " in f" {joined} ")
        if hit and canon.lower() not in present:
            out.append(canon.lower())
            present.add(canon.lower())
    return out


def normalize_item_text(text_val: str) -> str:
    """Deterministic normalization pipeline (spec section 8): unicode
    normalize / lowercase / whitespace-collapse / punctuation-strip (via
    normalize_text), then additive abbreviation + Lebanese/Arabizi synonym
    expansion. Numeric pack codes are left untouched by every step here -
    normalize_text already preserves digits, and neither dict below maps
    anything that looks like a number.
    """
    base = normalize_text(text_val)
    if not base:
        return base
    tokens = base.split(" ")
    for t in list(tokens):
        for sub in _split_glued(t):
            if sub not in tokens:
                tokens.append(sub)
    tokens = _expand_dict(tokens, ABBREVIATION_DICT)
    tokens = _expand_dict(tokens, LEBANESE_DICT)
    return " ".join(tokens)
