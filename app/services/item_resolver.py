import math
import re
from dataclasses import asdict, dataclass

from rapidfuzz import fuzz
from sqlalchemy import select, text

from app.config import settings
from app.models import Item, OrderDetail, OrderHeader
from app.schemas.enums import MatchMethod
from app.services.normalization import (COLOR_WORDS, SIZE_SYNONYMS,
                                        SIZE_WORDS, expand_size_synonyms,
                                        normalize_color, normalize_size,
                                        normalize_text)


# Kept as module-level aliases (not just settings.*) for backward
# compatibility with existing importers (draft_builder.py) and tests.
FUZZY_ALIAS_THRESHOLD = settings.fuzzy_alias_threshold

# If the runner-up candidate is within this much of the top score, the two
# items are effectively tied and picking one over the other isn't a match,
# it's a coin flip - see TIE_EPSILON usage in resolve().
TIE_EPSILON = settings.resolver_tie_epsilon

# Size/color synonym dicts and the normalization helpers built on them now
# live in app/services/normalization.py, the single centralization point
# for text normalization shared across the resolver, the alias table, and
# transcript comparisons - imported above, not duplicated here.
_expand_size_synonyms = expand_size_synonyms
_normalize_size = normalize_size
_normalize_color = normalize_color
_SIZE_WORDS = SIZE_WORDS
_COLOR_WORDS = COLOR_WORDS


def _escape_ilike(value: str) -> str:
    """Escape ILIKE wildcard metacharacters so a literal transcript string
    is matched literally. Without this, a spoken/extracted item description
    containing a literal "%" (real catalogue text does - see
    _desc_discount_percent below, e.g. "SV20%") or "_" would be
    misinterpreted by Postgres as a wildcard, letting a supposedly "exact"
    match over-match unrelated rows.
    """
    return (value.replace("\\", "\\\\")
                 .replace("%", "\\%")
                 .replace("_", "\\_"))


def _letter_bounded(desc_lower: str, token: str) -> bool:
    """True if `token` appears not glued to surrounding LETTERS.

    Deliberately more permissive than _on_word_boundary (which also treats
    digits as word characters): real catalogue descriptions routinely glue
    a size abbreviation straight onto a trailing pack-count digit with no
    separator ("MEDICA ADULT DIAPER MED20X4" - no space between "MED" and
    "20X4"). Requiring a full alnum boundary there means the token is never
    found at all, silently weakening the attribute-conflict check for
    exactly the real data it exists to protect. A digit immediately after
    the token is accepted as a boundary; a letter is not (so "MED" still
    correctly fails to match inside "MEDICA").
    """
    return re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", desc_lower) is not None


def _desc_size(desc_lower: str) -> str | None:
    """Scan a catalogue item_desc for a known size, on a letter boundary so
    e.g. "MED" doesn't match inside "MEDICA" but does match "MED20X4"
    (glued pack-count suffix, no space). Checks both the abbreviation form
    (SML/MED/LRG/XLG) and the spelled-out form (SIZE_SYNONYMS' English
    keys, e.g. "large", "medium") since real catalogue descriptions use
    both inconsistently ("TENDREX ADULT MED 12X4" vs "MEDICA PULL UPS
    LARGE 14X6") - checking only the abbreviation silently found no size
    at all on the spelled-out rows, so a candidate whose size actually
    conflicted with what was asked for never got penalized.
    """
    for token in _SIZE_WORDS:
        if _letter_bounded(desc_lower, token.lower()):
            return token
    for word, abbrev in SIZE_SYNONYMS.items():
        if word.isascii() and _letter_bounded(desc_lower, word):
            return abbrev
    return None


def _desc_color(desc_lower: str) -> str | None:
    for token in _COLOR_WORDS:
        if _letter_bounded(desc_lower, token.lower()):
            return token
    return None


# Real catalogues bake a promotion's discount percentage directly into
# item_desc (e.g. "ELEGANCE MED 12X4 AD SV20%", "ELEGANCE PANT MED 14X6
# DIS40%") - this reads that number back out so a stated discount can be
# compared against it the same way size/color are.
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _desc_discount_percent(item_desc: str) -> float | None:
    m = _PERCENT_RE.search(item_desc)
    return float(m.group(1)) if m else None


