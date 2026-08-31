from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_salesman, get_db, require_api_key
from app.models import Salesman
from app.schemas.auth import (AccountUpdateIn, ChangePasswordIn, LoginIn,
                              LoginOut, RegisterIn, SalesmanOut)
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
            dependencies=[Depends(require_api_key)])
def register(body: RegisterIn, s: Session = Depends(get_db)):
    """Account provisioning. Deliberately not reachable from the 5 mobile
    screens (there is no registration screen in the app) - always gated by
    the API key regardless of whether `settings.api_key` is configured for
    the other routers, since this is the only endpoint that can mint a new
    login identity."""
    if s.get(Salesman, body.login_id) is not None:
        raise HTTPException(409, "login id already registered")
    salesman = Salesman(login_id=body.login_id,
                        password_hash=hash_password(body.password),
                        name=body.name, email=body.email)
    s.add(salesman)
    s.flush()
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
