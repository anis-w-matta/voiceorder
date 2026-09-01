from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_salesman, get_db, require_admin
from app.models import Salesman
from app.schemas.api_out import CustomerCacheOut, CustomerCandidateOut
from app.schemas.auth import SalesmanOut
from app.schemas.customers import AssignSalesmanIn, CustomerDetailOut
from app.services import catalog_client
from app.services.authorization import require_customer_ownership

router = APIRouter(tags=["customers"])


@router.get("/customers/search", response_model=list[CustomerCandidateOut])
def search(q: str = Query(..., min_length=1),
          salesman: Salesman = Depends(get_current_salesman)):
    """Free-text customer lookup for the Request screen's "select customer"
    flow - proxies catalog-service's own /customers/search (same fuzzy
    scoring match_customer.py uses to auto-resolve a customer during
    voice intake, just without its threshold/tie-margin gating).

    Restricted to the caller's own assigned customers unless they're an
    admin - the identity driving that filter always comes from the
    authenticated bearer token (get_current_salesman), never a client-
    supplied parameter.
    """
    return [CustomerCandidateOut(cust_nb=nb, customer_name=name, score=score)
           for nb, name, score in catalog_client.search_customers(
               q, salesman_id=salesman.login_id, admin=salesman.is_admin)]


@router.get("/customers/all", response_model=list[CustomerCacheOut])
def list_all(salesman: Salesman = Depends(get_current_salesman)):
    """The full customer list, for the Android app's local offline cache
    (core/datastore's CacheDatabase) - never called automatically, only
    when the operator explicitly taps Refresh. Proxies catalog-service's
    /customers/all, restricted to the caller's own book unless admin (see
    search() above).
    """
    return [CustomerCacheOut(cust_nb=r.cust_nb, customer_name=r.customer_name)
           for r in catalog_client.list_all_customers(
               salesman_id=salesman.login_id, admin=salesman.is_admin)]


@router.get("/customers/{cust_nb}", response_model=CustomerDetailOut)
def get_customer(cust_nb: str,
                 salesman: Salesman = Depends(get_current_salesman)):
    """Direct single-customer lookup. Must not expose another salesman's
    customer just because the caller knows/guesses the number (spec: "GET
    /customers/7 must NOT return Customer 7 to Salesman A" when it belongs
    to Salesman B) - same 403 as every other ownership-gated endpoint, not
    a 404, so this behaves identically to accept()/claim() for a caller
    probing for the difference between "doesn't exist" and "not yours"."""
    detail = catalog_client.get_customer_detail(cust_nb)
    if detail is None:
        raise HTTPException(404, "no such customer")
    require_customer_ownership(cust_nb, salesman)
    return CustomerDetailOut(
        cust_nb=detail.cust_nb, customer_name=detail.customer_name,
        email=detail.email, telephone=detail.telephone, city=detail.city,
        address1=detail.address1, salesman_id=detail.salesman_id)


@router.get("/salesmen", response_model=list[SalesmanOut],
           dependencies=[Depends(require_admin)])
def list_salesmen(include_inactive: bool = Query(default=False),
                  s: Session = Depends(get_db)):
    """Admin-only - backs both the customer-assignment picker (which only
    ever wants active salesmen to assign to) and the admin app's salesman
    roster screen (which also needs to see deactivated accounts, to
    reactivate them - include_inactive=true). Default preserves the
    picker's existing behavior."""
    q = select(Salesman).order_by(Salesman.name)
    if not include_inactive:
        q = q.where(Salesman.is_active.is_(True))
    return list(s.scalars(q))


@router.patch("/customers/{cust_nb}/salesman", response_model=CustomerDetailOut,
             dependencies=[Depends(require_admin)])
def assign_salesman(cust_nb: str, body: AssignSalesmanIn,
                    s: Session = Depends(get_db)):
    """Admin-only reassignment of a customer's owning salesman (or
    clearing it, with salesman_id=null). Takes effect immediately: the
    next request either salesman makes for this customer sees the new
    owner, since ownership is always re-checked from the database, never
    cached in a token or session.
    """
    if catalog_client.get_customer_detail(cust_nb) is None:
        raise HTTPException(404, "no such customer")
    if body.salesman_id is not None:
        target = s.get(Salesman, body.salesman_id)
        if target is None or not target.is_active:
            raise HTTPException(422, "no such active salesman")
    detail = catalog_client.assign_customer_salesman(cust_nb, body.salesman_id)
    return CustomerDetailOut(
        cust_nb=detail.cust_nb, customer_name=detail.customer_name,
        email=detail.email, telephone=detail.telephone, city=detail.city,
        address1=detail.address1, salesman_id=detail.salesman_id)
