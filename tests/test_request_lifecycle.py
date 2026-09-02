"""End-to-end PendingRequest.status lifecycle coverage: Created -> Claimed
-> Accepted/Committed, and Created -> Claimed -> Rejected, exercised as one
sequence through the actual route handler functions in app/api/queue.py
and app/api/review.py (called directly as plain Python functions, the
same lightweight pattern the rest of this suite uses instead of
fastapi.testclient - every dependency here is either a real DB session
fixture or an explicit Salesman instance, matching each handler's type
annotations exactly).

Phase 17 certification gap: test_commit.py covers OrderCommitService.commit()
in isolation starting from status="new" (skipping the claim step), and
test_authorization.py covers the ownership *gate* function in isolation -
neither exercises claim() and accept()/reject() together against the same
request the way a real reviewer session does. This file closes that gap.
"""
from decimal import Decimal

import pytest

from app.api import queue, review
from app.models import PendingLine, PendingRequest, VoiceMessage
from app.schemas.api_in import AcceptIn, RejectIn
from app.schemas.enums import RequestStatus
from app.services import catalog_client


@pytest.fixture
def voice_message(db_session):
    vm = VoiceMessage(phone_raw="+96170000000", audio_path="test.wav",
                      status="processed")
    db_session.add(vm)
    db_session.flush()
    return vm


@pytest.fixture
def pending_request(db_session, voice_message):
    req = PendingRequest(voice_message_id=voice_message.id, cust_nb="58466",
                        primary_intent="add_order", status=RequestStatus.new.value)
    req.lines.append(PendingLine(line_nb=1, raw_text="2 widgets",
                                 match_method="exact", item_nb="1001",
                                 item_desc="Widget", qty=Decimal("2"), uom="EA"))
    db_session.add(req)
    db_session.flush()
    return req


def _fake_create_order_result(**overrides):
    defaults = dict(order_nb="260000456", order_type="SO", cust_nb="58466",
                    target_order_nb=None, target_order_type=None, lines=[])
    defaults.update(overrides)
    return catalog_client.CreateOrderResult(**defaults)


class TestRequestLifecycle:
    def test_created_claimed_accepted_committed(
            self, db_session, pending_request, admin, monkeypatch):
        assert pending_request.status == RequestStatus.new.value

        # Claimed: POST /queue/{id}/claim
        queue.claim(req_id=pending_request.id, s=db_session, salesman=admin)
        db_session.flush()
        assert pending_request.status == RequestStatus.in_review.value
        assert pending_request.assigned_to == admin.login_id
        assert pending_request.claimed_at is not None

        # Accepted -> Committed: POST /requests/{id}/accept
        result = _fake_create_order_result()
        monkeypatch.setattr(catalog_client, "create_order",
                            lambda **kwargs: result)
        out = review.accept(
            req_id=pending_request.id,
            body=AcceptIn(order_type="SO", lines=[], removed_line_nbs=[]),
            s=db_session, salesman=admin)
        db_session.expire_all()

        assert out == {"order_nb": "260000456", "order_type": "SO"}
        assert pending_request.status == RequestStatus.committed.value
        assert pending_request.committed_order_nb == "260000456"

    def test_created_claimed_rejected(
            self, db_session, pending_request, admin):
        assert pending_request.status == RequestStatus.new.value

        queue.claim(req_id=pending_request.id, s=db_session, salesman=admin)
        db_session.flush()
        assert pending_request.status == RequestStatus.in_review.value

        review.reject(req_id=pending_request.id,
                      body=RejectIn(reason="no_stock", note="out of stock"),
                      s=db_session, salesman=admin)
        db_session.flush()

        assert pending_request.status == RequestStatus.rejected.value
        assert pending_request.decided_by == admin.login_id
        assert pending_request.decided_at is not None
        assert pending_request.decision_note == "no_stock: out of stock"

    def test_claim_then_reject_by_a_second_reviewer_is_rejected(
            self, db_session, pending_request, admin, salesman, monkeypatch):
        """The 409 "claimed by X" guard - a second reviewer must not be
        able to just take over an in-review request out from under the
        first. The second claimant is a plain salesman who legitimately
        owns the customer (mocked at the catalog_client boundary, same
        pattern as test_authorization.py), so the only thing standing in
        their way is the claim conflict itself, not an ownership 403."""
        from fastapi import HTTPException

        queue.claim(req_id=pending_request.id, s=db_session, salesman=admin)
        db_session.flush()
        assert pending_request.assigned_to == admin.login_id

        monkeypatch.setattr(
            catalog_client, "get_customer_detail",
            lambda cust_nb: catalog_client.CustomerDetail(
                cust_nb=cust_nb, customer_name="Test Co", email=None,
                telephone=None, city=None, address1=None,
                salesman_id=salesman.login_id))
        with pytest.raises(HTTPException) as exc_info:
            queue.claim(req_id=pending_request.id, s=db_session, salesman=salesman)
        assert exc_info.value.status_code == 409
        # Status/assignment must be unchanged by the failed second claim.
        assert pending_request.status == RequestStatus.in_review.value
        assert pending_request.assigned_to == admin.login_id

    def test_already_decided_request_cannot_be_rejected_again(
            self, db_session, pending_request, admin):
        """AlreadyDecided guard: once a request has an outcome, a second
        decision (e.g. a race between two reviewer actions) must be
        rejected with 409, not silently overwrite decided_by/decided_at."""
        from fastapi import HTTPException

        queue.claim(req_id=pending_request.id, s=db_session, salesman=admin)
        review.reject(req_id=pending_request.id,
                      body=RejectIn(reason="no_stock"),
                      s=db_session, salesman=admin)
        db_session.flush()
        first_decided_at = pending_request.decided_at

        with pytest.raises(HTTPException) as exc_info:
            review.reject(req_id=pending_request.id,
                          body=RejectIn(reason="changed_mind"),
                          s=db_session, salesman=admin)
        assert exc_info.value.status_code == 409
        assert pending_request.decided_at == first_decided_at
        assert pending_request.decision_note == "no_stock: "
