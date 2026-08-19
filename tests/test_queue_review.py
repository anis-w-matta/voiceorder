from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal, session_scope
from app.models import (ActivityLog, ItemAlias, OrderDetail, OrderHeader,
                        PendingLine, PendingRequest, Salesman, VoiceMessage)
from app.main import app
from app.services.auth import create_token, hash_password

client = TestClient(app)


def _ensure_salesman(login_id, name):
    with session_scope() as s:
        sm = s.get(Salesman, login_id)
        if sm is None:
            s.add(Salesman(login_id=login_id,
                           password_hash=hash_password("testpass123"),
                           name=name, email=f"{login_id}@example.com"))
        else:
            sm.is_active = True


# Every test below authenticates as one of these two salesmen via bearer
# token (get_operator, app/api/deps.py) instead of the old free-text
# X-Operator header - login_id doubles as the "operator" name asserted
# on claimed/committed/decided rows.
TOKENS = {}
for _login_id in ("alice", "bob"):
    _ensure_salesman(_login_id, _login_id.title())
    TOKENS[_login_id] = create_token(_login_id)


def _make_request(status="new", cust_nb="C001", lines=None, assigned_to=None):
    """Commits a fresh VoiceMessage + PendingRequest (+lines) directly, so
    later HTTP requests in the same test (each getting its own DB session)
    can see it. Cleaned up at teardown."""
    s = SessionLocal()
    try:
        vm = VoiceMessage(phone_raw="03123456", phone_e164="+9613123456",
                          audio_path="2026/08/10/doesnotexist.wav",
                          duration_sec=Decimal("4.20"), transcript="test call",
                          transcript_conf=-0.2, language="en", status="drafted")
        s.add(vm)
        s.flush()
        req = PendingRequest(
            voice_message_id=vm.id, cust_nb=cust_nb, intents=["add_order"],
            primary_intent="add_order", flags=[], status=status,
            assigned_to=assigned_to,
            raw_model_output={})
        if lines:
            req.lines = lines
        s.add(req)
        s.commit()
        return vm.id, req.id
    finally:
        s.close()


def _cleanup(req_id, vm_id, order_nb=None, order_type="SO"):
    s = SessionLocal()
    try:
        if order_nb:
            s.execute(OrderDetail.__table__.delete().where(
                OrderDetail.order_nb == order_nb,
                OrderDetail.order_type == order_type))
            s.execute(OrderHeader.__table__.delete().where(
                OrderHeader.order_nb == order_nb,
                OrderHeader.order_type == order_type))
        req = s.get(PendingRequest, req_id)
        if req:
            s.delete(req)
        vm = s.get(VoiceMessage, vm_id)
        if vm:
            s.delete(vm)
        s.commit()
    finally:
        s.close()


@pytest.fixture
def fresh_request():
    """(vm_id, req_id) for a plain 'new' request with one resolved line."""
    vm_id, req_id = _make_request(lines=[
        PendingLine(line_nb=1, raw_text="blue paint", item_nb="A100",
                   item_desc="Blue Paint 5L", qty=Decimal("2"), uom="PCS",
                   match_method="exact", match_confidence=1.0)])
    yield vm_id, req_id
    _cleanup(req_id, vm_id)


# ---- GET /queue ------------------------------------------------------

def test_list_queue_includes_new_request(fresh_request):
    _, req_id = fresh_request
    resp = client.get("/queue?limit=200")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()]
    assert req_id in ids


def test_list_queue_filters_by_status(fresh_request):
    _, req_id = fresh_request
    resp = client.get("/queue?status=rejected&limit=200")
    assert resp.status_code == 200
    assert req_id not in [r["id"] for r in resp.json()]

    resp = client.get("/queue?status=new&limit=200")
    assert req_id in [r["id"] for r in resp.json()]


def test_list_queue_filters_by_flag():
    vm_id, req_id = _make_request()
    s = SessionLocal()
    try:
        req = s.get(PendingRequest, req_id)
        req.flags = ["missing_qty"]
        s.commit()
    finally:
        s.close()
    try:
        resp = client.get("/queue?flag=missing_qty&limit=200")
        assert req_id in [r["id"] for r in resp.json()]
        resp = client.get("/queue?flag=cancellation&limit=200")
        assert req_id not in [r["id"] for r in resp.json()]
    finally:
        _cleanup(req_id, vm_id)


# ---- GET /queue/{id} ---------------------------------------------------

