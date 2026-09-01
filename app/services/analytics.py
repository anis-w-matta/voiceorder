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

from sqlalchemy import func, select
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
    age_buckets: dict[str, int]  # "0-1d" / "1-3d" / "3-7d" / "7d+"


def _age_bucket(age: timedelta) -> str:
    days = age.total_seconds() / 86400
    if days < 1:
        return "0-1d"
    if days < 3:
        return "1-3d"
    if days < 7:
        return "3-7d"
    return "7d+"


def backlog_summary(session: Session, f: RequestsFilter) -> BacklogSummary:
    q = _base_query(f).where(PendingRequest.status.in_(OPEN_STATUSES))
    rows = session.scalars(q).all()
    now = datetime.now(timezone.utc)
    buckets = {"0-1d": 0, "1-3d": 0, "3-7d": 0, "7d+": 0}
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
    day = func.date_trunc("day", PendingRequest.created_at)
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

CONFIDENCE_BUCKETS = [(0.0, 0.5, "low"), (0.5, 0.8, "medium"),
                      (0.8, 0.95, "high"), (0.95, 1.01, "very_high")]


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
