from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_audio, get_current_salesman, get_db
from app.models import PendingRequest, Salesman, VoiceMessage
from app.schemas.api_out import (LineOut, QraBonusLineOut, QueueRow,
                                 RequestDetail)
from app.schemas.enums import Intent, RequestStatus
from app.services import catalog_client
from app.services.authorization import require_customer_ownership
from app.services.qra_engine import preview_qra

router = APIRouter(tags=["queue"])

DECIDED = {RequestStatus.committed.value, RequestStatus.rejected.value,
          RequestStatus.committing.value}


@router.get("/queue", response_model=list[QueueRow])
def list_queue(status: str | None = None, flag: str | None = Query(None),
               limit: int = 50, s: Session = Depends(get_db),
               salesman: Salesman = Depends(get_current_salesman)):
    q = select(PendingRequest).order_by(PendingRequest.created_at.desc())
    if status:
        q = q.where(PendingRequest.status == status)
    else:
        # A "queue" is pending work by default - once a request is decided
        # (rejected/committing/committed) it shouldn't keep showing up here
        # or as the Request screen's "most recent" fallback. ?status=rejected
        # and ?status=committed are still real history views: a committed
        # request's row is kept (status="committed", committed_order_nb set)
        # rather than deleted, so its AI/edit history and its link to the
        # resulting order both survive - see
        # OrderCommitService._finalize_committed. GET /orders/recent remains
        # the history view sourced from catalog-service's own order data.
        q = q.where(PendingRequest.status.notin_(DECIDED))
    if flag:
        # Filter in SQL (jsonb @> '["flag"]'). Doing this in Python after the
        # LIMIT meant the filter only ever saw the first `limit` rows, so a
        # matching request just outside that window vanished from the queue.
        q = q.where(PendingRequest.flags.contains([flag]))
    if not salesman.is_admin:
        # A plain salesman only ever sees requests for their own customers
        # (or ones not yet resolved to any customer - nothing to protect
        # there yet). Filtered here in SQL rather than in Python after the
        # LIMIT, same reasoning as the flag filter above: a request for
        # another salesman's customer just outside the LIMIT window must
        # not silently look like "no more results" once excluded.
        owned = {row.cust_nb for row in catalog_client.list_all_customers(
            salesman_id=salesman.login_id)}
        q = q.where(or_(PendingRequest.cust_nb.is_(None),
                        PendingRequest.cust_nb.in_(owned)))
    requests = list(s.scalars(q.limit(limit)))
    names = catalog_client.get_customers_batch(
        [r.cust_nb for r in requests if r.cust_nb])
    rows = []
    for r in requests:
        rows.append(QueueRow(
            id=r.id, created_at=r.created_at,
            customer_name=names.get(r.cust_nb) if r.cust_nb else None,
            cust_nb=r.cust_nb, primary_intent=r.primary_intent,
            line_count=len(r.lines), flags=r.flags or [], status=r.status,
            duration_sec=r.voice.duration_sec,
            languages=r.voice.languages or []))
    return rows


@router.get("/queue/{req_id}", response_model=RequestDetail)
def get_request(req_id: int, s: Session = Depends(get_db),
                salesman: Salesman = Depends(get_current_salesman)):
    r = s.get(PendingRequest, req_id)
    if not r:
        raise HTTPException(404)
    # Direct-ID access must not leak another salesman's customer/request
    # just because the caller guessed or enumerated an id (spec: "GET
    # /customers/7 must NOT return Customer 7 to Salesman A").
    require_customer_ownership(r.cust_nb, salesman)
    cust = catalog_client.get_customer(r.cust_nb) if r.cust_nb else None

    # QRA only actually applies inside OrderCommitService.commit() - this
    # is a read-only preview of what WOULD happen, so a reviewer can see
    # the substitution/bonus/price-override before deciding to Accept,
    # since there's no screen yet that shows a committed order's lines.
    # Skipped once the request is decided: a committed request's lines
    # already include the real, persisted QRA effect (apply_qra ran for
    # real at commit time) - previewing again on top would double-count
    # a bonus line that already exists.
    if r.status in DECIDED:
        line_previews, bonus_previews = [], []
    else:
        line_previews, bonus_previews = preview_qra(
            s, r.cust_nb, r.lines,
            is_return=(r.primary_intent == Intent.return_order.value))
    preview_by_nb = {p.line_nb: p for p in line_previews}
    lines_out = []
    for l in r.lines:
        lo = LineOut.model_validate(l)
        p = preview_by_nb.get(l.line_nb)
        if p:
            lo = lo.model_copy(update={
                "qra_unit_price": p.unit_price, "qra_is_free": p.is_free,
                "qra_substituted_item_nb": p.substituted_item_nb,
                "qra_substituted_item_desc": p.substituted_item_desc})
        lines_out.append(lo)

    return RequestDetail(
        id=r.id, status=r.status, intents=r.intents or [],
        primary_intent=r.primary_intent, flags=r.flags or [], cust_nb=r.cust_nb,
        customer_name=cust.customer_name if cust else None,
        transcript=r.voice.transcript,
        transcript_conf=r.voice.transcript_conf, language=r.voice.language,
        languages=r.voice.languages or [], duration_sec=r.voice.duration_sec,
        audio_url=f"/audio/{r.voice_message_id}",
        segments=r.voice.segments or [],
        target_order_nb=r.target_order_nb, assigned_to=r.assigned_to,
        committed_order_nb=r.committed_order_nb,
        lines=lines_out,
        qra_bonus_lines=[
            QraBonusLineOut(item_nb=b.item_nb, item_desc=b.item_desc,
                            qty=b.qty, uom=b.uom) for b in bonus_previews])


@router.post("/queue/{req_id}/claim")
def claim(req_id: int, s: Session = Depends(get_db),
          salesman: Salesman = Depends(get_current_salesman)):
    r = s.execute(select(PendingRequest).where(PendingRequest.id == req_id)
                  .with_for_update()).scalar_one_or_none()
    if not r:
        raise HTTPException(404)
    require_customer_ownership(r.cust_nb, salesman)
    if r.status in DECIDED:
        raise HTTPException(409, f"already decided ({r.status})")
    operator = salesman.login_id
    if r.assigned_to and r.assigned_to != operator:
        raise HTTPException(409, f"claimed by {r.assigned_to}")
    r.assigned_to = operator
    r.claimed_at = datetime.now(timezone.utc)
    r.status = RequestStatus.in_review.value
    return {"ok": True}


@router.get("/audio/{voice_id}")
def get_audio_file(voice_id: int, s: Session = Depends(get_db),
                   store=Depends(get_audio)):
    # Deliberately no salesman-ownership check here (unlike every other
    # endpoint in this router): the Android app's MediaPlayer streams this
    # URL directly, outside OkHttp/AuthInterceptor, and sends no bearer
    # token (see RequestViewModel.togglePlayback) - only the same shared
    # X-Api-Key gate every route already sits behind. Restricting this by
    # customer ownership would need that client-side plumbing first.
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
