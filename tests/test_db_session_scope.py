import pytest
from sqlalchemy.exc import OperationalError

from app.db import SessionLocal, session_scope
from app.models import Customer


def _get(cust_nb):
    s = SessionLocal()
    try:
        return s.get(Customer, cust_nb)
    finally:
        s.close()


def _cleanup(cust_nb):
    s = SessionLocal()
    try:
        row = s.get(Customer, cust_nb)
        if row:
            s.delete(row)
            s.commit()
    finally:
        s.close()


def test_session_scope_commits_on_success():
    try:
        with session_scope() as s:
            s.add(Customer(customer_number="ZDBS001", customer_name="ok"))
        assert _get("ZDBS001") is not None
    finally:
        _cleanup("ZDBS001")


def test_session_scope_rolls_back_on_exception_leaving_no_partial_write():
    # A DB connection drop, constraint violation, or any other failure
    # mid-transaction must never leave a half-written row behind - the
    # customer add below must vanish, not just the exception's own cause.
    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with session_scope() as s:
            s.add(Customer(customer_number="ZDBS002", customer_name="boom"))
            s.flush()
            raise Boom("simulated mid-transaction failure")

    assert _get("ZDBS002") is None


def test_session_scope_rolls_back_on_db_error_and_propagates():
    with pytest.raises(OperationalError):
        with session_scope() as s:
            s.add(Customer(customer_number="ZDBS003", customer_name="db-err"))
            s.flush()
            raise OperationalError("simulated connection loss", {}, Exception())

    assert _get("ZDBS003") is None
