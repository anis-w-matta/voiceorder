"""Centralized, extend-in-one-place configuration for the scripted-command
intake pipeline: anchor phrases for the fuzzy grammar, and the
abbreviation/Lebanese/UOM dictionaries normalization.py and match_qty_uom.py
draw from. Numeric thresholds live on app.config.Settings (env-tunable);
this module only holds static lookup tables re-exported for convenience.
"""
import json

from app.config import settings
from app.services.normalization import COLOR_SYNONYMS, SIZE_SYNONYMS
from app.services.quantity_uom import UOM_SYNONYMS


class AnchorPhraseLoadError(Exception):
    pass


def _load_anchor_phrases(path: str) -> tuple[dict, dict]:
    """Reads the salesman command grammar (anchor phrases + the reorder
    "last time" markers) from an editable JSON file instead of a Python
    literal, so adding a phrasing/language variant (spec section 12) is a
    one-file edit + restart, not a code change. Loaded once at import time,
    same as the rest of this module's tables - the app already restarts on
    every deploy, so a live-reload-without-restart path buys nothing here
    and would be one more thing to get wrong.

    Raises on a missing/malformed file rather than falling back to
    anything, matching CatalogLoadError's fail-fast style
    (app/services/scripted/catalog.py) - a broken command grammar should
    stop the app, not silently misparse every voice order.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise AnchorPhraseLoadError(
            f"could not load anchor phrases from {path!r}: {exc}") from exc
    try:
        return data["anchor_phrases"], data["reorder_mode_anchors"]
    except KeyError as exc:
        raise AnchorPhraseLoadError(
            f"{path!r} is missing required top-level key {exc}") from exc


FUZZY_DELIMITER_THRESHOLD = settings.fuzzy_delimiter_threshold
CUSTOMER_MATCH_THRESHOLD = settings.customer_match_threshold
CUSTOMER_MATCH_TIE_MARGIN = settings.customer_match_tie_margin
ITEM_FUZZY_THRESHOLD = settings.item_fuzzy_threshold
ITEM_AMBIGUITY_MARGIN = settings.item_ambiguity_margin
TOP_K_CANDIDATES = settings.top_k_candidates
NUMERIC_CONFLICT_PENALTY = settings.numeric_conflict_penalty

# Anchor phrases per structural marker, per command type. The parser fuzzy-
# matches the transcript against these (rapidfuzz), never exact-regex - ASR
# routinely mangles them ("place order four", "items", "item"). Extend by
# editing anchor_phrases.json (in this directory), never hard-code an anchor
# phrase inline in command_parser.py.
#
# Each list mixes English, (Lebanese) Arabic-script, and Arabizi/Latin-
# transliterated phrasings so the salesman can say the whole scripted
# command in any of those, or mix them - the anchor finder tries phrases in
# order (most specific first) and is language-agnostic, it just fuzzy-
# matches whichever phrase is actually present. Non-English phrasings are
# deliberately short/fixed, same rationale as the English ones: this is
# still a trained script, not free dictation.
ANCHOR_PHRASES, REORDER_MODE_ANCHORS = _load_anchor_phrases(
    settings.anchor_phrases_path)

# Which anchor identifies which command type, checked in this order - a
# transcript is routed to the first command type whose command_start anchor
# clears FUZZY_DELIMITER_THRESHOLD. "return_order" is checked before
# "place_order" because "return order" partially overlaps "order for" under
# loose fuzzy matching and is the more specific phrase.
COMMAND_TYPE_ORDER = ["return_order", "reorder", "place_order"]

# order_nb / date modes have no fixed anchor phrase (the salesman just says
# the number or the date) - reorder parsing checks for those by shape
# (digits -> order_nb, a recognizable date phrase -> date) after ruling out
# the "last" anchor. See command_parser._parse_reorder.
# order_nb / date modes have no fixed anchor phrase (the salesman just says
# the number or the date) - reorder parsing checks for those by shape
# (digits -> order_nb, a recognizable date phrase -> date) after ruling out
# the "last" anchor. See command_parser._parse_reorder.

# ABBREVIATION_DICT: catalogue-side ERP abbreviations expanded to a common
# word so a spoken full word matches a candidate that only ever spells out
# the abbreviation (spec section 9). Kept separate from LEBANESE_DICT
# (a translation problem) even though some entries overlap in effect.
ABBREVIATION_DICT: dict[str, str] = {
    "sml": "small", "med": "medium", "lrg": "large", "xlg": "extra large",
    "ad": "adult", "ctn": "carton", "pcs": "piece", "pkt": "packet",
    "sv": "save", "dis": "discount", "eco": "economy",
}

# LEBANESE_DICT: Lebanese Arabic / Arabizi size-word equivalents, reusing
# the single source of truth already centralized in
# app/services/normalization.py (SIZE_SYNONYMS/COLOR_SYNONYMS) rather than
# duplicating it - extend there, not here.
LEBANESE_DICT: dict[str, str] = {**SIZE_SYNONYMS, **COLOR_SYNONYMS}

# UOM_DICT: canonical unit-of-measure vocabulary, built on the existing
# UOM_SYNONYMS (app/services/quantity_uom.py) plus French terms the
# original dict didn't cover.
UOM_DICT: dict[str, str] = {
    **UOM_SYNONYMS,
    "douzaine": "DZ", "douzaines": "DZ", "dozen": "DZ", "dozens": "DZ",
    "darzen": "DZ",
    "caisse": "CTN", "caisses": "CTN", "kartouneh": "CTN", "kartuneh": "CTN",
    "unite": "PCS", "unites": "PCS", "unité": "PCS", "unités": "PCS",
    "sac": "PCS", "sacs": "PCS",
    "rouleau": "PCS", "rouleaux": "PCS",
}
