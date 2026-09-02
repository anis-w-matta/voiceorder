"""Shared fixtures for the backend's regression suite.

DB-backed tests run against the `voiceorder_test` schema (same SQL Server
database as the real `voiceorder` DB - a real SQL Server schema, isolated
via SQLAlchemy's schema_translate_map, not a separate database). That
schema needs to exist and be at the current Alembic head before running
this suite:

    CREATE SCHEMA voiceorder_test;   -- once, via SSMS/sqlcmd
    ALEMBIC_SCHEMA=voiceorder_test .venv/Scripts/python -m alembic upgrade head

(see alembic/env.py's ALEMBIC_SCHEMA handling - it now redirects both the
alembic_version bookkeeping table AND every migration's unqualified table
DDL into that schema, not just bookkeeping). Each test runs inside a
transaction that's rolled back afterward (nested SAVEPOINT so code under
test can call session.commit() without actually persisting), so tests
never depend on each other's data and never require a manual cleanup step.

Post catalog-service split, this backend no longer owns Customer/Item/
Order tables - only Salesman/VoiceMessage/PendingRequest/PendingLine/
ActivityLog. Anything customer/item/order-shaped is reached through
app.services.catalog_client, which every DB-backed test here monkeypatches
rather than calling over real HTTP (see test_catalog_client_boundary.py
for the one place the client's own request/response shaping is tested).
"""
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models import Salesman

_TEST_SCHEMA = "voiceorder_test"


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(settings.database_url, future=True).execution_options(
        schema_translate_map={None: _TEST_SCHEMA})
    yield eng
    eng.dispose()


@pytest.fixture
def db_session(engine) -> Session:
    """A session bound to one connection/outer transaction, rolled back
    after the test. Uses a SAVEPOINT (begin_nested) so application code
    calling session.commit() (e.g. OrderCommitService.commit()) doesn't
    end the outer transaction early - restarted automatically after each
    commit via the session_end/after_transaction_end pattern from the
    SQLAlchemy docs.
    """
    connection = engine.connect()
    outer = connection.begin()
    SessionLocal = sessionmaker(bind=connection, future=True,
                                expire_on_commit=False)
    session = SessionLocal()
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


def _unique_login_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@pytest.fixture
def salesman(db_session) -> Salesman:
    s = Salesman(login_id=_unique_login_id("sm"), password_hash="x",
                name="Test Salesman", role="salesman")
    db_session.add(s)
    db_session.flush()
    return s


@pytest.fixture
def admin(db_session) -> Salesman:
    s = Salesman(login_id=_unique_login_id("admin"), password_hash="x",
                name="Test Admin", role="admin")
    db_session.add(s)
    db_session.flush()
    return s
