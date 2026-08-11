from sqlalchemy import select

from app.db import SessionLocal
from app.models import ActivityLog
from app.services.activity_log import log, log_standalone


def _make_log(event_type="voice_received", level="info", cust_nb=None,
             message="test"):
    s = SessionLocal()
    try:
        entry = ActivityLog(event_type=event_type, level=level,
                            cust_nb=cust_nb, message=message, details={})
        s.add(entry)
        s.commit()
        s.refresh(entry)
        return entry.id
    finally:
        s.close()


def _delete_log(log_id):
    s = SessionLocal()
    try:
        row = s.get(ActivityLog, log_id)
        if row:
            s.delete(row)
            s.commit()
    finally:
        s.close()


# ---- app.services.activity_log ------------------------------------------

def test_log_writes_within_callers_session(db_session):
    entry = log(db_session, "voice_received", "test message", cust_nb="C001")
    assert entry.id is not None
    found = db_session.get(ActivityLog, entry.id)
    assert found.event_type == "voice_received"
    assert found.cust_nb == "C001"


def test_log_standalone_commits_independently_of_caller_transaction():
    # No session/transaction of ours is even open here - log_standalone
    # opens and commits its own, which is exactly the point: it must
    # survive regardless of what any caller's transaction later does.
    log_standalone("error", "standalone test message", level="error",
                   cust_nb="ZZZ_TEST_STANDALONE")
    s = SessionLocal()
    try:
        row = s.scalars(select(ActivityLog).where(
            ActivityLog.cust_nb == "ZZZ_TEST_STANDALONE",
            ActivityLog.event_type == "error")).first()
        assert row is not None
        assert row.message == "standalone test message"
        s.delete(row)
        s.commit()
    finally:
        s.close()


# ---- GET /activity --------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_list_activity_returns_rows():
    lid = _make_log(event_type="email_sent", cust_nb="ZTEST1")
    try:
        resp = client.get("/activity?limit=200")
        assert resp.status_code == 200
        assert lid in [r["id"] for r in resp.json()]
    finally:
        _delete_log(lid)


def test_list_activity_filters_by_event_type():
    lid1 = _make_log(event_type="email_sent", cust_nb="ZTEST2")
    lid2 = _make_log(event_type="error", cust_nb="ZTEST2")
    try:
        resp = client.get("/activity?event_type=email_sent&limit=200")
        ids = [r["id"] for r in resp.json()]
        assert lid1 in ids
        assert lid2 not in ids
    finally:
        _delete_log(lid1)
        _delete_log(lid2)


def test_list_activity_filters_by_level():
    lid = _make_log(event_type="error", level="error", cust_nb="ZTEST3")
    try:
        resp = client.get("/activity?level=error&limit=200")
        assert lid in [r["id"] for r in resp.json()]
        resp2 = client.get("/activity?level=info&limit=200")
        assert lid not in [r["id"] for r in resp2.json()]
    finally:
        _delete_log(lid)


def test_list_activity_filters_by_cust_nb():
    lid = _make_log(cust_nb="ZTEST4")
    try:
        resp = client.get("/activity?cust_nb=ZTEST4&limit=200")
        assert lid in [r["id"] for r in resp.json()]
    finally:
        _delete_log(lid)
