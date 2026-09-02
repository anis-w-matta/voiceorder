from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.enums import RequestStatus
from app.schemas.transcript import TranscriptSegment


class QueueRow(BaseModel):
    id: int
    created_at: datetime
    customer_name: str | None
    cust_nb: str | None
    primary_intent: str
    line_count: int
    flags: list[str]
    status: RequestStatus
    duration_sec: Decimal | None
    languages: list[str] = []


class CandidateOut(BaseModel):
    item_nb: str
    item_desc: str
    category: str
    score: float
    # Without this the reviewer sees a score but not how it was reached, so
    # a loose substring guess is indistinguishable from an exact code match.
    method: str | None = None
    attribute_conflict: bool = False
    conflict_reason: str | None = None


class CustomerCandidateOut(BaseModel):
    cust_nb: str
    customer_name: str
    score: float


class CustomerCacheOut(BaseModel):
    cust_nb: str
    customer_name: str


class ItemCacheOut(BaseModel):
    item_nb: str
    item_desc: str
    category: str


class QraDetailCacheOut(BaseModel):
    # item_nb_buy/item_nb_get/qty_get are null for a type P row;
    # item_nb_price is null for type T/B - see app/models/qra.py's
    # QraDetail docstring.
    item_nb_buy: str | None = None
    item_nb_get: str | None = None
    item_nb_price: str | None = None
    qty_buy: Decimal
    qty_get: Decimal | None = None
    qra_type: str
    qra_price: Decimal | None = None


class QraHeaderCacheOut(BaseModel):
    cust_nb: str
    from_date: date
    to_date: date
    status: str
    details: list[QraDetailCacheOut]


class RecentOrderLineOut(BaseModel):
    item_nb: str | None
    item_desc: str
    qty: Decimal
    uom: str | None
    is_free: bool = False


class RecentOrderOut(BaseModel):
    order_nb: str
    order_type: str
    cust_nb: str
    customer_name: str | None
    lines: list[RecentOrderLineOut]


class LineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    line_nb: int
    raw_text: str
    raw_lang: str | None
    item_nb: str | None
    item_desc: str | None
    qty: Decimal | None
    uom: str | None
    match_confidence: float | None
    match_method: str | None
    change: str | None = None
    category: str | None
    candidates: list[CandidateOut] = []
    line_flags: list[str] = []
    resolution_meta: dict = {}
    attributes: dict = {}
    qualifiers: dict = {}
    # QRA preview (app/services/qra_engine.py's preview_qra) - what this
    # line's price/item WOULD become at commit time under the customer's
    # active QRA agreement, if any. Never applied to item_nb/item_desc
    # above - those still show what the salesman actually said/ordered.
    qra_unit_price: Decimal | None = None
    qra_is_free: bool = False
    qra_substituted_item_nb: str | None = None
    qra_substituted_item_desc: str | None = None


class QraBonusLineOut(BaseModel):
    """A free bonus line QRA would add at commit time - has no
    corresponding PendingLine yet, so it can't live on LineOut."""
    item_nb: str
    item_desc: str
    qty: Decimal
    uom: str | None


class RequestDetail(BaseModel):
    id: int
    status: RequestStatus
    intents: list[str]
    primary_intent: str
    flags: list[str]
    cust_nb: str | None
    customer_name: str | None
    transcript: str | None
    transcript_conf: float | None
    language: str | None
    languages: list[str] = []
    duration_sec: Decimal | None
    audio_url: str
    segments: list[TranscriptSegment] = []
    target_order_nb: str | None
    assigned_to: str | None
    committed_order_nb: str | None = None
    lines: list[LineOut]
    qra_bonus_lines: list[QraBonusLineOut] = []


# ---- analytics (VeNdO Intelligence, Phase 3) ------------------------------
# No financial fields anywhere below - only request/backlog/turnaround/
# rejection/AI-quality counts.

class StatusCountOut(BaseModel):
    status: RequestStatus
    count: int


class BacklogSummaryOut(BaseModel):
    total: int
    oldest_created_at: datetime | None = None
    age_buckets: dict[str, int]


class TurnaroundSummaryOut(BaseModel):
    sample_size: int
    median_seconds: float | None = None
    avg_seconds: float | None = None
    p75_seconds: float | None = None
    p90_seconds: float | None = None
    p95_seconds: float | None = None


class RejectionSummaryOut(BaseModel):
    sample_size: int
    rejection_rate: float | None = None
    previous_period_rejection_rate: float | None = None


class VolumePointOut(BaseModel):
    day: datetime
    status: RequestStatus
    count: int


class RequestsSummaryOut(BaseModel):
    status_counts: list[StatusCountOut]
    backlog: BacklogSummaryOut
    turnaround: TurnaroundSummaryOut
    rejection: RejectionSummaryOut
    volume_over_time: list[VolumePointOut]


class ConfidenceBucketStatOut(BaseModel):
    bucket: str
    sample_size: int
    correction_rate: float | None = None


class AiQualitySummaryOut(BaseModel):
    reviewed_lines: int
    edited_lines: int
    overall_correction_rate: float | None = None
    low_confidence_count: int
    by_confidence_bucket: list[ConfidenceBucketStatOut]


# Phase 11: hotspots + trend. No correction TAXONOMY (item/qty/UOM/intent
# breakdown of *what* changed) or "prediction -> edit -> final value" view -
# both need a stored original-AI-prediction distinct from PendingLine's
# final value, which does not exist anywhere in this schema. See the
# module docstring in app/services/analytics.py for the full explanation -
# the frontend must render this as an explicit UNAVAILABLE gap, not omit it.

class ItemQualityStatOut(BaseModel):
    item_nb: str
    sample_size: int
    correction_rate: float | None = None


class IntentQualityStatOut(BaseModel):
    intent: str
    sample_size: int
    correction_rate: float | None = None


class QualityTrendPointOut(BaseModel):
    bucket: str
    sample_size: int
    correction_rate: float | None = None


class SalesmanRequestMetricsOut(BaseModel):
    salesman_id: str
    request_count: int
    rejection_rate: float | None = None
    median_turnaround_seconds: float | None = None
    ai_correction_rate: float | None = None


# ---- activity log analytics (Phase 10) - aggregates only, never raw rows,
# and admin-gated (see app/api/analytics.py) unlike the existing raw
# GET /activity, which has no per-user auth at all.

class HourCountOut(BaseModel):
    hour: int
    count: int


class EventTypeCountOut(BaseModel):
    event_type: str
    count: int


class ActivityVolumePointOut(BaseModel):
    day: datetime
    count: int


class ActivitySummaryOut(BaseModel):
    by_hour: list[HourCountOut]
    by_event_type: list[EventTypeCountOut]
    volume_over_time: list[ActivityVolumePointOut]


class ActivityLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ts: datetime
    event_type: str
    level: str
    voice_message_id: int | None
    request_id: int | None
    cust_nb: str | None
    order_nb: str | None
    message: str
    details: dict
