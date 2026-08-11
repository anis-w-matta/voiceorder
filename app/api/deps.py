import secrets
from typing import Iterator

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db import session_scope
from app.services.audio_store import AudioStore
from app.services.mailer import Mailer
from app.services.phone import PhoneNormaliser

_audio = AudioStore()
_phone = PhoneNormaliser()
_mailer = Mailer()


def get_db() -> Iterator[Session]:
    with session_scope() as s:
        yield s


def get_audio() -> AudioStore:
    return _audio


def get_phone() -> PhoneNormaliser:
    return _phone


def get_mailer() -> Mailer:
    return _mailer


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


def get_operator(x_operator: str | None = Header(default=None)) -> str:
    """Identify the acting operator.

    The header is still self-asserted; configuring `operators` at least
    restricts it to a known roster so claims and decisions cannot be
    attributed to an arbitrary made-up name.
    """
    name = (x_operator or "").strip()
    if not name:
        raise HTTPException(400, "X-Operator header required")
    if settings.operators and name not in settings.operators:
        raise HTTPException(403, f"unknown operator {name!r}")
    return name
