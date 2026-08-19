from datetime import datetime, timezone
from decimal import Decimal

from app.config import settings
from app.db import session_scope
from app.errors import AudioReadFailed, TranscriptionFailed, VoiceMessageNotFound
from app.models import PendingRequest, VoiceMessage
from app.schemas.enums import Intent
from app.schemas.transcript import Transcript
from app.services.activity_log import log as log_activity
from app.services.audio_duration import duration_seconds
from app.services.catalogue import CatalogueService
from app.services.draft_builder import DraftBuilder
from app.services.normalization import normalize_text
from app.services.prior_order import PriorOrderService
from app.services.scripted import command_parser, resolve_order
from app.services.scripted.models import ParseFailure


class IntakePipeline:
    def __init__(self, stt, audio):
        self.stt = stt
        self.audio = audio

    def process(self, voice_message_id: int) -> None:
        with session_scope() as s:
            voice = s.get(VoiceMessage, voice_message_id)
            if voice is None:
                raise VoiceMessageNotFound(voice_message_id)

            audio_path = self.audio.absolute(voice.audio_path)
            try:
                duration = duration_seconds(audio_path)
            except Exception as e:
                raise AudioReadFailed(f"could not read audio duration: {e}") from e
            if duration > settings.max_audio_seconds:
                voice.duration_sec = Decimal(str(round(duration, 2)))
                voice.status = "too_long"
                voice.processed_at = datetime.now(timezone.utc)
                s.flush()
                req = PendingRequest(
                    voice_message_id=voice.id, intents=[],
                    primary_intent=Intent.other.value,
                    flags=["audio_too_long"], status="new")
                s.add(req)
                s.flush()
                log_activity(
                    s, "audio_too_long",
                    f"voice message {voice.id} is {duration:.0f}s, over the "
                    f"{settings.max_audio_seconds}s limit - skipped "
                    "transcription/classification", level="warn",
                    voice_message_id=voice.id)
                return

            if voice.transcript_source == "client_whisper" and voice.transcript:
                # The Android app already transcribed on-device with
                # whisper.cpp (app/api/ingest.py) - build the same Transcript
                # shape the Gemini path produces, skipping the network call
                # entirely, rather than re-transcribing audio that was
                # already turned into text once.
                text = voice.transcript
                tr = Transcript(
                    text=text, normalized_transcript=normalize_text(text),
                    quality="good", disagreement=False, attempts=[],
                    language=voice.language or "unknown",
                    languages=[voice.language] if voice.language else [],
                    duration=duration, confidence=1.0, segments=[])
            else:
                try:
                    # Pass the duration already computed above for the
                    # too-long check, so transcribe() doesn't decode the
                    # same file a second time just to re-derive the same
                    # number.
                    tr = self.stt.transcribe(audio_path, duration=duration)
                except Exception as e:
                    raise TranscriptionFailed(
                        f"transcription failed: {e}") from e
            voice.transcript = tr.text
            voice.normalized_transcript = tr.normalized_transcript
            voice.transcript_quality = tr.quality
            voice.transcription_disagreement = tr.disagreement
            voice.transcript_attempts = tr.attempts
            voice.transcript_conf = tr.confidence
            voice.language = tr.language
            voice.languages = tr.languages
            voice.segments = [sg.model_dump() for sg in tr.segments]
            voice.duration_sec = Decimal(str(round(tr.duration, 2)))
            voice.status = "transcribed"
            s.flush()

            prior = PriorOrderService(s)
            builder = DraftBuilder(s, prior, CatalogueService(s))

            parsed = command_parser.parse(tr.text)
            if not isinstance(parsed, ParseFailure):
                result = resolve_order.resolve(s, parsed)
                if result.command_type == "place_order":
                    builder.build_scripted_order(voice, result)
                elif result.command_type == "return_order":
                    order_header = (
                        prior.find_by_order_nb(result.order_reference)
                        if result.order_reference else None)
                    builder.build_return(voice, order_header, result)
                else:  # reorder
                    builder.build_reorder(voice, result)
            else:
                # Every supported command is scripted (place/return/reorder)
                # - a transcript that doesn't match one of those anchors has
                # no other extraction path. Record it for manual review
                # rather than silently discarding it or guessing at intent.
                req = PendingRequest(
                    voice_message_id=voice.id, intents=[],
                    primary_intent=Intent.other.value,
                    raw_model_output={"scripted": True,
                                      "parse_error": parsed.error.value,
                                      "detail": parsed.detail},
                    flags=["unrecognized_command", parsed.error.value],
                    status="new")
                s.add(req)
                s.flush()
                log_activity(
                    s, "unrecognized_command",
                    f"voice message {voice.id} did not match a scripted "
                    f"command ({parsed.error.value}): {parsed.detail}",
                    level="warn", voice_message_id=voice.id)

            voice.status = "drafted"
            voice.processed_at = datetime.now(timezone.utc)
