from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


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

    item: Mapped["Item"] = relationship(back_populates="aliases")
