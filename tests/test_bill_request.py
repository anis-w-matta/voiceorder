from sqlalchemy import select

from app.config import settings
from app.errors import SmtpNotConfigured
from app.models import BillEmailLog
from app.services.bill_request import (maybe_send_bill_notification,
                                       order_nb_from_reference)


class _RecordingMailer:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def send_text(self, to, subject, body):
        if self.fail:
            raise SmtpNotConfigured()
        self.calls.append((to, subject, body))


def _log_count(session, cust_nb, order_nb):
    return len(list(session.scalars(select(BillEmailLog).where(
        BillEmailLog.cust_nb == cust_nb, BillEmailLog.order_nb == order_nb,
        BillEmailLog.order_type == "SO"))))


# ---- order_nb_from_reference -------------------------------------------

def test_order_nb_from_reference_extracts_single_digit_run():
    assert order_nb_from_reference("order 990000001 please") == "990000001"


def test_order_nb_from_reference_none_when_missing():
    assert order_nb_from_reference(None) is None
    assert order_nb_from_reference("my last order") is None


def test_order_nb_from_reference_none_when_ambiguous():
    assert order_nb_from_reference("order 1 or maybe order 2") is None


# ---- maybe_send_bill_notification --------------------------------------

def test_sends_once_on_valid_match(db_session):
    mailer = _RecordingMailer()
    maybe_send_bill_notification(
        db_session, mailer, cust_nb="C001", order_reference="990000001",
        voice_message_id=1, request_id=1)

    assert len(mailer.calls) == 1
    to, subject, body = mailer.calls[0]
    assert to == settings.bill_request_notify_email
    assert body == "I am requesting bill for C001, 990000001"
    assert _log_count(db_session, "C001", "990000001") == 1


def test_second_matching_request_does_not_resend(db_session):
    mailer = _RecordingMailer()
    maybe_send_bill_notification(
        db_session, mailer, cust_nb="C001", order_reference="990000001",
        voice_message_id=1, request_id=1)
    maybe_send_bill_notification(
        db_session, mailer, cust_nb="C001", order_reference="990000001",
        voice_message_id=2, request_id=2)

    assert len(mailer.calls) == 1
    assert _log_count(db_session, "C001", "990000001") == 1


def test_wrong_customer_does_not_send(db_session):
    mailer = _RecordingMailer()
    # 990000004 belongs to C002, not C001.
    maybe_send_bill_notification(
        db_session, mailer, cust_nb="C001", order_reference="990000004",
        voice_message_id=1, request_id=1)
    assert mailer.calls == []


def test_nonexistent_order_does_not_send(db_session):
    mailer = _RecordingMailer()
    maybe_send_bill_notification(
        db_session, mailer, cust_nb="C001", order_reference="999999999",
        voice_message_id=1, request_id=1)
    assert mailer.calls == []


def test_missing_reference_does_not_send(db_session):
    mailer = _RecordingMailer()
    maybe_send_bill_notification(
        db_session, mailer, cust_nb="C001", order_reference=None,
        voice_message_id=1, request_id=1)
    assert mailer.calls == []


def test_ambiguous_reference_does_not_send(db_session):
    mailer = _RecordingMailer()
    maybe_send_bill_notification(
        db_session, mailer, cust_nb="C001",
        order_reference="order 1 or order 2", voice_message_id=1,
        request_id=1)
    assert mailer.calls == []


def test_smtp_failure_does_not_record_as_sent(db_session):
    mailer = _RecordingMailer(fail=True)
    maybe_send_bill_notification(
        db_session, mailer, cust_nb="C001", order_reference="990000001",
        voice_message_id=1, request_id=1)
    assert _log_count(db_session, "C001", "990000001") == 0
