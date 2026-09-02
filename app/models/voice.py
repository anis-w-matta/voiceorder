from datetime import datetime
from decimal import Decimal

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, Numeric,
                        String, Text, text)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VoiceMessage(Base):
    __tablename__ = "voice_message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                    autoincrement=True)
    phone_raw: Mapped[str] = mapped_column(String(50))
    audio_path: Mapped[str] = mapped_column(Text)
    duration_sec: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    transcript: Mapped[str | None] = mapped_column(Text)
    normalized_transcript: Mapped[str | None] = mapped_column(Text)
    transcript_quality: Mapped[str] = mapped_column(String(20), default="good")
    transcription_disagreement: Mapped[bool] = mapped_column(Boolean, default=False)
    transcript_attempts: Mapped[list] = mapped_column(JSON, default=list)
    transcript_conf: Mapped[float | None] = mapped_column(Float)
    language: Mapped[str | None] = mapped_column(String(10))
    languages: Mapped[list] = mapped_column(JSON, default=list)
    segments: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="received",
                                        index=True)
    # "server" (default): app/worker.py calls self.stt.transcribe() (Gemini).
    # "client_whisper": the Android app already transcribed on-device with
    # whisper.cpp and posted the text - app/pipeline.py skips the Gemini
    # call and builds a Transcript directly from `transcript`.
    transcript_source: Mapped[str] = mapped_column(String(20),
                                                    default="server")
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(default=0)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("SYSUTCDATETIME()"))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True))
