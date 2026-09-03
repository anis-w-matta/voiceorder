"""Read-only aggregation queries for VeNdO Intelligence (see
vendo-intelligence-web/docs/audit and the Phase 3 plan) - the first
GROUP BY/COUNT aggregate queries in this codebase. Every function here is
a plain read against this service's own tables; nothing here writes
anything or is reachable from the operational (claim/accept/reject/
commit) code path, and none of it ever computes or returns a
price/revenue/amount field.

Turnaround/AI-quality scope note: since Phase 2 (see
app/services/commit.py._finalize_committed), a committed request's row is
kept (status="committed"), not deleted - but only for requests committed
*after* that change shipped. Anything committed before then has no
PendingRequest/PendingLine row left at all (already deleted), so these
queries will under-report completeness for a while; every response below
carries an explicit completeness count rather than pretending the
available rows are the whole population.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session

from app.models import PendingLine, PendingRequest
from app.schemas.enums import RequestStatus

OPEN_STATUSES = (RequestStatus.new.value, RequestStatus.in_review.value,
                RequestStatus.callback.value)
DECIDED_STATUSES = (RequestStatus.rejected.value, RequestStatus.committed.value)


@dataclass
class RequestsFilter:
    date_from: datetime | None = None
    date_to: datetime | None = None
    salesman_id: str | None = None  # PendingRequest.assigned_to
    cust_nb: str | None = None
    status: str | None = None
    intent: str | None = None


def _apply_filters(q, f: RequestsFilter):
    """Applies f's WHERE conditions to any select() targeting
    PendingRequest (whatever its SELECT column list is) - deliberately
    NOT built as select(PendingRequest).subquery() plus a separate
    outer query, since referencing PendingRequest's own class columns
    against a derived subquery silently produces a cartesian product
    instead of an error (SQLAlchemy only warns). Every function below
    that needs PendingRequest-level filters applies them directly to
    its own target SELECT via this helper instead.
    """
    if f.date_from is not None:
        q = q.where(PendingRequest.created_at >= f.date_from)
    if f.date_to is not None:
        q = q.where(PendingRequest.created_at <= f.date_to)
    if f.salesman_id is not None:
        q = q.where(PendingRequest.assigned_to == f.salesman_id)
    if f.cust_nb is not None:
        q = q.where(PendingRequest.cust_nb == f.cust_nb)
    if f.status is not None:
        q = q.where(PendingRequest.status == f.status)
    if f.intent is not None:
        q = q.where(PendingRequest.primary_intent == f.intent)
    return q


def _base_query(f: RequestsFilter):
    return _apply_filters(select(PendingRequest), f)


def _previous_period(f: RequestsFilter) -> RequestsFilter | None:
    """The immediately preceding period of the same length, for a simple
    "vs. previous period" comparison - only meaningful when both bounds of
    the current period are given (an open-ended range has no fixed length
    to mirror)."""
    if f.date_from is None or f.date_to is None:
        return None
    length = f.date_to - f.date_from
    return RequestsFilter(date_from=f.date_from - length, date_to=f.date_from,
                          salesman_id=f.salesman_id, cust_nb=f.cust_nb,
                          status=f.status, intent=f.intent)


@dataclass
class StatusCount:
    status: str
    count: int


@dataclass
class BacklogSummary:
    total: int
    oldest_created_at: datetime | None
    age_buckets: dict[str, int]  # "<5m" / "5-10m" / "10-30m" / "30-60m" / "60m+"


def _age_bucket(age: timedelta) -> str:
    """Phase 10 (Operations Command Center): a request backlog is
    operationally urgent on the scale of minutes, not days - so these
    buckets are minute-granularity, not the day-granularity buckets an
    earlier phase used. Changing this is a deliberate Phase 10 fix, not a
    reinterpretation of backlog semantics (still: age = now - created_at,
    still scoped to OPEN_STATUSES in backlog_summary below)."""
    minutes = age.total_seconds() / 60
    if minutes < 5:
        return "<5m"
    if minutes < 10:
        return "5-10m"
    if minutes < 30:
        return "10-30m"
    if minutes < 60:
        return "30-60m"
    return "60m+"


def backlog_summary(session: Session, f: RequestsFilter) -> BacklogSummary:
    q = _base_query(f).where(PendingRequest.status.in_(OPEN_STATUSES))
    rows = session.scalars(q).all()
    now = datetime.now(timezone.utc)
    buckets = {"<5m": 0, "5-10m": 0, "10-30m": 0, "30-60m": 0, "60m+": 0}
    oldest = None
    for r in rows:
        buckets[_age_bucket(now - r.created_at)] += 1
        if oldest is None or r.created_at < oldest:
            oldest = r.created_at
    return BacklogSummary(total=len(rows), oldest_created_at=oldest,
                          age_buckets=buckets)


@dataclass
class TurnaroundSummary:
    sample_size: int
    median_seconds: float | None
    avg_seconds: float | None
    p75_seconds: float | None
    p90_seconds: float | None
    p95_seconds: float | None


def turnaround_summary(session: Session, f: RequestsFilter) -> TurnaroundSummary:
    """decided_at - created_at over rejected/committed requests (the
    "decided" set - callback is not final). For a committed request,
    decided_at is set when the commit attempt *started*, not when it
    finished - a documented, deliberate ambiguity (see Phase 2 plan), not
    fixed here."""
    seconds = func.extract(
        "epoch", PendingRequest.decided_at - PendingRequest.created_at)
    q = _apply_filters(
        select(
            func.count(),
            func.percentile_cont(0.5).within_group(seconds),
            func.avg(seconds),
            func.percentile_cont(0.75).within_group(seconds),
            func.percentile_cont(0.9).within_group(seconds),
            func.percentile_cont(0.95).within_group(seconds),
        ).select_from(PendingRequest), f)
    q = q.where(PendingRequest.status.in_(DECIDED_STATUSES),
               PendingRequest.decided_at.is_not(None))
    row = session.execute(q).one()
    n = row[0] or 0
    if n == 0:
        return TurnaroundSummary(sample_size=0, median_seconds=None,
                                 avg_seconds=None, p75_seconds=None,
                                 p90_seconds=None, p95_seconds=None)
    return TurnaroundSummary(
        sample_size=n, median_seconds=float(row[1]), avg_seconds=float(row[2]),
        p75_seconds=float(row[3]), p90_seconds=float(row[4]),
        p95_seconds=float(row[5]))


@dataclass
class RejectionSummary:
    sample_size: int  # rejected + committed (the decided population)
    rejection_rate: float | None
    previous_period_rejection_rate: float | None


def _rejection_rate(session: Session, f: RequestsFilter) -> tuple[int, float | None]:
    q = _base_query(f).where(PendingRequest.status.in_(DECIDED_STATUSES))
    rejected = session.scalar(
        select(func.count()).select_from(q.where(
            PendingRequest.status == RequestStatus.rejected.value).subquery())) or 0
    total = session.scalar(select(func.count()).select_from(q.subquery())) or 0
    rate = (rejected / total) if total else None
    return total, rate


def rejection_summary(session: Session, f: RequestsFilter) -> RejectionSummary:
    total, rate = _rejection_rate(session, f)
    prev_f = _previous_period(f)
    prev_rate = _rejection_rate(session, prev_f)[1] if prev_f else None
    return RejectionSummary(sample_size=total, rejection_rate=rate,
                            previous_period_rejection_rate=prev_rate)


def status_counts(session: Session, f: RequestsFilter) -> list[StatusCount]:
    q = _apply_filters(
        select(PendingRequest.status, func.count())
        .group_by(PendingRequest.status), f)
    rows = session.execute(q).all()
    return [StatusCount(status=r[0], count=r[1]) for r in rows]


@dataclass
class VolumePoint:
    day: datetime
    status: str
    count: int


def volume_over_time(session: Session, f: RequestsFilter) -> list[VolumePoint]:
    """Day boundaries in UTC, explicitly - same reasoning as
    activity_by_hour/activity_volume_over_time's timezone notes below;
    date_trunc('day', a timestamptz) would otherwise shift day boundaries
    by this deployment's Postgres session offset (Europe/Chisinau) instead
    of UTC midnight."""
    day = func.date_trunc("day", func.timezone("UTC", PendingRequest.created_at))
    q = _apply_filters(
        select(day, PendingRequest.status, func.count())
        .group_by(day, PendingRequest.status)
        .order_by(day), f)
    rows = session.execute(q).all()
    return [VolumePoint(day=r[0], status=r[1], count=r[2]) for r in rows]


@dataclass
class RequestsSummary:
    status_counts: list[StatusCount]
    backlog: BacklogSummary
    turnaround: TurnaroundSummary
    rejection: RejectionSummary
    volume_over_time: list[VolumePoint]


def requests_summary(session: Session, f: RequestsFilter) -> RequestsSummary:
    return RequestsSummary(
        status_counts=status_counts(session, f),
        backlog=backlog_summary(session, f),
        turnaround=turnaround_summary(session, f),
        rejection=rejection_summary(session, f),
        volume_over_time=volume_over_time(session, f))


# ---- AI quality -------------------------------------------------------
#
# Phase 11 data-honesty note, load-bearing for everything below: PendingLine
# stores only the FINAL (possibly human-edited) item_nb/qty/uom, plus a
# single `operator_edited` boolean - there is no stored original AI
# prediction distinct from the final value anywhere in this schema
# (`raw_model_output` on PendingRequest is request-level parse metadata,
# e.g. {"scripted": True, "command_type": "place_order"} - never a per-line
# predicted-vs-final snapshot; confirmed by reading every PendingRequest
# construction site in app/pipeline.py and app/services/draft_builder.py).
# So "AI prediction -> human edit -> final value" and a correction
# TAXONOMY (item mismatch vs. quantity vs. UOM vs. intent) are NOT
# reconstructable from current data - only "was this line edited at all"
# is known. Do not fabricate a taxonomy by guessing; the BFF/frontend must
# expose this as an explicit, honest gap (completeness UNAVAILABLE with a
# clear note), not silently omit it. What IS real and built below:
# confidence buckets, overall/bucketed correction rate (binary edited/not),
# hotspots by item and by intent, and a correction-rate trend over time.

CONFIDENCE_BUCKETS = [(0.0, 0.6, "under_60"), (0.6, 0.8, "60_80"),
                      (0.8, 0.9, "80_90"), (0.9, 1.01, "90_plus")]


@dataclass
class ConfidenceBucketStat:
    bucket: str
    sample_size: int
    correction_rate: float | None


@dataclass
class AiQualitySummary:
    reviewed_lines: int
    edited_lines: int
    overall_correction_rate: float | None
    low_confidence_count: int  # confidence < 0.6, matching CONFIDENCE_BUCKETS' low/medium split point loosely
    by_confidence_bucket: list[ConfidenceBucketStat]


def _lines_query(f: RequestsFilter):
    """PendingLine rows for requests matching f - a join, not
    _base_query() itself, since the aggregation target here is lines, not
    requests."""
    q = select(PendingLine).join(
        PendingRequest, PendingRequest.id == PendingLine.request_id)
    if f.date_from is not None:
        q = q.where(PendingRequest.created_at >= f.date_from)
    if f.date_to is not None:
        q = q.where(PendingRequest.created_at <= f.date_to)
    if f.salesman_id is not None:
        q = q.where(PendingRequest.assigned_to == f.salesman_id)
    if f.cust_nb is not None:
        q = q.where(PendingRequest.cust_nb == f.cust_nb)
    if f.status is not None:
        q = q.where(PendingRequest.status == f.status)
    if f.intent is not None:
        q = q.where(PendingRequest.primary_intent == f.intent)
    return q


def ai_quality_summary(session: Session, f: RequestsFilter) -> AiQualitySummary:
    """Scoped to PendingLine rows that still exist - see module docstring
    on why this under-represents requests committed before Phase 2."""
    sub = _lines_query(f).subquery()

    total = session.scalar(select(func.count()).select_from(sub)) or 0
    edited = session.scalar(
        select(func.count()).select_from(sub)
        .where(sub.c.operator_edited.is_(True))) or 0
    low_conf = session.scalar(
        select(func.count()).select_from(sub)
        .where(sub.c.match_confidence.is_not(None),
              sub.c.match_confidence < 0.6)) or 0

    buckets = []
    for lo, hi, name in CONFIDENCE_BUCKETS:
        in_bucket = sub.c.match_confidence.is_not(None) & \
            (sub.c.match_confidence >= lo) & (sub.c.match_confidence < hi)
        n = session.scalar(
            select(func.count()).select_from(sub).where(in_bucket)) or 0
        edited_n = session.scalar(
            select(func.count()).select_from(sub)
            .where(in_bucket, sub.c.operator_edited.is_(True))) or 0
        buckets.append(ConfidenceBucketStat(
            bucket=name, sample_size=n,
            correction_rate=(edited_n / n) if n else None))

    return AiQualitySummary(
        reviewed_lines=total, edited_lines=edited,
        overall_correction_rate=(edited / total) if total else None,
        low_confidence_count=low_conf, by_confidence_bucket=buckets)


@dataclass
class ItemQualityStat:
    item_nb: str
    sample_size: int
    correction_rate: float | None


def ai_quality_by_item(session: Session, f: RequestsFilter,
                       min_sample_size: int = 3) -> list[ItemQualityStat]:
    """Correction-rate hotspots by item - only items with at least
    min_sample_size reviewed lines are included, so a single edited line
    on a rarely-ordered item doesn't look like a 100% hotspot."""
    sub = _lines_query(f).subquery()
    rows = session.execute(
        select(sub.c.item_nb, func.count(),
              func.sum(func.cast(sub.c.operator_edited, Integer)))
        .select_from(sub)
        .where(sub.c.item_nb.is_not(None))
        .group_by(sub.c.item_nb)
        .having(func.count() >= min_sample_size)
        .order_by(func.count().desc())
    ).all()
    return [ItemQualityStat(item_nb=r[0], sample_size=r[1],
                            correction_rate=(r[2] or 0) / r[1])
           for r in rows]


