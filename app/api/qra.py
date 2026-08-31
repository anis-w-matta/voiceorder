from fastapi import APIRouter, Depends

from app.api.deps import get_current_salesman
from app.schemas.api_out import QraDetailCacheOut, QraHeaderCacheOut
from app.services import catalog_client

router = APIRouter(tags=["qra"])


@router.get("/qra/all", response_model=list[QraHeaderCacheOut])
def list_all(_salesman=Depends(get_current_salesman)):
    """Every QRA agreement, for the Android app's local offline cache -
    same shape/trigger as items.py's /items/all. QRA is never created or
    edited from this app, so there's no corresponding write endpoint.
    Proxies catalog-service's own /qra/all.
    """
    return [
        QraHeaderCacheOut(
            cust_nb=h["cust_nb"], from_date=h["from_date"], to_date=h["to_date"],
            status=h["status"],
            details=[QraDetailCacheOut(**d) for d in h["details"]])
        for h in catalog_client.list_all_qra()
    ]