def _attribute_conflict(item_desc: str, attributes: dict | None,
                        qualifiers: dict | None = None
                        ) -> tuple[bool, str | None]:
    """True + a human-readable reason if a candidate's inferred size/color/
    promotion (read textually off item_desc) explicitly contradicts an
    attribute or stated discount the customer actually gave.

    Only size, color, and an embedded discount percentage are checked -
    the only signals reliably inferable from free-text item_desc today.
    Brand/packaging/variant conflicts are NOT checked: Item has no
    structured columns for them, and there's no reliable textual
    convention to infer them from item_desc the way SML/MED/LRG/XLG and
    color words already are. This is a known, documented gap, not a silent
    one - adding structured attribute columns to Item would need a
    catalogue-import rework that's out of scope here.
    """
    desc_lower = item_desc.lower()

    if attributes:
        wanted_size = _normalize_size(attributes.get("size"))
        if wanted_size:
            got_size = _desc_size(desc_lower)
            if got_size and got_size != wanted_size:
                return True, f"size {got_size} != requested {wanted_size}"

        wanted_color = _normalize_color(attributes.get("color"))
        if wanted_color:
            got_color = _desc_color(desc_lower)
            if got_color and got_color != wanted_color:
                return True, f"color {got_color} != requested {wanted_color}"

    if qualifiers:
        wanted_pct = qualifiers.get("discount_percent")
        if wanted_pct is not None:
            got_pct = _desc_discount_percent(item_desc)
            # isclose, not !=: got_pct and wanted_pct are floats derived
            # independently (regex-parsed off item_desc vs. converted from
            # whatever the extractor produced), so an exact != comparison
            # would false-positive a "conflict" on values that are equal
            # but not bit-identical (e.g. a wanted_pct that arrives as
            # 33.300000000000004 after an upstream division).
            if got_pct is not None and not math.isclose(
                    got_pct, float(wanted_pct), abs_tol=1e-6):
                return True, (f"promotion {got_pct:g}% != requested "
                              f"{float(wanted_pct):g}%")

    return False, None


def _tokenize(text_val: str) -> list[str]:
    """Split into word-character runs (Unicode-aware, so Arabic counts)."""
    tokens, cur = [], []
    for ch in text_val:
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            tokens.append("".join(cur))
            cur = []
    if cur:
        tokens.append("".join(cur))
    return tokens


def _on_word_boundary(haystack: str, needle: str) -> bool:
    """True if `needle` occurs in `haystack` not glued to another word.

    Guards the substring fallback against matches buried inside a longer
    word ("brush" in "toothbrush"). Works for Arabic as well as Latin:
    str.isalnum() is Unicode-aware, so Arabic letters count as word
    characters and the space/punctuation around a spoken word does not.
    """
    if not needle:
        return False
    i = haystack.find(needle)
    while i != -1:
        before = haystack[i - 1] if i > 0 else " "
        after_i = i + len(needle)
        after = haystack[after_i] if after_i < len(haystack) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        i = haystack.find(needle, i + 1)
    return False


@dataclass
class Candidate:
    item_nb: str
    item_desc: str
    category: str
    score: float
    method: str
    attribute_conflict: bool = False
    conflict_reason: str | None = None

    def dict(self):
        return asdict(self)


def tied_with_top(cands: list, epsilon: float = TIE_EPSILON,
                  key=lambda c: c.score) -> list:
    """Every candidate within `epsilon` of the top score - a coin flip
    between distinct items, not a genuine match. `cands` must be sorted
    best-first (by `key`, descending); empty input returns empty.

    `key` defaults to `Candidate.score` but accepts any callable, so this
    is the single tie-break policy for every fuzzy-match module in the
    app - not just this one. app/services/scripted/match_customer.py uses
    it too (with `key=lambda t: t[2]` for its (nb, name, score) tuples)
    rather than reimplementing the same "is the runner-up within epsilon of
    the top score" check a second time.
    """
    if not cands:
        return []
    top_score = key(cands[0])
    return [c for c in cands if top_score - key(c) <= epsilon]


def unique_top(cands: list[Candidate], epsilon: float = TIE_EPSILON
              ) -> Candidate | None:
    """The top-scoring candidate, but only if it's uniquely best per
    `tied_with_top` - the single tie-break policy behind every auto-accept
    decision in this module (see the "Tendrex eco kbir" incident below:
    two different items scored 1.00 and one was silently auto-filled with
    full confidence - never again pick a coin flip)."""
    tied = tied_with_top(cands, epsilon)
    return cands[0] if len(tied) == 1 else None


