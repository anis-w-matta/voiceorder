from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Salesman(Base):
    __tablename__ = "salesman"

    login_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # "salesman" (default - restricted to their own assigned customers) or
    # "admin" (manages customer/salesman assignment, bypasses ownership
    # checks - see app/services/authorization.py and catalog-service's
    # POST /orders). A plain string rather than a separate roles table:
    # this is the only role split the app has, and every existing account
    # defaults to "salesman" so nothing already provisioned changes
    # behavior.
    role: Mapped[str] = mapped_column(String(20), default="salesman",
                                      server_default="salesman")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"
