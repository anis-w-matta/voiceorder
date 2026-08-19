import re
import unicodedata

# Centralized text normalization shared by every downstream comparison
# (extracted product text, item_alias.alias, resolver queries) so the
# spoken side and the catalogue side are normalized identically. Callers
# keep the original text alongside whatever this produces - normalize_text
# never replaces raw_text/alias, it only derives a comparison-friendly copy.

# Matches a bare int/decimal token (e.g. "12", "0.6") - shared by every
# module that pulls "the numbers actually present in this text" out for a
# safety/agreement check (quantity_uom.py's standalone-number scan,
# transcript_quality.py's attempt-to-attempt number comparison), so the
# definition of "a number" can't quietly drift between them.
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def normalize_text(s: str) -> str:
    """Lowercase, NFC-normalize, collapse whitespace, strip punctuation.

    Never strips digits: Arabizi letters (3=ع, 7=ح, 2=ء/ق, 5=خ, 8/6=ط,
    9=ص) and spoken quantities both depend on them.
    """
    if not s:
        return ""
    text_val = unicodedata.normalize("NFC", s).lower().strip()
    # Strip punctuation (anything that's not alphanumeric/whitespace, in a
    # Unicode-aware way so Arabic letters are never touched), then collapse
    # runs of whitespace left behind.
    text_val = "".join(ch if ch.isalnum() or ch.isspace() else " "
                       for ch in text_val)
    return re.sub(r"\s+", " ", text_val).strip()


# Distributor catalogues almost always abbreviate size in Latin letters
# (SML/MED/LRG/XLG) that a customer never actually says - "kbir"/"كبير"
# has zero character overlap with "LRG", so a raw trigram/fuzzy comparison
# has nothing to match the size word against even though a human reads it
# instantly. Sourced from abbreviations actually seen in a real catalogue
# import; extend as new conventions turn up rather than assuming this list
# is exhaustive.
SIZE_SYNONYMS: dict[str, str] = {
    "small": "SML", "sghir": "SML", "zghir": "SML", "zghire": "SML",
    "sghire": "SML", "صغير": "SML", "صغيرة": "SML", "صغار": "SML",
    "medium": "MED", "wasat": "MED", "متوسط": "MED", "وسط": "MED",
    "large": "LRG", "kbir": "LRG", "kbeer": "LRG", "kbire": "LRG",
    "kabir": "LRG", "كبير": "LRG", "كبيرة": "LRG", "كبار": "LRG",
    "extra large": "XLG", "xlarge": "XLG", "x-large": "XLG",
}

# Same idea as SIZE_SYNONYMS but for color: a spoken/Arabizi color word
# mapped to the canonical English word a catalogue item_desc is expected to
# contain, so attribute-conflict checking can compare like with like.
COLOR_SYNONYMS: dict[str, str] = {
    "red": "RED", "ahmar": "RED", "a7mar": "RED", "7amar": "RED",
    "احمر": "RED", "أحمر": "RED",
    "blue": "BLUE", "azraq": "BLUE", "azra2": "BLUE", "أزرق": "BLUE",
    "white": "WHITE", "abyad": "WHITE", "أبيض": "WHITE",
    "black": "BLACK", "aswad": "BLACK", "أسود": "BLACK",
    "green": "GREEN", "akhdar": "GREEN", "أخضر": "GREEN",
    "yellow": "YELLOW", "asfar": "YELLOW", "أصفر": "YELLOW",
}

SIZE_WORDS = {"SML", "MED", "LRG", "XLG"}
COLOR_WORDS = set(COLOR_SYNONYMS.values())


def expand_size_synonyms(q: str) -> str:
    low = q.lower()
    extra = [abbrev for word, abbrev in SIZE_SYNONYMS.items() if word in low]
    extra = list(dict.fromkeys(extra))  # dedupe, keep first-seen order
    return q if not extra else f"{q} {' '.join(extra)}"


def normalize_size(word: str | None) -> str | None:
    if not word:
        return None
    low = word.strip().lower()
    if low.upper() in SIZE_WORDS:
        return low.upper()
    return SIZE_SYNONYMS.get(low)


def normalize_color(word: str | None) -> str | None:
    if not word:
        return None
    low = word.strip().lower()
    if low.upper() in COLOR_WORDS:
        return low.upper()
    return COLOR_SYNONYMS.get(low)
