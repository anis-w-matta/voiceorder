from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Lead(Base):
    __tablename__ = "lead"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                    autoincrement=True)
    voice_message_id: Mapped[int] = mapped_column(
        ForeignKey("voice_message.id"))
    phone_e164: Mapped[str | None] = mapped_column(String(20), index=True)
    categories_sent: Mapped[list] = mapped_column(JSONB, default=list)
    products_mentioned: Mapped[list] = mapped_column(JSONB, default=list)
    catalogue_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
    converted_cust_nb: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
