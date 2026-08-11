"""Points every test at the isolated `voiceorder_test` Postgres schema
instead of the dev database, and must run before `app.*` is imported
anywhere (module-level engine/session creation reads DATABASE_URL once).

The `voiceorder_test` schema lives inside the same physical `voiceorder`
database as dev data (the DB role here has no CREATEDB privilege) but is
fully isolated at the schema level via search_path, and is migrated /
seeded independently - see seed_test.py. Nothing in this suite should
mutate the dev database; the schema check below is a guard against that.
"""
import os

os.environ["DATABASE_URL"] = (
    "postgresql+psycopg://voiceorder:changeme@localhost/voiceorder"
    "?options=-c%20search_path%3Dvoiceorder_test%2Cpublic"
)

import pytest
from sqlalchemy import text

from app.db import SessionLocal


@pytest.fixture
def db_session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture(autouse=True, scope="session")
def _verify_test_schema():
    """Refuse to run if DATABASE_URL isn't actually pointed at the test
    schema - a bug here would otherwise let tests write to dev data."""
    s = SessionLocal()
    try:
        schema = s.execute(text("SELECT current_schema()")).scalar()
        assert schema == "voiceorder_test", (
            f"tests must run against voiceorder_test schema, got {schema!r}")
    finally:
        s.close()
