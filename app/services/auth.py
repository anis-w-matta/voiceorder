from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"),
                              password_hash.encode("utf-8"))
    except ValueError:
        # Malformed/foreign hash format - never a match, not a server error.
        return False


def create_token(login_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": login_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> str:
    """Returns the salesman login_id encoded in a valid token.

    Raises jwt.InvalidTokenError (or a subclass, e.g. ExpiredSignatureError)
    on anything wrong with the token - callers turn that into a 401.
    """
    payload = jwt.decode(token, settings.jwt_secret,
                         algorithms=[settings.jwt_algorithm])
    return payload["sub"]
