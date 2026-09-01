from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import (get_current_salesman, get_db, require_admin,
                          require_api_key)
from app.models import Salesman
from app.schemas.auth import (AccountUpdateIn, ChangePasswordIn, LoginIn,
                              LoginOut, RegisterIn, SalesmanOut,
                              SalesmanUpdateIn)
from app.services.auth import create_token, hash_password, verify_password

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=LoginOut)
def login(body: LoginIn, s: Session = Depends(get_db)):
    salesman = s.get(Salesman, body.login_id)
    # Same generic error whether the id doesn't exist or the password is
    # wrong - a different message for "no such id" would let a caller
    # enumerate valid login ids.
    if (salesman is None or not salesman.is_active
            or not verify_password(body.password, salesman.password_hash)):
        raise HTTPException(401, "invalid id or password")
    token = create_token(salesman.login_id)
    return LoginOut(login_id=salesman.login_id, name=salesman.name,
                    email=salesman.email, role=salesman.role, token=token)


@router.post("/auth/register", response_model=SalesmanOut,
            dependencies=[Depends(require_api_key), Depends(require_admin)])
def register(body: RegisterIn, s: Session = Depends(get_db)):
    """Account provisioning - backs the admin app's "+ New account" flow
    (core/network's ApiService.register). Gated by both the shared API key
    and an authenticated admin caller: this is the only endpoint that can
    mint a new login identity, and unlike every other admin-gated route
    here there's no ownership context to fall back on if that check were
    missing. The very first admin account is never created through this
    endpoint - it's provisioned directly against the database by
    seed_admin.py, so this gate has no bootstrapping problem."""
    if s.get(Salesman, body.login_id) is not None:
        raise HTTPException(409, "login id already registered")
    salesman = Salesman(login_id=body.login_id,
                        password_hash=hash_password(body.password),
                        name=body.name, email=body.email, role=body.role)
    s.add(salesman)
    s.flush()
    return salesman


@router.patch("/salesmen/{login_id}", response_model=SalesmanOut,
             dependencies=[Depends(require_admin)])
def update_salesman(login_id: str, body: SalesmanUpdateIn,
                    s: Session = Depends(get_db)):
    """Admin-only activate/deactivate - backs the admin app's salesman
    roster. Deliberately narrow (is_active only, no password/role edit
    here): a role change is rarer and riskier than a status toggle, and a
    password reset has its own dedicated flow (change-password) that
    requires the account holder's own current password, not an admin
    override."""
    salesman = s.get(Salesman, login_id)
    if salesman is None:
        raise HTTPException(404, "no such salesman")
    if body.is_active is not None:
        salesman.is_active = body.is_active
    return salesman


@router.get("/auth/me", response_model=SalesmanOut)
def me(salesman: Salesman = Depends(get_current_salesman)):
    return salesman


@router.patch("/auth/me", response_model=SalesmanOut)
def update_me(body: AccountUpdateIn,
             salesman: Salesman = Depends(get_current_salesman)):
    if body.name is not None:
        salesman.name = body.name
    if body.email is not None:
        salesman.email = body.email
    return salesman


@router.post("/auth/change-password")
def change_password(body: ChangePasswordIn,
                    salesman: Salesman = Depends(get_current_salesman)):
    if not verify_password(body.old_password, salesman.password_hash):
        raise HTTPException(401, "current password is incorrect")
    salesman.password_hash = hash_password(body.new_password)
    return {"ok": True}