def test_get_request_detail_shape(fresh_request):
    vm_id, req_id = fresh_request
    resp = client.get(f"/queue/{req_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == req_id
    assert body["audio_url"] == f"/audio/{vm_id}"
    assert len(body["lines"]) == 1
    assert body["lines"][0]["item_nb"] == "A100"
    assert body["cust_nb"] == "C001"
    # New per-line reliability fields round-trip through the queue API even
    # when unset, so the dashboard can render them unconditionally.
    line = body["lines"][0]
    assert line["line_flags"] == []
    assert line["resolution_meta"] == {}
    assert line["attributes"] == {}
    assert line["qualifiers"] == {}
    assert line["change"] is None


def test_get_request_detail_404():
    resp = client.get("/queue/999999999")
    assert resp.status_code == 404


# ---- POST /queue/{id}/claim --------------------------------------------

def test_claim_success(fresh_request):
    _, req_id = fresh_request
    resp = client.post(f"/queue/{req_id}/claim", headers={"Authorization": f"Bearer {TOKENS['alice']}"})
    assert resp.status_code == 200
    detail = client.get(f"/queue/{req_id}").json()
    assert detail["assigned_to"] == "alice"
    assert detail["status"] == "in_review"


def test_claim_conflict_when_claimed_by_other(fresh_request):
    _, req_id = fresh_request
    client.post(f"/queue/{req_id}/claim", headers={"Authorization": f"Bearer {TOKENS['alice']}"})
    resp = client.post(f"/queue/{req_id}/claim", headers={"Authorization": f"Bearer {TOKENS['bob']}"})
    assert resp.status_code == 409


def test_claim_same_operator_is_idempotent(fresh_request):
    _, req_id = fresh_request
    client.post(f"/queue/{req_id}/claim", headers={"Authorization": f"Bearer {TOKENS['alice']}"})
    resp = client.post(f"/queue/{req_id}/claim", headers={"Authorization": f"Bearer {TOKENS['alice']}"})
    assert resp.status_code == 200


def test_claim_missing_bearer_token_401(fresh_request):
    _, req_id = fresh_request
    resp = client.post(f"/queue/{req_id}/claim")
    assert resp.status_code == 401


def test_claim_invalid_bearer_token_401(fresh_request):
    _, req_id = fresh_request
    resp = client.post(f"/queue/{req_id}/claim",
                       headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_claim_already_decided_409(fresh_request):
    _, req_id = fresh_request
    client.post(f"/requests/{req_id}/reject", headers={"Authorization": f"Bearer {TOKENS['alice']}"},
               json={"reason": "spam"})
    resp = client.post(f"/queue/{req_id}/claim", headers={"Authorization": f"Bearer {TOKENS['bob']}"})
    assert resp.status_code == 409


def test_claim_missing_request_404():
    resp = client.post("/queue/999999999/claim", headers={"Authorization": f"Bearer {TOKENS['alice']}"})
    assert resp.status_code == 404


# ---- POST /requests/{id}/accept ----------------------------------------

def test_accept_commits_order_with_price_snapshot(fresh_request):
    vm_id, req_id = fresh_request
    resp = client.post(f"/requests/{req_id}/accept",
                       headers={"Authorization": f"Bearer {TOKENS['alice']}"},
                       json={"order_type": "SO",
                             "lines": [{"line_nb": 1, "item_nb": "A100",
                                       "qty": "2"}],
                             "removed_line_nbs": []})
    assert resp.status_code == 200
    order_nb = resp.json()["order_nb"]
    try:
        s = SessionLocal()
        try:
            detail = s.get(OrderDetail, (order_nb, "SO", 1))
            assert detail.item_nb == "A100"
            assert detail.qty == Decimal("2.000")
            assert detail.unit_price == Decimal("19.50")
        finally:
            s.close()
    finally:
        _cleanup(req_id, vm_id, order_nb)


def test_accept_unresolved_line_422():
    vm_id, req_id = _make_request(lines=[
        PendingLine(line_nb=1, raw_text="something", item_nb=None,
                   qty=None)])
    try:
        resp = client.post(f"/requests/{req_id}/accept",
                           headers={"Authorization": f"Bearer {TOKENS['alice']}"},
                           json={"order_type": "SO", "lines": [],
                                 "removed_line_nbs": []})
        assert resp.status_code == 422
    finally:
        _cleanup(req_id, vm_id)


def test_accept_already_committed_409(fresh_request):
    vm_id, req_id = fresh_request
    resp1 = client.post(f"/requests/{req_id}/accept",
                        headers={"Authorization": f"Bearer {TOKENS['alice']}"},
                        json={"order_type": "SO",
                              "lines": [{"line_nb": 1, "item_nb": "A100",
                                        "qty": "2"}],
                              "removed_line_nbs": []})
    order_nb = resp1.json()["order_nb"]
    try:
        resp2 = client.post(f"/requests/{req_id}/accept",
                            headers={"Authorization": f"Bearer {TOKENS['alice']}"},
                            json={"order_type": "SO", "lines": [],
                                  "removed_line_nbs": []})
        assert resp2.status_code == 409
    finally:
        _cleanup(req_id, vm_id, order_nb)


def test_accept_denied_when_customer_does_not_exist():
    # Database is the source of truth for customer identity: even if a
    # PendingRequest somehow carries a cust_nb with no matching Customer
    # row, committing it into an order must be refused - no operator
    # action can override that.
    vm_id, req_id = _make_request(cust_nb="ZNOPE_999999", lines=[
        PendingLine(line_nb=1, raw_text="blue paint", item_nb="A100",
                   item_desc="Blue Paint 5L", qty=Decimal("2"), uom="PCS",
                   match_method="exact", match_confidence=1.0)])
    try:
        resp = client.post(f"/requests/{req_id}/accept",
                           headers={"Authorization": f"Bearer {TOKENS['alice']}"},
                           json={"order_type": "SO",
                                 "lines": [{"line_nb": 1, "item_nb": "A100",
                                           "qty": "2"}],
                                 "removed_line_nbs": []})
        assert resp.status_code == 404
        s = SessionLocal()
        try:
            assert s.get(PendingRequest, req_id).status == "new"
            leaked = s.scalars(select(OrderHeader).where(
                OrderHeader.cust_nb == "ZNOPE_999999")).first()
            assert leaked is None
        finally:
            s.close()
    finally:
        _cleanup(req_id, vm_id)


def test_accept_missing_request_404():
    resp = client.post("/requests/999999999/accept",
                       headers={"Authorization": f"Bearer {TOKENS['alice']}"},
                       json={"order_type": "SO", "lines": [],
                             "removed_line_nbs": []})
    assert resp.status_code == 404


# ---- POST /requests/{id}/reject and /callback --------------------------

def test_reject_success(fresh_request):
    _, req_id = fresh_request
    resp = client.post(f"/requests/{req_id}/reject",
                       headers={"Authorization": f"Bearer {TOKENS['alice']}"},
                       json={"reason": "duplicate", "note": "dup of #123"})
    assert resp.status_code == 200
    assert client.get(f"/queue/{req_id}").json()["status"] == "rejected"


def test_reject_already_decided_409(fresh_request):
    _, req_id = fresh_request
    client.post(f"/requests/{req_id}/reject", headers={"Authorization": f"Bearer {TOKENS['alice']}"},
               json={"reason": "spam"})
    resp = client.post(f"/requests/{req_id}/reject",
                       headers={"Authorization": f"Bearer {TOKENS['alice']}"},
                       json={"reason": "spam"})
    assert resp.status_code == 409


def test_callback_success(fresh_request):
    _, req_id = fresh_request
    resp = client.post(f"/requests/{req_id}/callback",
                       headers={"Authorization": f"Bearer {TOKENS['alice']}"},
                       json={"note": "call back after 5pm"})
    assert resp.status_code == 200
    assert client.get(f"/queue/{req_id}").json()["status"] == "callback"


def test_callback_already_decided_409(fresh_request):
    _, req_id = fresh_request
    client.post(f"/requests/{req_id}/reject", headers={"Authorization": f"Bearer {TOKENS['alice']}"},
               json={"reason": "spam"})
    resp = client.post(f"/requests/{req_id}/callback",
                       headers={"Authorization": f"Bearer {TOKENS['alice']}"}, json={"note": "x"})
    assert resp.status_code == 409


# ---- GET /audio/{voice_id} ----------------------------------------------

def test_get_audio_missing_voice_404():
    resp = client.get("/audio/999999999")
    assert resp.status_code == 404


def test_get_audio_missing_file_404(fresh_request):
    vm_id, _ = fresh_request
    # audio_path points at a file that was never written.
    resp = client.get(f"/audio/{vm_id}")
    assert resp.status_code == 404


# ---- buffer-state guard on updates (spec: order may only be updated ----
# ---- while it is in the buffer state) ------------------------------------

def _activity_rows(request_id, event_type):
    s = SessionLocal()
    try:
        return list(s.scalars(select(ActivityLog).where(
            ActivityLog.request_id == request_id,
            ActivityLog.event_type == event_type)))
    finally:
        s.close()


def test_accept_commits_and_logs_order_committed(fresh_request):
    vm_id, req_id = fresh_request
    resp = client.post(f"/requests/{req_id}/accept",
                       headers={"Authorization": f"Bearer {TOKENS['alice']}"},
                       json={"order_type": "SO",
                             "lines": [{"line_nb": 1, "item_nb": "A100",
                                       "qty": "2"}],
                             "removed_line_nbs": []})
    order_nb = resp.json()["order_nb"]
    try:
        rows = _activity_rows(req_id, "order_committed")
        assert len(rows) == 1
        assert rows[0].order_nb == order_nb
    finally:
        _cleanup(req_id, vm_id, order_nb)


def test_update_attempt_on_committed_request_rejected_and_logged(fresh_request):
    vm_id, req_id = fresh_request
    resp1 = client.post(f"/requests/{req_id}/accept",
                        headers={"Authorization": f"Bearer {TOKENS['alice']}"},
                        json={"order_type": "SO",
                              "lines": [{"line_nb": 1, "item_nb": "A100",
                                        "qty": "2"}],
                              "removed_line_nbs": []})
    order_nb = resp1.json()["order_nb"]
    try:
        # A second accept is the only "update a buffer row" entry point in
        # this API - it must be refused once the row left the buffer state.
        resp2 = client.post(f"/requests/{req_id}/accept",
                            headers={"Authorization": f"Bearer {TOKENS['alice']}"},
                            json={"order_type": "SO",
                                  "lines": [{"line_nb": 1, "item_nb": "B200",
                                            "qty": "9"}],
                                  "removed_line_nbs": []})
        assert resp2.status_code == 409

        # The order itself must be untouched by the rejected attempt.
        s = SessionLocal()
        try:
            detail = s.get(OrderDetail, (order_nb, "SO", 1))
            assert detail.item_nb == "A100"
            assert detail.qty == Decimal("2.000")
        finally:
            s.close()

        rows = _activity_rows(req_id, "update_rejected")
        assert len(rows) == 1
    finally:
        _cleanup(req_id, vm_id, order_nb)


def test_update_attempt_on_rejected_request_refused_and_logged(fresh_request):
    _, req_id = fresh_request
    client.post(f"/requests/{req_id}/reject", headers={"Authorization": f"Bearer {TOKENS['alice']}"},
               json={"reason": "spam"})
    resp = client.post(f"/requests/{req_id}/accept",
                       headers={"Authorization": f"Bearer {TOKENS['alice']}"},
                       json={"order_type": "SO", "lines": [],
                             "removed_line_nbs": []})
    assert resp.status_code == 409
    rows = _activity_rows(req_id, "update_rejected")
    assert len(rows) == 1


# ---- no intent auto-commits (spec: every intent is always routed to -----
# ---- manual review, never handled automatically) -------------------------

@pytest.mark.parametrize("intent", ["return_order", "repeat_order", "other"])
def test_non_bill_intents_stay_in_queue_uncommitted(intent):
    vm_id, req_id = _make_request(status="new")
    s = SessionLocal()
    try:
        req = s.get(PendingRequest, req_id)
        req.primary_intent = intent
        req.intents = [intent]
        s.commit()
    finally:
        s.close()
    try:
        detail = client.get(f"/queue/{req_id}").json()
        assert detail["status"] == "new"
        assert detail["primary_intent"] == intent
        # Nothing in the system auto-commits: no order exists for this
        # request until an operator explicitly accepts it.
        s = SessionLocal()
        try:
            row = s.get(PendingRequest, req_id)
            assert row.committed_order_nb is None
        finally:
            s.close()
    finally:
        _cleanup(req_id, vm_id)


# ---- accepting with remember_alias=true teaches the resolver -------------

def test_accept_with_remember_alias_creates_alias_usable_by_resolver():
    vm_id, req_id = _make_request(lines=[
        PendingLine(line_nb=1, raw_text="a very special widget",
                   item_nb=None, qty=Decimal("1"), uom="PCS",
                   candidates=[{"item_nb": "A101", "item_desc": "White Paint 5L",
                               "category": "Paint", "score": 0.65,
                               "method": "fuzzy"}])])
    try:
        resp = client.post(
            f"/requests/{req_id}/accept", headers={"Authorization": f"Bearer {TOKENS['alice']}"},
            json={"order_type": "SO",
                 "lines": [{"line_nb": 1, "item_nb": "A100", "qty": "1",
                           "remember_alias": True}],
                 "removed_line_nbs": []})
        assert resp.status_code == 200
        order_nb = resp.json()["order_nb"]

        from app.services.item_resolver import ItemResolver
        s = SessionLocal()
        try:
            match, cands = ItemResolver(s).resolve("a very special widget")
            assert match is not None
            assert match.item_nb == "A100"
            assert match.method == "alias"
        finally:
            s.close()
    finally:
        _cleanup(req_id, vm_id, order_nb)
        s = SessionLocal()
        try:
            s.execute(ItemAlias.__table__.delete().where(
                ItemAlias.normalized_alias == "a very special widget"))
            s.commit()
        finally:
            s.close()


def test_accept_without_remember_alias_does_not_write_alias():
    vm_id, req_id = _make_request(lines=[
        PendingLine(line_nb=1, raw_text="another special gadget",
                   item_nb=None, qty=Decimal("1"), uom="PCS",
                   candidates=[{"item_nb": "A101", "item_desc": "White Paint 5L",
                               "category": "Paint", "score": 0.65,
                               "method": "fuzzy"}])])
    try:
        resp = client.post(
            f"/requests/{req_id}/accept", headers={"Authorization": f"Bearer {TOKENS['alice']}"},
            json={"order_type": "SO",
                 "lines": [{"line_nb": 1, "item_nb": "A100", "qty": "1"}],
                 "removed_line_nbs": []})
        assert resp.status_code == 200
        order_nb = resp.json()["order_nb"]

        s = SessionLocal()
        try:
            rows = s.execute(select(ItemAlias).where(
                ItemAlias.normalized_alias == "another special gadget")).all()
            assert rows == []
        finally:
            s.close()
    finally:
        _cleanup(req_id, vm_id, order_nb)


# ---- pipeline failure attribution: TranscriptionFailed

def _make_voice_only(phone="+9613123456"):
    s = SessionLocal()
    try:
        vm = VoiceMessage(phone_raw="03123456", phone_e164=phone,
                          audio_path="2026/08/10/doesnotexist.wav",
                          status="received")
        s.add(vm)
        s.commit()
        return vm.id
    finally:
        s.close()


def _cleanup_voice_only(vm_id):
    s = SessionLocal()
    try:
        vm = s.get(VoiceMessage, vm_id)
        if vm:
            s.delete(vm)
        s.commit()
    finally:
        s.close()


class _RaisingSTT:
    def transcribe(self, audio_path, duration=None):
        raise RuntimeError("network blip")


class _FailIfCalledSTT:
    def transcribe(self, audio_path, duration=None):
        raise AssertionError(
            "stt.transcribe must not be called for a client_whisper voice "
            "message - its transcript is already final")


def test_client_whisper_transcript_skips_server_stt(monkeypatch):
    import app.pipeline as pipeline_module
    from app.services.audio_store import AudioStore

    monkeypatch.setattr(pipeline_module, "duration_seconds", lambda p: 3.0)
    s = SessionLocal()
    try:
        vm = VoiceMessage(phone_raw="03123456", phone_e164="+9613123456",
                          audio_path="2026/08/10/doesnotexist.wav",
                          status="received", transcript="place order test",
                          transcript_source="client_whisper", language="en")
        s.add(vm)
        s.commit()
        vm_id = vm.id
    finally:
        s.close()
    try:
        pipeline = pipeline_module.IntakePipeline(_FailIfCalledSTT(), AudioStore())
        pipeline.process(vm_id)
        s = SessionLocal()
        try:
            row = s.get(VoiceMessage, vm_id)
            assert row.transcript == "place order test"
            assert row.status == "drafted"
            assert row.transcript_conf == 1.0
        finally:
            s.close()
    finally:
        s = SessionLocal()
        try:
            req = s.scalars(select(PendingRequest).where(
                PendingRequest.voice_message_id == vm_id)).first()
            if req:
                s.delete(req)
                s.commit()
        finally:
            s.close()
        _cleanup_voice_only(vm_id)


def test_transcription_failure_is_wrapped_as_transcription_failed(monkeypatch):
    import app.pipeline as pipeline_module
    from app.errors import TranscriptionFailed
    from app.services.audio_store import AudioStore

    monkeypatch.setattr(pipeline_module, "duration_seconds", lambda p: 3.0)
    vm_id = _make_voice_only()
    try:
        pipeline = pipeline_module.IntakePipeline(_RaisingSTT(), AudioStore())
        try:
            pipeline.process(vm_id)
            assert False, "expected TranscriptionFailed"
        except TranscriptionFailed as e:
            assert "network blip" in str(e)
    finally:
        _cleanup_voice_only(vm_id)
