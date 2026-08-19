from sqlalchemy import select

from app.models import ItemAlias
from app.services.activity_log import log_standalone
from app.services.normalization import normalize_text


def maybe_learn_alias(session, *, raw_text: str, item_nb: str,
                      suggested_item_nb: str | None,
                      remember: bool) -> ItemAlias | None:
    """Turn an operator's catalogue correction into a reusable alias.

    Writes a new item_alias row (source="human_correction", confidence=1.0)
    only when the operator explicitly asked to remember it AND their chosen
    item genuinely differs from what the resolver suggested - a correction
    that just confirms the resolver's own top pick teaches nothing new.

    Refuses to write (and logs a warn-level activity entry instead) if the
    same normalized alias already maps to a DIFFERENT item: a contradiction
    between what a human just said and what the catalogue already claims
    needs a person to look at it, not a silent overwrite. No-op if the
    identical (item, normalized_alias) pair already exists.
    """
    if not remember or not raw_text or not raw_text.strip():
        return None
    if suggested_item_nb == item_nb:
        return None

    normalized = normalize_text(raw_text)
    if not normalized:
        return None

    existing = session.scalars(select(ItemAlias).where(
        ItemAlias.normalized_alias == normalized)).all()
    for row in existing:
        if row.item_number == item_nb:
            return None  # already known, nothing new to learn
    conflicting = [row for row in existing if row.item_number != item_nb]
    if conflicting:
        log_standalone(
            "alias_learning_conflict",
            f"correction '{raw_text}' -> {item_nb} conflicts with existing "
            f"alias(es) for {sorted({r.item_number for r in conflicting})} - "
            "not overwritten, needs human review",
            level="warn", cust_nb=None,
            details={"raw_text": raw_text, "item_nb": item_nb,
                    "conflicting_items": sorted(
                        {r.item_number for r in conflicting})})
        return None

    alias = ItemAlias(item_number=item_nb, alias=raw_text, lang="unknown",
                      source="human_correction", confidence=1.0)
    session.add(alias)
    session.flush()
    return alias
