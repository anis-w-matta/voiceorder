from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Float, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.models.base import Base
from app.services.normalization import normalize_text


class Item(Base):
    __tablename__ = "item"

    item_number: Mapped[str] = mapped_column(String(30), primary_key=True)
    item_desc: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(100), index=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    aliases: Mapped[list["ItemAlias"]] = relationship(
        back_populates="item", cascade="all, delete-orphan")


class ItemAlias(Base):
    __tablename__ = "item_alias"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    item_number: Mapped[str] = mapped_column(
        ForeignKey("item.item_number", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(300))
    lang: Mapped[str] = mapped_column(String(10), default="ar")
    # Provenance: where this alias came from, so a human-entered correction
    # can be trusted differently from a bulk catalogue import. See
    # app/services/alias_learning.py for the write path that creates
    # "human_correction" rows.
    source: Mapped[str] = mapped_column(String(20), default="seed")
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    # Pre-normalized copy of `alias` (lowercased/whitespace-collapsed) so
    # the resolver's exact-alias lookup can compare against a single
    # indexed column instead of normalizing on every query.
    normalized_alias: Mapped[str] = mapped_column(String(300))

    item: Mapped["Item"] = relationship(back_populates="aliases")

    @validates("alias")
    def _sync_normalized_alias(self, key, value):
        # Auto-derives normalized_alias from alias so every existing
        # ItemAlias(...) call site (seed scripts, tests) keeps working
        # without having to pass it explicitly.
        self.normalized_alias = normalize_text(value)
        return value
