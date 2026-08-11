from datetime import datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, Boolean, DateTime, Float, ForeignKey,
                        Numeric, String, Text, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PendingRequest(Base):
    __tablename__ = "pending_request"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                    autoincrement=True)
    voice_message_id: Mapped[int] = mapped_column(
        ForeignKey("voice_message.id"))
    cust_nb: Mapped[str | None] = mapped_column(String(20), index=True)
    intents: Mapped[list] = mapped_column(JSONB, default=list)
    primary_intent: Mapped[str] = mapped_column(String(40))
    target_order_nb: Mapped[str | None] = mapped_column(String(30))
    target_order_type: Mapped[str | None] = mapped_column(String(10))
    raw_model_output: Mapped[dict] = mapped_column(JSONB, default=dict)
    flags: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    assigned_to: Mapped[str | None] = mapped_column(String(100))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(100))
    decision_note: Mapped[str | None] = mapped_column(Text)
    committed_order_nb: Mapped[str | None] = mapped_column(String(30))

    lines: Mapped[list["PendingLine"]] = relationship(
        back_populates="request", cascade="all, delete-orphan",
        order_by="PendingLine.line_nb")
    voice: Mapped["VoiceMessage"] = relationship()


class PendingLine(Base):
    __tablename__ = "pending_request_line"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                    autoincrement=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("pending_request.id", ondelete="CASCADE"))
    line_nb: Mapped[int] = mapped_column()
    raw_text: Mapped[str] = mapped_column(Text)
    raw_lang: Mapped[str | None] = mapped_column(String(10))
    item_nb: Mapped[str | None] = mapped_column(String(30))
    item_desc: Mapped[str | None] = mapped_column(String(300))
    qty: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    uom: Mapped[str | None] = mapped_column(String(20))
    match_confidence: Mapped[float | None] = mapped_column(Float)
    match_method: Mapped[str | None] = mapped_column(String(20))
    operator_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    candidates: Mapped[list] = mapped_column(JSONB, default=list)
    category: Mapped[str | None] = mapped_column(String(100))

    request: Mapped["PendingRequest"] = relationship(back_populates="lines")