@dataclass
class IntentQualityStat:
    intent: str
    sample_size: int
    correction_rate: float | None


def ai_quality_by_intent(session: Session, f: RequestsFilter) -> list[IntentQualityStat]:
    """Correction-rate hotspots by request intent (add_order, return_order,
    etc.) - request-level intent joined onto each of its lines."""
    q = (
        select(PendingRequest.primary_intent, func.count(),
              func.sum(func.cast(PendingLine.operator_edited, Integer)))
        .select_from(PendingLine)
        .join(PendingRequest, PendingRequest.id == PendingLine.request_id)
        .group_by(PendingRequest.primary_intent)
    )
    if f.date_from is not None:
        q = q.where(PendingRequest.created_at >= f.date_from)
    if f.date_to is not None:
        q = q.where(PendingRequest.created_at <= f.date_to)
    if f.salesman_id is not None:
        q = q.where(PendingRequest.assigned_to == f.salesman_id)
    if f.cust_nb is not None:
        q = q.where(PendingRequest.cust_nb == f.cust_nb)
    rows = session.execute(q).all()
    return [IntentQualityStat(intent=r[0], sample_size=r[1],
                              correction_rate=(r[2] or 0) / r[1] if r[1] else None)
           for r in rows]


@dataclass
class QualityTrendPoint:
    bucket: str  # "YYYY-MM"
    sample_size: int
    correction_rate: float | None


