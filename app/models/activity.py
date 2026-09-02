from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                    autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("SYSUTCDATETIME()"),
        index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    level: Mapped[str] = mapped_column(String(10), default="info")
    voice_message_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    request_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    cust_nb: Mapped[str | None] = mapped_column(String(20), index=True)
    order_nb: Mapped[str | None] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
