"""app/services/commit.py's OrderCommitService - the backend half of the
commit saga. catalog-service's own POST /orders (order number allocation,
QRA application, the real ownership DB check) is mocked at the
catalog_client boundary throughout - see catalog_client's own module
docstring on why that boundary, not a live second service, is what these
tests stub.
"""
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.errors import RequestNotFound, RequestNotReviewable
from app.models import ActivityLog, PendingLine, PendingRequest, VoiceMessage
from app.schemas.enums import RequestStatus
from app.services import catalog_client
from app.services.catalog_client import CommitTransientError
from app.services.commit import OrderCommitService, reconcile_stuck_commit


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
    def test_successful_commit_marks_request_committed_and_sets_order_nb(
            self, db_session, pending_request, monkeypatch):
        result = _fake_create_order_result()
        monkeypatch.setattr(catalog_client, "create_order",
                            lambda **kwargs: result)

        svc = OrderCommitService(db_session)
        out = svc.commit(pending_request.id, "SO", line_edits=[],
                         operator="sm1")

        assert out is result
        db_session.expire_all()
        req = db_session.get(PendingRequest, pending_request.id)
        # The row is kept, not deleted - AI confidence/edit history and the
        # request-to-order link both need to survive commit for analytics
        # (see vendo-intelligence-web/docs/audit/06_data_limitations.md #3).
        assert req is not None
        assert req.status == RequestStatus.committed.value
        assert req.committed_order_nb == result.order_nb
        assert len(req.lines) == 1

    def test_retried_accept_on_already_committed_request_is_rejected(
            self, db_session, pending_request, monkeypatch):
        monkeypatch.setattr(catalog_client, "create_order",
                            lambda **kwargs: _fake_create_order_result())

        svc = OrderCommitService(db_session)
        svc.commit(pending_request.id, "SO", line_edits=[], operator="sm1")

        with pytest.raises(RequestNotReviewable):
            svc.commit(pending_request.id, "SO", line_edits=[],
                      operator="sm1")

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

    def test_definitive_failure_also_reverts_line_edits(
            self, db_session, pending_request, monkeypatch):
        """An unauthorized/otherwise-rejected accept must not leave
        attacker- or otherwise-uncommitted line edits behind - only the
        actual commit is supposed to make edits stick. Covers edit, add,
        and remove together since _apply_edits can do all three."""
        from app.errors import CustomerNotAuthorized

        def raise_definitive(**kwargs):
            raise CustomerNotAuthorized("58466")

        monkeypatch.setattr(catalog_client, "create_order", raise_definitive)

        edit = catalog_client.LineEditIn(line_nb=1, item_nb="9999",
                                         item_desc="Swapped", qty=Decimal("99"))
        add = catalog_client.LineEditIn(line_nb=2, item_nb="2002",
                                        item_desc="Added", qty=Decimal("3"),
                                        uom="EA")
        svc = OrderCommitService(db_session)
        with pytest.raises(CustomerNotAuthorized):
            svc.commit(pending_request.id, "SO", line_edits=[edit, add],
                      operator="attacker")

        db_session.expire_all()
        req = db_session.get(PendingRequest, pending_request.id)
        assert len(req.lines) == 1, (
            "a line added by a rejected commit attempt must not survive")
        [line] = req.lines
        assert line.item_nb == "1001"
        assert line.qty == Decimal("2")
        assert line.operator_edited is False, (
            "a rejected edit attempt must not leave operator_edited=True "
            "behind on the original line")


