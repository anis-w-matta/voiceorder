from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_salesman, get_db
from app.models import ActivityLog, Salesman
from app.schemas.api_out import ActivityLogOut
from app.services import catalog_client
from app.services.authorization import require_customer_ownership

router = APIRouter(tags=["activity"])


@router.get("/activity", response_model=list[ActivityLogOut])
def list_activity(event_type: str | None = None, level: str | None = None,
                  cust_nb: str | None = None, limit: int = 100,
                  offset: int = 0, s: Session = Depends(get_db),
                  salesman: Salesman = Depends(get_current_salesman)):
    q = select(ActivityLog).order_by(ActivityLog.ts.desc())
    if event_type:
        q = q.where(ActivityLog.event_type == event_type)
    if level:
        q = q.where(ActivityLog.level == level)
    if cust_nb:
        require_customer_ownership(cust_nb, salesman)
        q = q.where(ActivityLog.cust_nb == cust_nb)
    elif not salesman.is_admin:
        owned = [r.cust_nb for r in catalog_client.list_all_customers(
            salesman_id=salesman.login_id, admin=False)]
        q = q.where(ActivityLog.cust_nb.in_(owned))
    return list(s.scalars(q.offset(offset).limit(limit)))
