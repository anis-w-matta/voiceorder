from pydantic import BaseModel, ConfigDict


class LoginIn(BaseModel):
    login_id: str
    password: str


class RegisterIn(BaseModel):
    login_id: str
    password: str
    name: str
    email: str | None = None
    role: str = "salesman"


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


class AccountUpdateIn(BaseModel):
    name: str | None = None
    email: str | None = None


class SalesmanUpdateIn(BaseModel):
    is_active: bool | None = None


class SalesmanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    login_id: str
    name: str
    email: str | None
    role: str = "salesman"
    is_active: bool = True


class LoginOut(SalesmanOut):
    token: str
