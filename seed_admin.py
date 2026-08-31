"""Provision or promote a VeNdO admin account.

An admin bypasses customer-ownership checks (can view/manage every
customer, place orders for any of them, and assign customers to
salesmen - see app/services/authorization.py and catalog-service's
POST /orders). There's no registration screen for this either (same as
seed_salesman.py) - always provisioned here or via the API-key-gated
POST /auth/register + a manual role promotion.

Usage:
    .venv/Scripts/python seed_admin.py <login_id> <password> <name> [email]

With no arguments, seeds one demo admin account (login_id "admin",
password "admin1234") for local development.
"""
import sys

from app.db import session_scope
from app.models import Salesman
from app.services.auth import hash_password


def upsert_admin(login_id: str, password: str, name: str,
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
        salesman.role = "admin"
    print(f"admin {login_id!r} ready")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        upsert_admin("admin", "admin1234", "System Administrator",
                    "admin@example.com")
    elif len(sys.argv) in (4, 5):
        upsert_admin(*sys.argv[1:])
    else:
        print(__doc__)
        sys.exit(1)