class ItemResolver:
    def __init__(self, session, accept=None, suggest=None):
        self.s = session
        self.accept = accept if accept is not None else settings.fuzzy_accept
        self.suggest = suggest if suggest is not None else settings.fuzzy_suggest

    def _history(self, cust_nb: str) -> set[str]:
        return set(self.s.execute(
            select(OrderDetail.item_nb)
            .join(OrderHeader,
                  (OrderDetail.order_nb == OrderHeader.order_nb) &
                  (OrderDetail.order_type == OrderHeader.order_type))
            .where(OrderHeader.cust_nb == cust_nb)
        ).scalars().all())

    def resolve(self, raw: str, cust_nb: str | None = None,
                attributes: dict | None = None, qualifiers: dict | None = None):
        q = (raw or "").strip()
        if not q:
            return None, []

        def _exact(cands_in: list[Candidate]):
            """An otherwise-auto-accept exact match still has to clear two
            checks before being returned as a unique winner:

            1. Duplicates: two different SKUs sharing the same item_desc
               (real catalogues have these - see the item_alias_provenance
               era's TD-suffix variants) or two aliases normalizing to the
               same text must never be silently resolved to whichever the
               database happened to return first. This used to
               `SELECT ... LIMIT 1` with no ORDER BY, i.e. an arbitrary
               pick - fixed here by fetching every match and only
               auto-accepting when exactly one remains.
            2. Attribute conflict: a candidate whose size/color contradicts
               what the customer actually said isn't a match even at 1.0
               confidence, per the safety rule (never auto-select when
               attributes conflict).
            """
            # Dedupe by item_number first: the same item can have more than
            # one alias row that both normalize to the same query text
            # (e.g. two spellings of the same phrase) - that is not a tie
            # between different items, just one item matched twice.
            by_item: dict[str, Candidate] = {}
            for c in cands_in:
                by_item.setdefault(c.item_nb, c)
            cands = list(by_item.values())
            for c in cands:
                conflict, reason = _attribute_conflict(c.item_desc, attributes,
                                                       qualifiers)
                c.attribute_conflict, c.conflict_reason = conflict, reason
            clean = [c for c in cands if not c.attribute_conflict]
            if len(clean) == 1:
                return clean[0], cands
            return None, cands

        it = self.s.get(Item, q.upper())
        if it:
            c = Candidate(it.item_number, it.item_desc, it.category, 1.0,
                          MatchMethod.exact.value)
            return _exact([c])

        desc_matches = self.s.scalars(
            select(Item).where(
                Item.item_desc.ilike(_escape_ilike(q), escape="\\"))).all()
        if desc_matches:
            cands = [Candidate(it.item_number, it.item_desc, it.category, 0.98,
                               MatchMethod.exact.value) for it in desc_matches]
            return _exact(cands)

        alias_rows = self.s.execute(text("""
            SELECT i.item_number, i.item_desc, i.category
            FROM item_alias a JOIN item i ON i.item_number = a.item_number
            WHERE a.normalized_alias = :q
        """), {"q": normalize_text(q)}).all()
        if alias_rows:
            cands = [Candidate(row.item_number, row.item_desc, row.category, 0.96,
                               MatchMethod.alias.value) for row in alias_rows]
            return _exact(cands)

        # Punctuation-strip/whitespace-collapse before fuzzy comparison -
        # speech-to-text transcripts routinely carry commas/hyphens/ellipses
        # that add no real signal but measurably degrade both the pg_trgm
        # trigram similarity and rapidfuzz's token comparison below if left
        # in. The exact-match stages above don't need this (ILIKE handles
        # case, normalized_alias is pre-normalized) - only the fuzzy stage
        # actually scores on raw character content.
        q_fuzzy = _expand_size_synonyms(normalize_text(q))
        rows = self.s.execute(text("""
            SELECT item_number, item_desc, category, score, method FROM (
              SELECT i.item_number, i.item_desc, i.category,
                     similarity(i.item_desc, :q) AS score, 'fuzzy' AS method
              FROM item i WHERE i.item_desc % :q
              UNION ALL
              SELECT i.item_number, i.item_desc, i.category,
                     similarity(a.alias, :q) AS score, 'alias' AS method
              FROM item_alias a JOIN item i ON i.item_number = a.item_number
              WHERE a.alias % :q
            ) u ORDER BY score DESC LIMIT 30
        """), {"q": q_fuzzy}).all()

        hist = self._history(cust_nb) if cust_nb else set()
        # First pass: pick the best-scoring row per item_number, before any
        # attribute-conflict penalty. The UNION ALL above can return the
        # same item_number more than once (once via item.item_desc, again
        # via each item_alias row that points at it), and item_desc/category
        # are always identical across those duplicates (both come from the
        # same joined `item` row) - so _attribute_conflict's result and
        # penalty would be identical for every duplicate too, and applying
        # the same constant penalty to all of them can't change which
        # duplicate has the higher score. Deduping first means the
        # (regex-heavy) conflict check below runs once per unique item, not
        # once per duplicate row.
        best_raw: dict[str, tuple[float, str, str | None, str]] = {}
        for r in rows:
            rf = fuzz.token_set_ratio(q_fuzzy.lower(), r.item_desc.lower()) / 100.0
            score = max(float(r.score), rf) if r.method == "fuzzy" \
                else float(r.score)
            if r.item_number in hist:
                score = min(1.0, score + 0.10)
            if r.item_number not in best_raw or score > best_raw[r.item_number][0]:
                best_raw[r.item_number] = (score, r.item_desc, r.category, r.method)

        best: dict[str, Candidate] = {}
        for item_number, (score, item_desc, category, method) in best_raw.items():
            conflict, reason = _attribute_conflict(item_desc, attributes, qualifiers)
            if conflict:
                # Pushes a conflicting duplicate below its clean sibling
                # (e.g. the wrong-size variant of the same product), so the
                # TIE_EPSILON logic below picks the clean one uniquely
                # instead of treating them as a coin-flip tie.
                score = max(0.0, score - settings.attribute_conflict_penalty)
            best[item_number] = Candidate(
                item_number, item_desc, category, round(score, 3), method,
                attribute_conflict=conflict, conflict_reason=reason)

        cands = sorted(best.values(), key=lambda c: c.score, reverse=True)
        cands = [c for c in cands if c.score >= self.suggest][:5]
        top = None
        if cands and cands[0].score >= self.accept and not cands[0].attribute_conflict:
            top = unique_top(cands)
        return top, cands

    def find_in_text(self, text_val: str) -> list[Candidate]:
        """Find every catalogue item whose alias or description appears
        literally inside `text_val`. Covers the case where the extractor
        leaves `product` unset and returns the whole spoken sentence as
        raw_text: pg_trgm's `%` operator compares the *entire* sentence
        against a short alias, so its similarity usually falls below the
        default threshold and resolve()'s fuzzy path finds nothing even
        though the alias is right there in the text. Can return more than
        one item, e.g. two products named in a single merged line."""
        t = (text_val or "").strip()
        if not t:
            return []

        rows = self.s.execute(text("""
            SELECT i.item_number, i.item_desc, i.category,
                   a.alias AS alias, 'alias' AS method
            FROM item_alias a JOIN item i ON i.item_number = a.item_number
            WHERE length(a.alias) >= 3 AND strpos(lower(:t), lower(a.alias)) > 0
            UNION ALL
            SELECT i.item_number, i.item_desc, i.category,
                   i.item_desc AS alias, 'exact' AS method
            FROM item i
            WHERE length(i.item_desc) >= 3 AND strpos(lower(:t), lower(i.item_desc)) > 0
        """), {"t": t}).all()

        low = t.lower()
        best: dict[str, Candidate] = {}
        for r in rows:
            needle = (r.alias or "").lower()
            if not _on_word_boundary(low, needle):
                continue          # e.g. "brush" inside "toothbrush"
            score = 0.94 if r.method == "alias" else 0.90
            if r.item_number not in best or score > best[r.item_number].score:
                best[r.item_number] = Candidate(
                    r.item_number, r.item_desc, r.category, score,
                    MatchMethod.substring.value)

        # Arabizi has no standard spelling ("roleh" vs "rolleh", "lasse2"
        # vs "lase3"), so an exact literal substring often misses a word
        # a human would recognise instantly. Catch those with a fuzzy pass
        # over items the exact scan didn't already find, scored lower so
        # a genuine exact hit always wins the top slot.
        alias_rows = self.s.execute(text("""
            SELECT i.item_number, i.item_desc, i.category, a.alias AS alias
            FROM item_alias a JOIN item i ON i.item_number = a.item_number
            WHERE length(a.alias) >= 3
        """)).all()
        tokens = _tokenize(low)
        for r in alias_rows:
            if r.item_number in best:
                continue
            alias = (r.alias or "").lower()
            alias_tokens = alias.split()
            windows = tokens if len(alias_tokens) <= 1 else [
                " ".join(tokens[i:i + len(alias_tokens)])
                for i in range(len(tokens) - len(alias_tokens) + 1)]
            for cand_str in windows:
                if abs(len(cand_str) - len(alias)) > 2:
                    continue
                if fuzz.ratio(cand_str, alias) >= FUZZY_ALIAS_THRESHOLD:
                    best[r.item_number] = Candidate(
                        r.item_number, r.item_desc, r.category, 0.85,
                        MatchMethod.substring.value)
                    break

        return sorted(best.values(), key=lambda c: c.score, reverse=True)
