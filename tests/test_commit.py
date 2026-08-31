"""app/services/commit.py's OrderCommitService - the backend half of the
commit saga. catalog-service's own POST /orders (order number allocation,
QRA application, the real ownership DB check) is mocked at the
catalog_client boundary throughout - see catalog_client's own module
docstring on why that boundary, not a live second service, is what these
tests stub.
"""
from decimal import Decimal

import pytest

from app.errors import RequestNotFound, RequestNotReviewable
from app.models import PendingLine, PendingRequest, VoiceMessage
from app.schemas.enums import RequestStatus
from app.services import catalog_client
from app.services.catalog_client import CommitTransientError
from app.services.commit import OrderCommitService


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
    defaults = dict(order_nb="260000123", order_type="SO", cust_nb="58466",
                    target_order_nb=None, target_order_type=None, lines=[])
    defaults.update(overrides)
    return catalog_client.CreateOrderResult(**defaults)


class TestCommitSuccess:
    def test_successful_commit_deletes_pending_request_and_returns_result(
            self, db_session, pending_request, monkeypatch):
        result = _fake_create_order_result()
        monkeypatch.setattr(catalog_client, "create_order",
                            lambda **kwargs: result)

        svc = OrderCommitService(db_session)
        out = svc.commit(pending_request.id, "SO", line_edits=[],
                         operator="sm1")

        assert out is result
        db_session.expire_all()
        assert db_session.get(PendingRequest, pending_request.id) is None

    def test_successful_commit_passes_acting_identity_never_client_supplied(
            self, db_session, pending_request, monkeypatch):
        captured = {}

        def fake_create_order(**kwargs):
            captured.update(kwargs)
            return _fake_create_order_result()

        monkeypatch.setattr(catalog_client, "create_order", fake_create_order)

        svc = OrderCommitService(db_session)
        svc.commit(pending_request.id, "SO", line_edits=[], operator="sm1",
                  acting_is_admin=True)

        assert captured["acting_salesman_id"] == "sm1"
        assert captured["acting_is_admin"] is True
        assert captured["cust_nb"] == "58466"

    def test_operator_line_edits_applied_before_commit_and_sent_upstream(
            self, db_session, pending_request, monkeypatch):
        captured = {}

        def fake_create_order(**kwargs):
            captured.update(kwargs)
            return _fake_create_order_result()

        monkeypatch.setattr(catalog_client, "create_order", fake_create_order)

        edit = catalog_client.LineEditIn(line_nb=1, qty=Decimal("5"))
        svc = OrderCommitService(db_session)
        svc.commit(pending_request.id, "SO",
                  line_edits=[edit], operator="sm1")

        sent_lines = captured["lines"]
        assert len(sent_lines) == 1 and sent_lines[0].qty == Decimal("2"), (
            "create_order() must receive the pre-edit snapshot - "
            "catalog-service re-applies line_edits itself")
        sent_edits = captured["line_edits"]
        assert len(sent_edits) == 1 and sent_edits[0].qty == Decimal("5")


class TestCommitStatusGuards:
    def test_missing_request_raises_request_not_found(self, db_session):
        svc = OrderCommitService(db_session)
        with pytest.raises(RequestNotFound):
            svc.commit(999999999, "SO", line_edits=[], operator="sm1")

    @pytest.mark.parametrize("status", [RequestStatus.rejected.value,
                                        RequestStatus.committing.value])
    def test_rejected_or_committing_request_is_not_reviewable(
            self, db_session, pending_request, status):
        pending_request.status = status
        db_session.flush()
        svc = OrderCommitService(db_session)
        with pytest.raises(RequestNotReviewable):
            svc.commit(pending_request.id, "SO", line_edits=[], operator="sm1")


class TestCommitFailureHandling:
    def test_transient_error_leaves_request_in_committing_for_reconciliation(
            self, db_session, pending_request, monkeypatch):
        def raise_transient(**kwargs):
            raise CommitTransientError("catalog-service unreachable")

        monkeypatch.setattr(catalog_client, "create_order", raise_transient)

        svc = OrderCommitService(db_session)
        with pytest.raises(CommitTransientError):
            svc.commit(pending_request.id, "SO", line_edits=[], operator="sm1")

        db_session.expire_all()
        req = db_session.get(PendingRequest, pending_request.id)
        assert req is not None
        assert req.status == RequestStatus.committing.value
        assert req.commit_intent_id is not None

    def test_definitive_failure_reverts_status_and_clears_commit_intent(
            self, db_session, pending_request, monkeypatch):
        from app.errors import CustomerNotFound

        def raise_definitive(**kwargs):
            raise CustomerNotFound("58466")

        monkeypatch.setattr(catalog_client, "create_order", raise_definitive)

        original_status = pending_request.status
        svc = OrderCommitService(db_session)
        with pytest.raises(CustomerNotFound):
            svc.commit(pending_request.id, "SO", line_edits=[], operator="sm1")

        db_session.expire_all()
        req = db_session.get(PendingRequest, pending_request.id)
        assert req is not None
        assert req.status == original_status
        assert req.commit_intent_id is None
        assert req.decided_at is None
