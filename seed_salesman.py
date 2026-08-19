"""Provision or reset a salesman login for the VeNdO Android app.

There is no registration screen in the app (see the 5-screen UI spec), so
accounts are provisioned here or via the API-key-gated POST /auth/register.

Usage:
    .venv/Scripts/python seed_salesman.py <login_id> <password> <name> [email]

With no arguments, seeds one demo account (login_id "demo", password
"demo1234") for local Android development against a dev database.
"""
import sys

from app.db import session_scope
from app.models import Salesman
from app.services.auth import hash_password


def upsert_salesman(login_id: str, password: str, name: str,
                    email: str | None = None) -> None:
    with session_scope() as s:
        salesman = s.get(Salesman, login_id)
        if salesman is None:
            salesman = Salesman(login_id=login_id)
            s.add(salesman)
        salesman.password_hash = hash_password(password)
        salesman.name = name
        salesman.email = email
        salesman.is_active = True
    print(f"salesman {login_id!r} ready")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        upsert_salesman("demo", "demo1234", "Demo Salesman",
                        "demo@example.com")
    elif len(sys.argv) in (4, 5):
        upsert_salesman(*sys.argv[1:])
    else:
        print(__doc__)
        sys.exit(1)