def ai_quality_trend(session: Session, f: RequestsFilter) -> list[QualityTrendPoint]:
    """Monthly correction-rate trend, bucketed by the request's created_at
    (when it entered the queue - a line's own "when was it AI-classified"
    moment, not a business/order date). Month boundaries computed in UTC,
    explicitly - same reasoning as volume_over_time's timezone note above;
    date_trunc('month', a timestamptz) would otherwise shift month
    boundaries (only visible near a month's start/end) by this
    deployment's Postgres session offset (Europe/Chisinau) instead of
    UTC."""
    month = func.to_char(
        func.date_trunc("month", func.timezone("UTC", PendingRequest.created_at)),
        "YYYY-MM")
    q = (
        select(month, func.count(),
              func.sum(func.cast(PendingLine.operator_edited, Integer)))
        .select_from(PendingLine)
        .join(PendingRequest, PendingRequest.id == PendingLine.request_id)
        .group_by(month)
        .order_by(month)
    )
    if f.date_from is not None:
        q = q.where(PendingRequest.created_at >= f.date_from)
    if f.date_to is not None:
        q = q.where(PendingRequest.created_at <= f.date_to)
    if f.salesman_id is not None:
        q = q.where(PendingRequest.assigned_to == f.salesman_id)
    if f.cust_nb is not None:
        q = q.where(PendingRequest.cust_nb == f.cust_nb)
    rows = session.execute(q).all()
    return [QualityTrendPoint(bucket=r[0], sample_size=r[1],
                              correction_rate=(r[2] or 0) / r[1] if r[1] else None)
           for r in rows]


