from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKeyConstraint, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class OrderHeader(Base):
    __tablename__ = "order_header"

    order_nb: Mapped[str] = mapped_column(String(30), primary_key=True)
    order_type: Mapped[str] = mapped_column(String(10), primary_key=True)
    cust_nb: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String(20), default="manual")

    lines: Mapped[list["OrderDetail"]] = relationship(
        back_populates="header", cascade="all, delete-orphan",
        order_by="OrderDetail.line_nb")


class OrderDetail(Base):
    __tablename__ = "order_details"

    order_nb: Mapped[str] = mapped_column(String(30), primary_key=True)
    order_type: Mapped[str] = mapped_column(String(10), primary_key=True)
    line_nb: Mapped[int] = mapped_column(primary_key=True)
    item_nb: Mapped[str] = mapped_column(String(30))
    item_desc: Mapped[str] = mapped_column(String(300))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    uom: Mapped[str] = mapped_column(String(20))
    # Snapshot of Item.unit_price at commit time, so a later price change
    # doesn't rewrite the amount on a bill for an order placed under the old
    # price. NULL when the item had no price set at commit time.
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Snapshot of the classification decided at intake time (see
    # app.services.item_classifier), same reasoning as unit_price above.
    category: Mapped[str | None] = mapped_column(String(100))

    header: Mapped["OrderHeader"] = relationship(back_populates="lines")

    __table_args__ = (
        ForeignKeyConstraint(
            ["order_nb", "order_type"],
            ["order_header.order_nb", "order_header.order_type"]),
    )
