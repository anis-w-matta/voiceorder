from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.enums import RequestStatus
from app.schemas.transcript import TranscriptSegment


class QueueRow(BaseModel):
    id: int
    created_at: datetime
    phone_e164: str | None
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


class CustomerCandidateOut(BaseModel):
    cust_nb: str
    customer_name: str
    phone_e164: str | None = None
    score: float


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


class RequestDetail(BaseModel):
    id: int
    status: RequestStatus
    intents: list[str]
    primary_intent: str
    flags: list[str]
    cust_nb: str | None
    customer_name: str | None
    phone_e164: str | None
    transcript: str | None
    transcript_conf: float | None
    language: str | None
    languages: list[str] = []
    duration_sec: Decimal | None
    audio_url: str
    segments: list[TranscriptSegment] = []
    target_order_nb: str | None
    assigned_to: str | None
    lines: list[LineOut]


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