# ---- activity log (Phase 10 Operations) ----------------------------------
# Deliberately a NEW admin-gated aggregate endpoint (see
# app/api/analytics.py, require_admin), not the existing raw GET /activity
# - that route has no per-user auth dependency at all (see
# vendo-intelligence-web/docs/audit/04_auth_map.md and /08), so Phase 10
# must not read from it. These queries return only counts/aggregates,
# never raw log rows (message/details/cust_nb-per-row), so there is no
# lineage back through this endpoint to the same exposure.

@dataclass
class ActivityFilter:
    date_from: datetime | None = None
    date_to: datetime | None = None
    cust_nb: str | None = None


def _activity_query(f: ActivityFilter):
    from app.models import ActivityLog
    q = select(ActivityLog)
    if f.date_from is not None:
        q = q.where(ActivityLog.ts >= f.date_from)
    if f.date_to is not None:
        q = q.where(ActivityLog.ts <= f.date_to)
    if f.cust_nb is not None:
        q = q.where(ActivityLog.cust_nb == f.cust_nb)
    return q


@dataclass
class HourCount:
    hour: int  # 0-23, UTC
    count: int


def activity_by_hour(session: Session, f: ActivityFilter) -> list[HourCount]:
    """Hour-of-day in UTC, explicitly - EXTRACT(HOUR FROM a timestamptz)
    otherwise silently converts to the DB session's timezone first (this
    deployment's Postgres session defaults to Europe/Chisinau, not UTC),
    which would make "hour 9" mean a different real hour depending on
    server/session config. Not business-local time (BUSINESS_TIMEZONE is
    Asia/Beirut) - a documented, deliberate choice: this is an operational
    "when do events happen" view, not a business-hours calculation, and
    UTC is unambiguous across deployments."""
    from app.models import ActivityLog
    hour = func.extract("hour", func.timezone("UTC", ActivityLog.ts))
    q = _activity_query(f).with_only_columns(hour, func.count()).group_by(hour).order_by(hour)
    rows = session.execute(q).all()
    counts = {h: 0 for h in range(24)}
    for h, c in rows:
        counts[int(h)] = c
    return [HourCount(hour=h, count=counts[h]) for h in range(24)]


