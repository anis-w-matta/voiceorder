"""Customer-ownership checks shared by the queue/review endpoints
(app/api/queue.py, app/api/review.py) - everywhere a salesman acts on a
PendingRequest/customer outside the accept()->commit()->POST /orders path,
which has its own, database-backed check inside catalog-service (see
app/services/commit.py and catalog-service's app/services/orders.py).

The security rule is the same everywhere it's applied: a salesman may only
see/act on a customer whose salesman_id matches their own login_id, unless
they're an admin. The authenticated salesman's identity is always the
source of truth - never anything supplied by the caller.
"""
from fastapi import HTTPException

from app.models import Salesman
from app.services import catalog_client

NOT_AUTHORIZED_DETAIL = "You are not authorized to place an order for this customer."


def owns_customer(cust_nb: str | None, salesman: Salesman) -> bool:
    """True if `salesman` may act on `cust_nb` - always true for an admin,
    always true for a not-yet-resolved request (cust_nb is None, so there's
    no customer to protect yet), otherwise only if catalog-service's
    Customer.salesman_id for this number matches."""
    if salesman.is_admin or cust_nb is None:
        return True
    detail = catalog_client.get_customer_detail(cust_nb)
    return detail is not None and detail.salesman_id == salesman.login_id


def require_customer_ownership(cust_nb: str | None, salesman: Salesman,
                               detail: str = NOT_AUTHORIZED_DETAIL) -> None:
    if not owns_customer(cust_nb, salesman):
        raise HTTPException(403, detail)
