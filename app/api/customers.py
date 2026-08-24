from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_salesman, get_db
from app.schemas.api_out import CustomerCandidateOut
from app.services.scripted.match_customer import search_customers

router = APIRouter(tags=["customers"])


@router.get("/customers/search", response_model=list[CustomerCandidateOut])
def search(q: str = Query(..., min_length=1), s: Session = Depends(get_db),
           _salesman=Depends(get_current_salesman)):
    """Free-text customer lookup for the Request screen's "select customer"
    flow - reuses the same fuzzy scoring match_customer.py already uses to
    auto-resolve a customer during voice intake, just without its
    threshold/tie-margin gating (see search_customers' docstring).
    """
    return [
        CustomerCandidateOut(cust_nb=nb, customer_name=name,
                             phone_e164=phone, score=score)
        for nb, name, phone, score in search_customers(s, q)
    ]
