from rapidfuzz import fuzz

UNABLE_TO_CLASSIFY = "unable to classify"

CATEGORY_FUZZY_THRESHOLD = 80


def classify_line(*, matched_category: str | None, raw_text: str,
                  known_categories: list[str]) -> str:
    """Three-tier fallback: resolved item's own category, then a guess from
    the category vocabulary against the raw text, then "unable to classify".

    Tier 1 is "classify from the item description": item resolution (see
    ItemResolver) already matched this line's text against a real catalogue
    description/alias, so the category of *that* match is the
    description-derived classification - no separate step needed for lines
    that already resolved.
    """
    if matched_category:
        return matched_category

    text = (raw_text or "").strip().lower()
    if not text:
        return UNABLE_TO_CLASSIFY

    for cat in known_categories:
        c = cat.lower()
        if c in text or fuzz.partial_ratio(c, text) >= CATEGORY_FUZZY_THRESHOLD:
            return cat

    return UNABLE_TO_CLASSIFY
