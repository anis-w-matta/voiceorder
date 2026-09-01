"""VeNdO Intelligence Phase 3 aggregation queries - see
app/services/analytics.py. status_counts/backlog/turnaround/rejection all
scope to whatever PendingRequest rows exist today, which (since Phase 2)
includes committed ones going forward - see the module docstring there.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models import PendingLine, PendingRequest, VoiceMessage
from app.schemas.enums import RequestStatus
from app.services import analytics


@pytest.fixture
def voice_message(db_session):
    vm = VoiceMessage(phone_raw="+96170000000", audio_path="test.wav",
                      status="processed")
    db_session.add(vm)
    db_session.flush()
    return vm


def _request(db_session, voice_message, *, status, created_at=None,
            decided_at=None, assigned_to=None, cust_nb="58466",
            lines=((1, 0.9, False),)):
    kwargs = {}
    if created_at is not None:
        kwargs["created_at"] = created_at
    req = PendingRequest(voice_message_id=voice_message.id, cust_nb=cust_nb,
                        primary_intent="add_order", status=status,
                        assigned_to=assigned_to, decided_at=decided_at,
                        **kwargs)
    for line_nb, confidence, edited in lines:
        req.lines.append(PendingLine(
            line_nb=line_nb, raw_text="x", match_confidence=confidence,
            operator_edited=edited, item_nb="I1", qty=Decimal("1"), uom="EA"))
    db_session.add(req)
    db_session.flush()
    return req


class TestBacklog:
    def test_counts_only_open_statuses_and_buckets_age(
            self, db_session, voice_message):
        now = datetime.now(timezone.utc)
        _request(db_session, voice_message, status=RequestStatus.new.value,
                 created_at=now - timedelta(minutes=2))
        _request(db_session, voice_message, status=RequestStatus.in_review.value,
                 created_at=now - timedelta(minutes=90))
        _request(db_session, voice_message, status=RequestStatus.rejected.value,
                 created_at=now - timedelta(minutes=1), decided_at=now)

        r = analytics.backlog_summary(db_session, analytics.RequestsFilter())
        assert r.total == 2
        assert r.age_buckets["<5m"] == 1
        assert r.age_buckets["60m+"] == 1

    def test_buckets_span_the_full_minute_range(self, db_session, voice_message):
        now = datetime.now(timezone.utc)
        _request(db_session, voice_message, status=RequestStatus.new.value,
                 created_at=now - timedelta(minutes=7))
        _request(db_session, voice_message, status=RequestStatus.new.value,
                 created_at=now - timedelta(minutes=20))
        _request(db_session, voice_message, status=RequestStatus.new.value,
                 created_at=now - timedelta(minutes=45))

        r = analytics.backlog_summary(db_session, analytics.RequestsFilter())
        assert r.total == 3
        assert r.age_buckets["5-10m"] == 1
        assert r.age_buckets["10-30m"] == 1
        assert r.age_buckets["30-60m"] == 1


class TestTurnaround:
    def test_computes_percentiles_over_decided_requests_only(
            self, db_session, voice_message):
        now = datetime.now(timezone.utc)
        _request(db_session, voice_message, status=RequestStatus.rejected.value,
                 created_at=now - timedelta(hours=2), decided_at=now)
        _request(db_session, voice_message, status=RequestStatus.new.value,
                 created_at=now - timedelta(hours=2))

        r = analytics.turnaround_summary(db_session, analytics.RequestsFilter())
        assert r.sample_size == 1
        assert r.median_seconds == pytest.approx(7200, rel=0.05)

    def test_empty_sample_reports_none_not_zero(self, db_session):
        r = analytics.turnaround_summary(
            db_session, analytics.RequestsFilter(cust_nb="no-such-customer"))
        assert r.sample_size == 0
        assert r.median_seconds is None


class TestRejection:
    def test_rate_over_decided_population(self, db_session, voice_message):
        now = datetime.now(timezone.utc)
        _request(db_session, voice_message, status=RequestStatus.rejected.value,
                 decided_at=now)
        _request(db_session, voice_message, status=RequestStatus.committed.value,
                 decided_at=now)
        _request(db_session, voice_message, status=RequestStatus.new.value)

        r = analytics.rejection_summary(db_session, analytics.RequestsFilter())
        assert r.sample_size == 2  # new (undecided) excluded
        assert r.rejection_rate == pytest.approx(0.5)


class TestAiQuality:
    def test_correction_rate_by_confidence_bucket(self, db_session, voice_message):
        _request(db_session, voice_message, status=RequestStatus.new.value,
                 lines=[(1, 0.3, True), (2, 0.97, False), (3, 0.97, False)])

        r = analytics.ai_quality_summary(db_session, analytics.RequestsFilter())
        assert r.reviewed_lines == 3
        assert r.edited_lines == 1
        by_bucket = {b.bucket: b for b in r.by_confidence_bucket}
        assert by_bucket["low"].sample_size == 1
        assert by_bucket["low"].correction_rate == 1.0
        assert by_bucket["very_high"].sample_size == 2
        assert by_bucket["very_high"].correction_rate == 0.0


class TestSalesmenRequestMetrics:
    def test_grouped_by_assigned_to(self, db_session, voice_message):
        now = datetime.now(timezone.utc)
        _request(db_session, voice_message, status=RequestStatus.rejected.value,
                 assigned_to="sm_a", decided_at=now)
        _request(db_session, voice_message, status=RequestStatus.committed.value,
                 assigned_to="sm_a", decided_at=now)
        _request(db_session, voice_message, status=RequestStatus.committed.value,
                 assigned_to="sm_b", decided_at=now)

        r = analytics.salesmen_request_metrics(
            db_session, analytics.RequestsFilter())
        by_sm = {x.salesman_id: x for x in r}
        assert by_sm["sm_a"].request_count == 2
        assert by_sm["sm_a"].rejection_rate == pytest.approx(0.5)
        assert by_sm["sm_b"].request_count == 1
        assert by_sm["sm_b"].rejection_rate == pytest.approx(0.0)


class TestActivitySummary:
    """Phase 10 - a NEW admin-gated aggregate endpoint (app/api/analytics.py
    require_admin), deliberately separate from the existing raw GET
    /activity, which has no per-user auth at all (see
    vendo-intelligence-web/docs/audit/04_auth_map.md)."""

    def _log(self, db_session, *, event_type, ts, cust_nb=None, level="info"):
        from app.models import ActivityLog
        db_session.add(ActivityLog(event_type=event_type, ts=ts, level=level,
                                   cust_nb=cust_nb, message="test"))
        db_session.flush()

    def test_by_hour_groups_across_the_full_24_hour_range(self, db_session):
        self._log(db_session, event_type="voice_received",
                  ts=datetime(2026, 3, 1, 9, 15, tzinfo=timezone.utc))
        self._log(db_session, event_type="voice_received",
                  ts=datetime(2026, 3, 2, 9, 45, tzinfo=timezone.utc))
        self._log(db_session, event_type="order_committed",
                  ts=datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc))

        r = analytics.activity_by_hour(db_session, analytics.ActivityFilter())
        by_hour = {x.hour: x.count for x in r}
        assert len(r) == 24  # every hour present, even with zero events
        assert by_hour[9] >= 2
        assert by_hour[14] >= 1

    def test_by_event_type_groups_and_sorts_descending(self, db_session):
        self._log(db_session, event_type="voice_received",
                  ts=datetime(2026, 3, 1, tzinfo=timezone.utc))
        self._log(db_session, event_type="voice_received",
                  ts=datetime(2026, 3, 1, tzinfo=timezone.utc))
        self._log(db_session, event_type="request_rejected",
                  ts=datetime(2026, 3, 1, tzinfo=timezone.utc))

        r = analytics.activity_by_event_type(
            db_session, analytics.ActivityFilter(
                date_from=datetime(2026, 3, 1, tzinfo=timezone.utc),
                date_to=datetime(2026, 3, 2, tzinfo=timezone.utc)))
        by_type = {x.event_type: x.count for x in r}
        assert by_type["voice_received"] == 2
        assert by_type["request_rejected"] == 1

    def test_filters_by_customer(self, db_session):
        self._log(db_session, event_type="voice_received",
                  ts=datetime(2026, 3, 1, tzinfo=timezone.utc), cust_nb="C1")
        self._log(db_session, event_type="voice_received",
                  ts=datetime(2026, 3, 1, tzinfo=timezone.utc), cust_nb="C2")

        r = analytics.activity_summary(
            db_session, analytics.ActivityFilter(cust_nb="C1"))
        total = sum(x.count for x in r.by_event_type)
        assert total == 1

    def test_volume_over_time_buckets_by_day(self, db_session):
        self._log(db_session, event_type="voice_received",
                  ts=datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc))
        self._log(db_session, event_type="voice_received",
                  ts=datetime(2026, 3, 1, 20, 0, tzinfo=timezone.utc))
        self._log(db_session, event_type="voice_received",
                  ts=datetime(2026, 3, 2, 8, 0, tzinfo=timezone.utc))

        r = analytics.activity_volume_over_time(
            db_session, analytics.ActivityFilter(
                date_from=datetime(2026, 3, 1, tzinfo=timezone.utc),
                date_to=datetime(2026, 3, 3, tzinfo=timezone.utc)))
        by_day = {x.day.date(): x.count for x in r}
        assert by_day[datetime(2026, 3, 1).date()] == 2
        assert by_day[datetime(2026, 3, 2).date()] == 1
