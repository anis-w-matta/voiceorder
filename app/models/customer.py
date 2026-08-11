from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Customer(Base):
    __tablename__ = "customer"

    customer_number: Mapped[str] = mapped_column(
        "CustomerNumber", String(20), primary_key=True)
    customer_name: Mapped[str] = mapped_column("CustomerName", String(200))
    email: Mapped[str | None] = mapped_column(String(200))
    telephone: Mapped[str | None] = mapped_column(String(50))
    city: Mapped[str | None] = mapped_column("City", String(100))
    address1: Mapped[str | None] = mapped_column("Address1", String(200))
    address2: Mapped[str | None] = mapped_column("Address2", String(200))
    phone_e164: Mapped[str | None] = mapped_column(String(20), index=True)
