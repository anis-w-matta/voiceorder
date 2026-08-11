from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_audio, get_db, get_operator
from app.models import Customer, PendingRequest, VoiceMessage
from app.schemas.api_out import QueueRow, RequestDetail
from app.schemas.enums import RequestStatus

router = APIRouter(tags=["queue"])

DECIDED = {RequestStatus.committed.value, RequestStatus.rejected.value}


@router.get("/queue", response_model=list[QueueRow])
def list_queue(status: str | None = None, flag: str | None = Query(None),
               limit: int = 50, s: Session = Depends(get_db)):
    q = select(PendingRequest).order_by(PendingRequest.created_at.desc())
    if status:
        q = q.where(PendingRequest.status == status)
    if flag:
        # Filter in SQL (jsonb @> '["flag"]'). Doing this in Python after the
        # LIMIT meant the filter only ever saw the first `limit` rows, so a
        # matching request just outside that window vanished from the queue.
        q = q.where(PendingRequest.flags.contains([flag]))
    rows = []
    for r in s.scalars(q.limit(limit)):
        cust = s.get(Customer, r.cust_nb) if r.cust_nb else None
        rows.append(QueueRow(
            id=r.id, created_at=r.created_at, phone_e164=r.voice.phone_e164,
            customer_name=cust.customer_name if cust else None,
            cust_nb=r.cust_nb, primary_intent=r.primary_intent,
            line_count=len(r.lines), flags=r.flags or [], status=r.status,
            duration_sec=r.voice.duration_sec,
            languages=r.voice.languages or []))
    return rows


@router.get("/queue/{req_id}", response_model=RequestDetail)
def get_request(req_id: int, s: Session = Depends(get_db)):
    r = s.get(PendingRequest, req_id)
    if not r:
        raise HTTPException(404)
    cust = s.get(Customer, r.cust_nb) if r.cust_nb else None
    return RequestDetail(
        id=r.id, status=r.status, intents=r.intents or [],
        primary_intent=r.primary_intent, flags=r.flags or [], cust_nb=r.cust_nb,
        customer_name=cust.customer_name if cust else None,
        phone_e164=r.voice.phone_e164, transcript=r.voice.transcript,
        transcript_conf=r.voice.transcript_conf, language=r.voice.language,
        languages=r.voice.languages or [], duration_sec=r.voice.duration_sec,
        audio_url=f"/audio/{r.voice_message_id}",
        segments=r.voice.segments or [],
        target_order_nb=r.target_order_nb, assigned_to=r.assigned_to,
        lines=list(r.lines))


@router.post("/queue/{req_id}/claim")
def claim(req_id: int, s: Session = Depends(get_db),
          operator: str = Depends(get_operator)):
    r = s.execute(select(PendingRequest).where(PendingRequest.id == req_id)
                  .with_for_update()).scalar_one_or_none()
    if not r:
        raise HTTPException(404)
    if r.status in DECIDED:
        raise HTTPException(409, f"already decided ({r.status})")
    if r.assigned_to and r.assigned_to != operator:
        raise HTTPException(409, f"claimed by {r.assigned_to}")
    r.assigned_to = operator
    r.claimed_at = datetime.now(timezone.utc)
    r.status = RequestStatus.in_review.value
    return {"ok": True}


@router.get("/audio/{voice_id}")
def get_audio_file(voice_id: int, s: Session = Depends(get_db),
                   store=Depends(get_audio)):
    vm = s.get(VoiceMessage, voice_id)
    if not vm:
        raise HTTPException(404)
    path = Path(store.absolute(vm.audio_path))
    # The DB row can outlive the file (archived, pruned, or never written
    # because ingest failed part-way); FileResponse on a missing path raises
    # and surfaces as a 500.
    if not path.is_file():
        raise HTTPException(404, "audio file missing")
    return FileResponse(path)