class TestReconciliation:
    """reconcile_stuck_commit() - app/worker.py's crash-recovery sweep for
    a request left in "committing" by an interrupted commit() call. Shares
    _finalize_committed() with the normal accept path, so a reconciled
    commit must end up in the same committed/committed_order_nb state -
    the request/order lineage a reconciled commit produces matters just as
    much for analytics as one that never got interrupted."""

    def test_successful_reconciliation_marks_request_committed(
            self, db_session, pending_request, monkeypatch):
        # Get the request stuck in "committing" with a real replay payload,
        # the same way test_transient_error_... above does.
        monkeypatch.setattr(
            catalog_client, "create_order",
            lambda **kwargs: (_ for _ in ()).throw(
                CommitTransientError("catalog-service unreachable")))
        svc = OrderCommitService(db_session)
        with pytest.raises(CommitTransientError):
            svc.commit(pending_request.id, "SO", line_edits=[],
                      operator="sm1")
        db_session.expire_all()
        req = db_session.get(PendingRequest, pending_request.id)
        assert req.status == RequestStatus.committing.value

        result = _fake_create_order_result()
        monkeypatch.setattr(catalog_client, "create_order",
                            lambda **kwargs: result)

        resolved = reconcile_stuck_commit(db_session, pending_request.id)

        assert resolved is True
        db_session.expire_all()
        req = db_session.get(PendingRequest, pending_request.id)
        assert req is not None
        assert req.status == RequestStatus.committed.value
        assert req.committed_order_nb == result.order_nb

    def test_definitive_failure_during_reconciliation_reverts_line_edits(
            self, db_session, pending_request, monkeypatch):
        """The crash-recovery retry path must undo _apply_edits() on a
        definitive failure the same way the live path's own definitive-
        failure branch does (see TestCommitFailureHandling's
        test_definitive_failure_also_reverts_line_edits) - otherwise a
        request that gets stuck, retried, and definitively rejected on
        retry keeps an edit that was never actually committed."""
        monkeypatch.setattr(
            catalog_client, "create_order",
            lambda **kwargs: (_ for _ in ()).throw(
                CommitTransientError("catalog-service unreachable")))
        edit = catalog_client.LineEditIn(line_nb=1, item_nb="9999",
                                         item_desc="Swapped", qty=Decimal("99"))
        svc = OrderCommitService(db_session)
        with pytest.raises(CommitTransientError):
            svc.commit(pending_request.id, "SO", line_edits=[edit],
                      operator="sm1")
        db_session.expire_all()
        req = db_session.get(PendingRequest, pending_request.id)
        assert req.status == RequestStatus.committing.value
        assert req.lines[0].item_nb == "9999", (
            "edit should already be applied locally, same as the live path")

        from app.errors import CustomerNotFound

        def raise_definitive(**kwargs):
            raise CustomerNotFound("58466")

        monkeypatch.setattr(catalog_client, "create_order", raise_definitive)

        resolved = reconcile_stuck_commit(db_session, pending_request.id)

        assert resolved is True
        db_session.expire_all()
        req = db_session.get(PendingRequest, pending_request.id)
        assert req.status == RequestStatus.new.value
        assert req.commit_intent_id is None
        assert len(req.lines) == 1
        assert req.lines[0].item_nb == "1001", (
            "a line edit staged for a commit that was never confirmed must "
            "not survive a definitive failure discovered on retry")
        assert req.lines[0].qty == Decimal("2")

    def test_live_commit_does_not_re_finalize_after_reconciliation_wins(
            self, db_session, pending_request, monkeypatch):
        """The row lock is released before commit()'s own outbound
        create_order() call (see that method's comment), so a slow call can
        race app/worker.py's reconciliation sweep: the sweep can resend the
        same commit_intent_id and finalize the request first (idempotent on
        catalog-service's side) before the live call's own response comes
        back. _finalize_committed() itself isn't idempotent - it
        unconditionally logs "order_committed" - so it must not run a
        second time once the sweep has already finalized this request."""
        result = _fake_create_order_result()

        def create_order_that_lets_reconciliation_win_first(**kwargs):
            # Simulates the sweep beating this call to the finalize step:
            # by the time this (the live call's own) create_order()
            # returns, the request has already been committed elsewhere.
            monkeypatch.setattr(catalog_client, "create_order",
                                lambda **kw: result)
            assert reconcile_stuck_commit(db_session, pending_request.id) is True
            return result

        monkeypatch.setattr(catalog_client, "create_order",
                            create_order_that_lets_reconciliation_win_first)

        svc = OrderCommitService(db_session)
        out = svc.commit(pending_request.id, "SO", line_edits=[],
                         operator="sm1")

        assert out is result
        db_session.expire_all()
        req = db_session.get(PendingRequest, pending_request.id)
        assert req.status == RequestStatus.committed.value
        entries = db_session.scalars(
            select(ActivityLog).where(
                ActivityLog.event_type == "order_committed",
                ActivityLog.request_id == pending_request.id)
        ).all()
        assert len(entries) == 1, (
            "reconciliation finalizing first, then the live call's own "
            "response arriving, must not double-log order_committed")
