from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (ActivityLog, OrderDetail, OrderHeader, PendingLine,
                        PendingRequest, VoiceMessage)
from app.main import app

client = TestClient(app)


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


def test_get_request_detail_404():
    resp = client.get("/queue/999999999")
    assert resp.status_code == 404


# ---- POST /queue/{id}/claim --------------------------------------------

def test_claim_success(fresh_request):
    _, req_id = fresh_request
    resp = client.post(f"/queue/{req_id}/claim", headers={"X-Operator": "alice"})
    assert resp.status_code == 200
    detail = client.get(f"/queue/{req_id}").json()
    assert detail["assigned_to"] == "alice"
    assert detail["status"] == "in_review"


def test_claim_conflict_when_claimed_by_other(fresh_request):
    _, req_id = fresh_request
    client.post(f"/queue/{req_id}/claim", headers={"X-Operator": "alice"})
    resp = client.post(f"/queue/{req_id}/claim", headers={"X-Operator": "bob"})
    assert resp.status_code == 409


def test_claim_same_operator_is_idempotent(fresh_request):
    _, req_id = fresh_request
    client.post(f"/queue/{req_id}/claim", headers={"X-Operator": "alice"})
    resp = client.post(f"/queue/{req_id}/claim", headers={"X-Operator": "alice"})
    assert resp.status_code == 200


def test_claim_missing_operator_header_400(fresh_request):
    _, req_id = fresh_request
    resp = client.post(f"/queue/{req_id}/claim")
    assert resp.status_code == 400


def test_claim_already_decided_409(fresh_request):
    _, req_id = fresh_request
    client.post(f"/requests/{req_id}/reject", headers={"X-Operator": "alice"},
               json={"reason": "spam"})
    resp = client.post(f"/queue/{req_id}/claim", headers={"X-Operator": "bob"})
    assert resp.status_code == 409


def test_claim_missing_request_404():
    resp = client.post("/queue/999999999/claim", headers={"X-Operator": "alice"})
    assert resp.status_code == 404


# ---- POST /requests/{id}/accept ----------------------------------------

def test_accept_commits_order_with_price_snapshot(fresh_request):
    vm_id, req_id = fresh_request
    resp = client.post(f"/requests/{req_id}/accept",
                       headers={"X-Operator": "alice"},
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
                           headers={"X-Operator": "alice"},
                           json={"order_type": "SO", "lines": [],
                                 "removed_line_nbs": []})
        assert resp.status_code == 422
    finally:
        _cleanup(req_id, vm_id)


def test_accept_already_committed_409(fresh_request):
    vm_id, req_id = fresh_request
    resp1 = client.post(f"/requests/{req_id}/accept",
                        headers={"X-Operator": "alice"},
                        json={"order_type": "SO",
                              "lines": [{"line_nb": 1, "item_nb": "A100",
                                        "qty": "2"}],
                              "removed_line_nbs": []})
    order_nb = resp1.json()["order_nb"]
    try:
        resp2 = client.post(f"/requests/{req_id}/accept",
                            headers={"X-Operator": "alice"},
                            json={"order_type": "SO", "lines": [],
                                  "removed_line_nbs": []})
        assert resp2.status_code == 409
    finally:
        _cleanup(req_id, vm_id, order_nb)


def test_accept_missing_request_404():
    resp = client.post("/requests/999999999/accept",
                       headers={"X-Operator": "alice"},
                       json={"order_type": "SO", "lines": [],
                             "removed_line_nbs": []})
    assert resp.status_code == 404


# ---- POST /requests/{id}/reject and /callback --------------------------

def test_reject_success(fresh_request):
    _, req_id = fresh_request
    resp = client.post(f"/requests/{req_id}/reject",
                       headers={"X-Operator": "alice"},
                       json={"reason": "duplicate", "note": "dup of #123"})
    assert resp.status_code == 200
    assert client.get(f"/queue/{req_id}").json()["status"] == "rejected"


def test_reject_already_decided_409(fresh_request):
    _, req_id = fresh_request
    client.post(f"/requests/{req_id}/reject", headers={"X-Operator": "alice"},
               json={"reason": "spam"})
    resp = client.post(f"/requests/{req_id}/reject",
                       headers={"X-Operator": "alice"},
                       json={"reason": "spam"})
    assert resp.status_code == 409


def test_callback_success(fresh_request):
    _, req_id = fresh_request
    resp = client.post(f"/requests/{req_id}/callback",
                       headers={"X-Operator": "alice"},
                       json={"note": "call back after 5pm"})
    assert resp.status_code == 200
    assert client.get(f"/queue/{req_id}").json()["status"] == "callback"


def test_callback_already_decided_409(fresh_request):
    _, req_id = fresh_request
    client.post(f"/requests/{req_id}/reject", headers={"X-Operator": "alice"},
               json={"reason": "spam"})
    resp = client.post(f"/requests/{req_id}/callback",
                       headers={"X-Operator": "alice"}, json={"note": "x"})
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
                       headers={"X-Operator": "alice"},
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
                        headers={"X-Operator": "alice"},
                        json={"order_type": "SO",
                              "lines": [{"line_nb": 1, "item_nb": "A100",
                                        "qty": "2"}],
                              "removed_line_nbs": []})
    order_nb = resp1.json()["order_nb"]
    try:
        # A second accept is the only "update a buffer row" entry point in
        # this API - it must be refused once the row left the buffer state.
        resp2 = client.post(f"/requests/{req_id}/accept",
                            headers={"X-Operator": "alice"},
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
    client.post(f"/requests/{req_id}/reject", headers={"X-Operator": "alice"},
               json={"reason": "spam"})
    resp = client.post(f"/requests/{req_id}/accept",
                       headers={"X-Operator": "alice"},
                       json={"order_type": "SO", "lines": [],
                             "removed_line_nbs": []})
    assert resp.status_code == 409
    rows = _activity_rows(req_id, "update_rejected")
    assert len(rows) == 1


# ---- other intents never auto-commit (spec: invoice/cancel/other are ---
# ---- always routed to manual review, never handled automatically) --------

@pytest.mark.parametrize("intent", ["get_invoice", "cancel_order", "other"])
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
