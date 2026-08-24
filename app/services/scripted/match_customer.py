"""Fuzzy customer resolution for the scripted-command pipeline.

The customer list is much smaller than the item catalog, so this is a
simpler problem than match_item.py - a single RapidFuzz pass against
customer_name (and a direct lookup against customer_number, for the
"reorder for C001" phrasing) is enough. The one rule that matters as much
here as it does for items: never silently pick a customer when two
candidates are effectively tied (spec section 16).
"""
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer
from app.services.item_resolver import tied_with_top
from app.services.normalization import normalize_text
from app.services.scripted.config import (CUSTOMER_MATCH_THRESHOLD,
                                          CUSTOMER_MATCH_TIE_MARGIN)
from app.services.scripted.models import CustomerMatch, MatchStatus


def match_customer(session: Session, raw_text: str,
                   threshold: float = CUSTOMER_MATCH_THRESHOLD,
                   tie_margin: float = CUSTOMER_MATCH_TIE_MARGIN
                   ) -> CustomerMatch:
    query = (raw_text or "").strip()
    if not query:
        return CustomerMatch(None, None, 0.0, MatchStatus.NOT_FOUND)

    exact_nb = session.get(Customer, query.upper())
    if exact_nb is not None:
        return CustomerMatch(exact_nb.customer_number, exact_nb.customer_name,
                             100.0, MatchStatus.MATCHED)

    q_norm = normalize_text(query)
    customers = list(session.scalars(select(Customer)))
    if not customers:
        return CustomerMatch(None, None, 0.0, MatchStatus.NOT_FOUND)

    scored: list[tuple[str, str, float]] = []
    for c in customers:
        name_score = fuzz.token_sort_ratio(q_norm, normalize_text(c.customer_name))
        nb_score = (fuzz.ratio(q_norm, normalize_text(c.customer_number))
                   if c.customer_number else 0.0)
        scored.append((c.customer_number, c.customer_name,
                       max(name_score, nb_score)))
    scored.sort(key=lambda t: t[2], reverse=True)

    top = scored[0]
    if top[2] < threshold:
        return CustomerMatch(None, None, top[2], MatchStatus.NOT_FOUND,
                             candidates=scored[:5])

    # Same tie-safety policy as item_resolver.tied_with_top (see its
    # docstring) - reused, not reimplemented, just keyed on this module's
    # (customer_number, customer_name, score) tuple shape instead of
    # Candidate.score.
    tied = tied_with_top(scored, epsilon=tie_margin, key=lambda t: t[2])
    if len(tied) > 1:
        return CustomerMatch(None, None, top[2], MatchStatus.AMBIGUOUS,
                             candidates=scored[:5])

    return CustomerMatch(top[0], top[1], top[2], MatchStatus.MATCHED,
                         candidates=scored[:5])


def search_customers(session: Session, q: str, limit: int = 5
                     ) -> list[tuple[str, str, str | None, float]]:
    """Ranked customer lookup for an explicit human search (the Request
    screen's "select customer" picker) - unlike match_customer, this never
    applies CUSTOMER_MATCH_THRESHOLD/tie-margin gating, since a human
    reviewer picking from a visible list doesn't need the auto-resolution
    safety net that exists to stop the *pipeline* from silently guessing.
    """
    query = (q or "").strip()
    if not query:
        return []
    q_norm = normalize_text(query)
    customers = list(session.scalars(select(Customer)))
    scored = [
        (c.customer_number, c.customer_name, c.phone_e164,
         max(fuzz.token_sort_ratio(q_norm, normalize_text(c.customer_name)),
             fuzz.ratio(q_norm, normalize_text(c.customer_number))
             if c.customer_number else 0.0))
        for c in customers
    ]
    scored.sort(key=lambda t: t[3], reverse=True)
    return scored[:limit]
