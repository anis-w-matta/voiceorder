from datetime import datetime

from fastapi import APIRouter, Depends

from app.api.deps import get_db, require_admin
from app.models import Salesman
from app.schemas.api_out import (ActivitySummaryOut, AiQualitySummaryOut,
                                 BacklogSummaryOut, ConfidenceBucketStatOut,
                                 EventTypeCountOut, HourCountOut,
                                 RejectionSummaryOut, RequestsSummaryOut,
                                 SalesmanRequestMetricsOut, StatusCountOut,
                                 TurnaroundSummaryOut, VolumePointOut,
                                 ActivityVolumePointOut)
from app.services import analytics

router = APIRouter(prefix="/admin/analytics", tags=["analytics"])


def _filter(date_from: datetime | None, date_to: datetime | None,
           salesman_id: str | None, cust_nb: str | None, status: str | None,
           intent: str | None) -> analytics.RequestsFilter:
    return analytics.RequestsFilter(
        date_from=date_from, date_to=date_to, salesman_id=salesman_id,
        cust_nb=cust_nb, status=status, intent=intent)


@router.get("/requests-summary", response_model=RequestsSummaryOut)
def requests_summary(date_from: datetime | None = None,
                     date_to: datetime | None = None,
                     salesman_id: str | None = None, cust_nb: str | None = None,
                     status: str | None = None, intent: str | None = None,
                     s=Depends(get_db), _admin: Salesman = Depends(require_admin)):
    r = analytics.requests_summary(
        s, _filter(date_from, date_to, salesman_id, cust_nb, status, intent))
    return RequestsSummaryOut(
        status_counts=[StatusCountOut(**vars(x)) for x in r.status_counts],
        backlog=BacklogSummaryOut(**vars(r.backlog)),
        turnaround=TurnaroundSummaryOut(**vars(r.turnaround)),
        rejection=RejectionSummaryOut(**vars(r.rejection)),
        volume_over_time=[VolumePointOut(**vars(x)) for x in r.volume_over_time])


@router.get("/ai-quality-summary", response_model=AiQualitySummaryOut)
def ai_quality_summary(date_from: datetime | None = None,
                       date_to: datetime | None = None,
                       salesman_id: str | None = None,
                       cust_nb: str | None = None, status: str | None = None,
                       intent: str | None = None, s=Depends(get_db),
                       _admin: Salesman = Depends(require_admin)):
    r = analytics.ai_quality_summary(
        s, _filter(date_from, date_to, salesman_id, cust_nb, status, intent))
    return AiQualitySummaryOut(
        reviewed_lines=r.reviewed_lines, edited_lines=r.edited_lines,
        overall_correction_rate=r.overall_correction_rate,
        low_confidence_count=r.low_confidence_count,
        by_confidence_bucket=[ConfidenceBucketStatOut(**vars(x))
                              for x in r.by_confidence_bucket])


@router.get("/salesmen-request-metrics",
           response_model=list[SalesmanRequestMetricsOut])
def salesmen_request_metrics(date_from: datetime | None = None,
                             date_to: datetime | None = None,
                             cust_nb: str | None = None,
                             status: str | None = None,
                             intent: str | None = None, s=Depends(get_db),
                             _admin: Salesman = Depends(require_admin)):
    r = analytics.salesmen_request_metrics(
        s, _filter(date_from, date_to, None, cust_nb, status, intent))
    return [SalesmanRequestMetricsOut(**vars(x)) for x in r]


@router.get("/activity-summary", response_model=ActivitySummaryOut)
def activity_summary(date_from: datetime | None = None,
                     date_to: datetime | None = None,
                     cust_nb: str | None = None, s=Depends(get_db),
                     _admin: Salesman = Depends(require_admin)):
    r = analytics.activity_summary(
        s, analytics.ActivityFilter(date_from=date_from, date_to=date_to, cust_nb=cust_nb))
    return ActivitySummaryOut(
        by_hour=[HourCountOut(**vars(x)) for x in r.by_hour],
        by_event_type=[EventTypeCountOut(**vars(x)) for x in r.by_event_type],
        volume_over_time=[ActivityVolumePointOut(**vars(x)) for x in r.volume_over_time])
