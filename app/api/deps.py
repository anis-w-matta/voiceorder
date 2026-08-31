import secrets
from typing import Iterator

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db import session_scope
from app.models import Salesman
from app.services.audio_store import AudioStore
from app.services.auth import decode_token

_audio = AudioStore()


def get_db() -> Iterator[Session]:
    with session_scope() as s:
        yield s


def get_audio() -> AudioStore:
    return _audio


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Shared-secret gate, active only once `api_key` is configured.

    Left open by default so existing local setups keep working - see the
    note in Settings. compare_digest keeps the check constant-time.
    """
    expected = settings.api_key
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(401, "missing or invalid API key")


def get_current_salesman(authorization: str | None = Header(default=None),
                         s: Session = Depends(get_db)) -> Salesman:
    """Identify the acting salesman from a bearer token issued by
    POST /auth/login (app/api/auth.py, app/services/auth.py).

    Replaces the old free-text X-Operator header: a token can only exist
    for a real, still-active salesman row, so claims and decisions can no
    longer be attributed to an arbitrary made-up name.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization[len("bearer "):].strip()
    try:
        login_id = decode_token(token)
    except jwt.InvalidTokenError:
        raise HTTPException(401, "invalid or expired token")
    salesman = s.get(Salesman, login_id)
    if salesman is None or not salesman.is_active:
        raise HTTPException(401, "invalid or expired token")
    return salesman


def get_operator(salesman: Salesman = Depends(get_current_salesman)) -> str:
    return salesman.login_id
