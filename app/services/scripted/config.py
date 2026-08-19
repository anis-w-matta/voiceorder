"""Centralized, extend-in-one-place configuration for the scripted-command
intake pipeline: the abbreviation/Lebanese/UOM dictionaries
normalization.py and match_qty_uom.py draw from. Numeric thresholds live
on app.config.Settings (env-tunable); this module only holds static lookup
tables re-exported for convenience.
"""
from app.config import settings
from app.services.normalization import COLOR_SYNONYMS, SIZE_SYNONYMS
from app.services.quantity_uom import UOM_SYNONYMS

CUSTOMER_MATCH_THRESHOLD = settings.customer_match_threshold
CUSTOMER_MATCH_TIE_MARGIN = settings.customer_match_tie_margin
ITEM_FUZZY_THRESHOLD = settings.item_fuzzy_threshold
ITEM_AMBIGUITY_MARGIN = settings.item_ambiguity_margin
TOP_K_CANDIDATES = settings.top_k_candidates
NUMERIC_CONFLICT_PENALTY = settings.numeric_conflict_penalty

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
