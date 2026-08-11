from pydantic import BaseModel, Field

from app.schemas.enums import ChangeType, Intent


class ExtractedLine(BaseModel):
    raw_text: str
    raw_lang: str | None = None
    product: str | None = None
    qty: float | None = None
    uom: str | None = None
    change: ChangeType | None = None


class Extraction(BaseModel):
    intents: list[Intent] = Field(default_factory=list)
    lines: list[ExtractedLine] = Field(default_factory=list)
    categories_mentioned: list[str] = Field(default_factory=list)
    order_reference: str | None = None
    delivery_note: str | None = None
    missing: list[str] = Field(default_factory=list)
