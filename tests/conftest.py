"""Shared fixtures for the backend's regression suite.

DB-backed tests run against the `voiceorder_test` Postgres schema (same
server as the real `voiceorder` DB, `public` schema - selected via a
`search_path` connection option, never touched by these tests). That
schema already exists and is at the current Alembic head (`aa0916e613fb`)
as of writing this suite; if it's ever behind, bring it up to date with:

    ALEMBIC_SCHEMA=voiceorder_test .venv/Scripts/python -m alembic \
        -x sqlalchemy.url="<DATABASE_URL>?options=-c%20search_path=voiceorder_test" \
        upgrade head

(see alembic/env.py's ALEMBIC_SCHEMA handling). Each test runs inside a
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


def _test_db_url() -> str:
    base = settings.database_url
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}options=-c%20search_path%3D{_TEST_SCHEMA}"


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(_test_db_url(), future=True)
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
