from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BillEmailLog(Base):
    """One row per (cust_nb, order_nb, order_type) the auto bill-request
    notification has already been sent for. The unique constraint is the
    actual guard against sending twice for the same pair - a plain "check
    then send" is subject to a race between two concurrent requests."""

    __tablename__ = "bill_email_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                    autoincrement=True)
    cust_nb: Mapped[str] = mapped_column(String(20))
    order_nb: Mapped[str] = mapped_column(String(30))
    order_type: Mapped[str] = mapped_column(String(10))
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("cust_nb", "order_nb", "order_type",
                         name="uq_bill_email_log_pair"),
    )
