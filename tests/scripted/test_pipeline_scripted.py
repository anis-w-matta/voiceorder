from decimal import Decimal

from app.db import SessionLocal
from app.models import Customer, Item, PendingRequest, VoiceMessage
from app.pipeline import IntakePipeline
from app.schemas.enums import Intent
from app.services.audio_store import AudioStore
from app.schemas.transcript import Transcript
from app.services.scripted.models import (ParsedItemSpan, ParsedPlaceOrder,
                                          ParseError, ParseFailure)


class _ScriptedSTT:
    def __init__(self, text):
        self._text = text

    def transcribe(self, audio_path, duration=None):
        return Transcript(text=self._text, language="en", languages=["en"],
                          duration=3.0, confidence=0.9)


class _StubCommandExtractor:
    """Stands in for Gemini command extraction with a fixed result, so
    pipeline tests stay fast/offline/deterministic - mirrors what the
    (now-removed) anchor-phrase grammar used to extract from the same
    canned transcript."""

    def __init__(self, parsed):
        self._parsed = parsed

    def extract(self, transcript):
        return self._parsed


def _make_voice(session, text):
    vm = VoiceMessage(phone_raw="03000000", phone_e164=None,
                      audio_path="2026/08/14/x.wav", status="received")
    session.add(vm)
    session.flush()
    return vm.id


def test_place_order_transcript_routes_through_scripted_pipeline(
        db_session, monkeypatch):
    # IntakePipeline.process() opens its own session (session_scope), a
    # separate connection from db_session - fixtures need a real commit to
    # be visible to it, so (unlike the rest of this suite) this test must
    # clean up explicitly rather than relying on db_session's rollback.
    import app.pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "duration_seconds", lambda p: 3.0)

    db_session.add(Customer(customer_number="ZZPL1", customer_name="Zzpipeline Trading"))
    db_session.add(Item(item_number="ZZPLI1", item_desc="ZZPIPELINE WIDGET MED 5X2",
                        category="Misc"))
    db_session.commit()

    text = ("place order for Zzpipeline Trading items zzpipeline widget "
           "med 5x2 quantity two cartons the end")
    vid = _make_voice(db_session, text)
    db_session.commit()

    try:
        parsed = ParsedPlaceOrder(customer_text="Zzpipeline Trading", items=[
            ParsedItemSpan(item_text="zzpipeline widget med 5x2",
                           quantity_text="two", uom_text="cartons")])
        pipeline = IntakePipeline(_ScriptedSTT(text), AudioStore(),
                                  _StubCommandExtractor(parsed))
        pipeline.process(vid)

        s = SessionLocal()
        try:
            req = s.query(PendingRequest).filter(
                PendingRequest.voice_message_id == vid).first()
            assert req is not None
            assert req.primary_intent == Intent.add_order.value
            assert req.cust_nb == "ZZPL1"
            assert req.lines[0].item_nb == "ZZPLI1"
            assert req.lines[0].qty == Decimal("2")
        finally:
            s.close()
    finally:
        s = SessionLocal()
        s.query(PendingRequest).filter(
            PendingRequest.voice_message_id == vid).delete(
            synchronize_session=False)
        vm = s.get(VoiceMessage, vid)
        if vm:
            s.delete(vm)
        s.query(Item).filter(Item.item_number == "ZZPLI1").delete(
            synchronize_session=False)
        s.query(Customer).filter(Customer.customer_number == "ZZPL1").delete(
            synchronize_session=False)
        s.commit()
        s.close()


def test_non_scripted_transcript_is_flagged_for_manual_review(
        db_session, monkeypatch):
    """The safety net: a transcript that doesn't match any scripted
    command anchor must never be silently dropped or guessed at - it
    becomes a PendingRequest flagged for manual review."""
    import app.pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "duration_seconds", lambda p: 3.0)

    vid = _make_voice(db_session, "hello how are you")
    db_session.commit()

    try:
        parsed = ParseFailure(ParseError.COMMAND_START_NOT_FOUND,
                              "no command found", "hello how are you")
        pipeline = IntakePipeline(_ScriptedSTT("hello how are you"),
                                  AudioStore(), _StubCommandExtractor(parsed))
        pipeline.process(vid)

        s = SessionLocal()
        try:
            req = s.query(PendingRequest).filter(
                PendingRequest.voice_message_id == vid).first()
            assert req is not None
            assert req.primary_intent == Intent.other.value
            assert "unrecognized_command" in req.flags
            assert req.lines == []
        finally:
            s.close()
    finally:
        s = SessionLocal()
        s.query(PendingRequest).filter(
            PendingRequest.voice_message_id == vid).delete(
            synchronize_session=False)
        vm = s.get(VoiceMessage, vid)
        if vm:
            s.delete(vm)
        s.commit()
        s.close()
