from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_salesman
from app.models import Salesman
from app.schemas.api_out import RecentOrderLineOut, RecentOrderOut
from app.services import catalog_client

router = APIRouter(tags=["orders"])


@router.get("/orders/recent", response_model=list[RecentOrderOut])
def recent_orders(limit: int = Query(30, le=100),
                  salesman: Salesman = Depends(get_current_salesman)):
    """Recently committed orders for this salesman's own customers (or
    every customer, for an admin), for the Android app's local offline
    cache (core/datastore's CacheDatabase) - never called automatically,
    only when the operator explicitly taps Refresh. Useful for reordering
    without a network round trip.

    Reads straight from catalog-service's order_header/customer, filtered
    by the same salesman_id ownership every other customer-facing endpoint
    uses - not from PendingRequest.decided_by, since a committed request's
    buffer row no longer exists once its order does (see
    OrderCommitService._finalize_committed).
    """
    orders = catalog_client.get_recent_orders(
        salesman_id=salesman.login_id, admin=salesman.is_admin, limit=limit)
    return [RecentOrderOut(
        order_nb=o.order_nb, order_type=o.order_type,
        cust_nb=o.cust_nb, customer_name=o.customer_name,
        lines=[RecentOrderLineOut(item_nb=l.item_nb, item_desc=l.item_desc,
                                  qty=l.qty, uom=l.uom) for l in o.lines])
           for o in orders]
