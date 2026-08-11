import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_mailer
from app.config import settings
from app.db import SessionLocal
from app.main import app


class _RecordingMailer:
    def __init__(self):
        self.calls = []

    def send_html(self, to, subject, html):
        self.calls.append((to, subject, html))


def _override_db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


client = TestClient(app)


@pytest.fixture(autouse=True)
def _db_override():
    # Bills are read-only, so a rollback-only session is enough here and
    # avoids depending on commit ordering. Scoped to this module only -
    # left as a bare module-level assignment it would leak into every
    # other test module sharing this `app` instance.
    app.dependency_overrides[get_db] = _override_db
    try:
        yield
    finally:
        del app.dependency_overrides[get_db]


def test_bill_request_without_smtp_reports_undelivered():
    # SMTP_PASSWORD is intentionally left blank in .env, so this is the
    # real default behaviour, not a mocked one.
    resp = client.post("/bills/request",
                       json={"cust_nb": "C001", "order_nb": "990000001",
                             "order_type": "SO"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["delivered"] is False
    assert "SMTP" in body["reason"]
    assert body["total"] == "65.00"


def test_bill_request_delivers_when_mailer_configured():
    mailer = _RecordingMailer()
    app.dependency_overrides[get_mailer] = lambda: mailer
    try:
        resp = client.post("/bills/request",
                           json={"cust_nb": "C001", "order_nb": "990000001",
                                 "order_type": "SO"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["delivered"] is True
        assert body["sent_to"] == settings.bill_recipient_email
        assert len(mailer.calls) == 1
        to, subject, html = mailer.calls[0]
        assert to == settings.bill_recipient_email
        assert "990000001" in subject
        assert "Test Trading" in html
    finally:
        del app.dependency_overrides[get_mailer]


def test_bill_request_unknown_order_404():
    resp = client.post("/bills/request",
                       json={"cust_nb": "C001", "order_nb": "no-such-order",
                             "order_type": "SO"})
    assert resp.status_code == 404


def test_bill_request_wrong_customer_403():
    resp = client.post("/bills/request",
                       json={"cust_nb": "C001", "order_nb": "990000004",
                             "order_type": "SO"})
    assert resp.status_code == 403


def test_bill_request_empty_order_422():
    resp = client.post("/bills/request",
                       json={"cust_nb": "C001", "order_nb": "990000003",
                             "order_type": "SO"})
    assert resp.status_code == 422
