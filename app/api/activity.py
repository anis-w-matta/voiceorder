from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models import ActivityLog
from app.schemas.api_out import ActivityLogOut

router = APIRouter(tags=["activity"])


@router.get("/activity", response_model=list[ActivityLogOut])
def list_activity(event_type: str | None = None, level: str | None = None,
                  cust_nb: str | None = None, limit: int = 100,
                  offset: int = 0, s: Session = Depends(get_db)):
    q = select(ActivityLog).order_by(ActivityLog.ts.desc())
    if event_type:
        q = q.where(ActivityLog.event_type == event_type)
    if level:
        q = q.where(ActivityLog.level == level)
    if cust_nb:
        q = q.where(ActivityLog.cust_nb == cust_nb)
    return list(s.scalars(q.offset(offset).limit(limit)))
