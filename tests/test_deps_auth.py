"""app/api/deps.py - token identity and the admin gate. Not the ownership
check itself (see test_authorization.py), just who's making the request.
"""
import pytest
from fastapi import HTTPException

from app.api.deps import get_current_salesman, require_admin
from app.services.auth import create_token


class TestGetCurrentSalesman:
    def test_valid_token_resolves_active_salesman(self, db_session, salesman):
        token = create_token(salesman.login_id)
        resolved = get_current_salesman(authorization=f"Bearer {token}",
                                        s=db_session)
        assert resolved.login_id == salesman.login_id

    def test_missing_header_is_401(self, db_session):
        with pytest.raises(HTTPException) as exc:
            get_current_salesman(authorization=None, s=db_session)
        assert exc.value.status_code == 401

    def test_non_bearer_scheme_is_401(self, db_session):
        with pytest.raises(HTTPException) as exc:
            get_current_salesman(authorization="Basic abc123", s=db_session)
        assert exc.value.status_code == 401

    def test_garbage_token_is_401(self, db_session):
        with pytest.raises(HTTPException) as exc:
            get_current_salesman(authorization="Bearer not-a-real-token",
                                 s=db_session)
        assert exc.value.status_code == 401

    def test_inactive_salesman_is_401(self, db_session, salesman):
        salesman.is_active = False
        db_session.flush()
        token = create_token(salesman.login_id)
        with pytest.raises(HTTPException) as exc:
            get_current_salesman(authorization=f"Bearer {token}", s=db_session)
        assert exc.value.status_code == 401


class TestRequireAdmin:
    def test_admin_passes(self, admin):
        assert require_admin(salesman=admin) is admin

    def test_plain_salesman_is_403(self, salesman):
        with pytest.raises(HTTPException) as exc:
            require_admin(salesman=salesman)
        assert exc.value.status_code == 403