@dataclass
class EventTypeCount:
    event_type: str
    count: int


def activity_by_event_type(session: Session, f: ActivityFilter) -> list[EventTypeCount]:
    from app.models import ActivityLog
    q = (_activity_query(f)
        .with_only_columns(ActivityLog.event_type, func.count())
        .group_by(ActivityLog.event_type).order_by(func.count().desc()))
    rows = session.execute(q).all()
    return [EventTypeCount(event_type=r[0], count=r[1]) for r in rows]


@dataclass
class ActivityVolumePoint:
    day: datetime
    count: int


def activity_volume_over_time(session: Session, f: ActivityFilter) -> list[ActivityVolumePoint]:
    """Day boundaries in UTC, explicitly - same reasoning as
    activity_by_hour's timezone note above; date_trunc('day', a
    timestamptz) would otherwise shift day boundaries by this deployment's
    Postgres session offset (Europe/Chisinau) instead of UTC midnight."""
    from app.models import ActivityLog
    day = func.date_trunc("day", func.timezone("UTC", ActivityLog.ts))
    q = (_activity_query(f)
        .with_only_columns(day, func.count())
        .group_by(day).order_by(day))
    rows = session.execute(q).all()
    return [ActivityVolumePoint(day=r[0], count=r[1]) for r in rows]


@dataclass
class ActivitySummary:
    by_hour: list[HourCount]
    by_event_type: list[EventTypeCount]
    volume_over_time: list[ActivityVolumePoint]


def activity_summary(session: Session, f: ActivityFilter) -> ActivitySummary:
    return ActivitySummary(
        by_hour=activity_by_hour(session, f),
        by_event_type=activity_by_event_type(session, f),
        volume_over_time=activity_volume_over_time(session, f))


# ---- per-salesman -------------------------------------------------------

@dataclass
class SalesmanRequestMetrics:
    salesman_id: str
    request_count: int
    rejection_rate: float | None
    median_turnaround_seconds: float | None
    ai_correction_rate: float | None


def salesmen_request_metrics(session: Session,
                             f: RequestsFilter) -> list[SalesmanRequestMetrics]:
    q = _apply_filters(
        select(PendingRequest.assigned_to).distinct(), f
    ).where(PendingRequest.assigned_to.is_not(None))
    salesmen = session.scalars(q).all()
    out = []
    for sm in salesmen:
        sm_f = RequestsFilter(date_from=f.date_from, date_to=f.date_to,
                              salesman_id=sm, cust_nb=f.cust_nb,
                              status=f.status, intent=f.intent)
        count = session.scalar(
            select(func.count()).select_from(_base_query(sm_f).subquery())) or 0
        _, rate = _rejection_rate(session, sm_f)
        turnaround = turnaround_summary(session, sm_f)
        ai = ai_quality_summary(session, sm_f)
        out.append(SalesmanRequestMetrics(
            salesman_id=sm, request_count=count, rejection_rate=rate,
            median_turnaround_seconds=turnaround.median_seconds,
            ai_correction_rate=ai.overall_correction_rate))
    return out
