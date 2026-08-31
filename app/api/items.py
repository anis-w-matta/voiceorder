from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_salesman
from app.schemas.api_out import CandidateOut, ItemCacheOut
from app.services import catalog_client

router = APIRouter(tags=["items"])


@router.get("/items/search", response_model=list[CandidateOut])
def search_items(q: str = Query(..., min_length=1),
                 _salesman=Depends(get_current_salesman)):
    """Free-text item lookup for the Request screen's "add item" flow -
    proxies catalog-service's own /items/search, which reuses the same
    fuzzy resolver that resolves items during voice intake, so a
    manually-added line gets the exact same ranked-candidate dropdown an
    auto-extracted line does.
    """
    return [CandidateOut(**row) for row in catalog_client.search_items(q)]


@router.get("/items/all", response_model=list[ItemCacheOut])
def list_all(_salesman=Depends(get_current_salesman)):
    """The full item catalogue, for the Android app's local offline cache
    (core/datastore's CacheDatabase) - never called automatically, only
    when the operator explicitly taps Refresh. Proxies catalog-service's
    /items/all.
    """
    return [ItemCacheOut(item_nb=r.item_nb, item_desc=r.item_desc,
                         category=r.category)
           for r in catalog_client.list_all_items()]
