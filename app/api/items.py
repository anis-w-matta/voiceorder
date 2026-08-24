from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_salesman, get_db
from app.schemas.api_out import CandidateOut
from app.services.scripted.match_item import resolve_item

router = APIRouter(tags=["items"])


@router.get("/items/search", response_model=list[CandidateOut])
def search_items(q: str = Query(..., min_length=1), s: Session = Depends(get_db),
                 _salesman=Depends(get_current_salesman)):
    """Free-text item lookup for the Request screen's "add item" flow -
    reuses the same fuzzy resolver (app/services/scripted/match_item.py)
    that resolves items during voice intake, so a manually-added line gets
    the exact same ranked-candidate dropdown an auto-extracted line does,
    not a second, differently-behaved search implementation.
    """
    match = resolve_item(s, q)
    return [
        CandidateOut(item_nb=c.item_number, item_desc=c.item_description,
                     category=c.item_family or "", score=c.score,
                     method=match.method, attribute_conflict=not c.numeric_compatible)
        for c in match.candidates[:5]
    ]
