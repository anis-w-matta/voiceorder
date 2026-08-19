"""Seeds the review dashboard (GET /console) with a representative spread
of scripted-command demo requests, so the Queue tab has something to look
at without waiting for real salesman calls.

Every request here is produced by actually running IntakePipeline.process()
against a scripted transcript (place_order / return_order / reorder) - the
same code path a real voice call goes through, with a canned transcript
standing in for Gemini transcription (zero-cost, deterministic) but real
Gemini command classification/extraction (GeminiCommandExtractor) - this
script makes live Gemini API calls, one per scenario below. This exercises
the real GeminiCommandExtractor -> resolve_order -> DraftBuilder pipeline
end to end, not a hand-built PendingRequest.

Run against the same DATABASE_URL the app uses:

    .venv/Scripts/python seed_dashboard_demo.py

Safe to re-run - each call creates a new VoiceMessage/PendingRequest, it
never touches the Item/Customer catalog.
"""
import io
import wave

import app.pipeline as pipeline_module
from app.db import SessionLocal, session_scope
from app.models import PendingRequest, VoiceMessage
from app.pipeline import IntakePipeline
from app.schemas.enums import RequestStatus
from app.schemas.transcript import Transcript
from app.services.audio_store import AudioStore
from app.services.commit import OrderCommitService
from app.services.gemini_command_extractor import GeminiCommandExtractor
from app.services.numbering import OrderNumberService

# duration_seconds() would otherwise try to actually decode the (silent
# placeholder) demo audio - short-circuit it to a fixed value the same way
# the test suite does (see tests/test_queue_review.py), rather than relying
# on av's duration parsing of a near-empty file.
pipeline_module.duration_seconds = lambda path: 4.0

_AUDIO_STORE = AudioStore()
_COMMAND_EXTRACTOR = GeminiCommandExtractor()

CUST_NB = "C001"  # "Test Trading" - seeded by seed.py/seed_test.py


def _placeholder_wav_bytes() -> bytes:
    """~0.3s of silence, real enough that GET /audio/{id} in the console
    actually has something to play instead of 404ing - the old version of
    this script pointed audio_path at a file that was never written."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 4800)
    return buf.getvalue()


class _CannedSTT:
    """Stands in for Gemini transcription with fixed text, so every demo
    request is deterministic and makes zero API calls."""

    def __init__(self, text: str, language: str = "en"):
        self._text = text
        self._language = language

    def transcribe(self, audio_path: str, duration: float | None = None
                  ) -> Transcript:
        languages = ["ar", "en"] if self._language == "ar" else ["en"]
        return Transcript(text=self._text, language=self._language,
                          languages=languages, duration=4.0, confidence=0.92)


def _ingest(text: str, language: str = "en") -> int:
    """Creates a VoiceMessage backed by a real (silent) placeholder audio
    file and runs it through the real pipeline with a canned transcript,
    returning the resulting voice_message_id."""
    audio_path = _AUDIO_STORE.save(_placeholder_wav_bytes(), ext=".wav")
    with session_scope() as s:
        vm = VoiceMessage(phone_raw="70000000", phone_e164=None,
                          audio_path=audio_path, status="received")
        s.add(vm)
        s.flush()
        vid = vm.id

    IntakePipeline(_CannedSTT(text, language), _AUDIO_STORE,
                  _COMMAND_EXTRACTOR).process(vid)
    return vid


def _pending_request_for(vid: int) -> PendingRequest:
    s = SessionLocal()
    try:
        return s.query(PendingRequest).filter(
            PendingRequest.voice_message_id == vid).one()
    finally:
        s.close()


def _commit(req_id: int, order_type: str = "SO") -> str:
    with session_scope() as s:
        svc = OrderCommitService(s, OrderNumberService(s))
        header = svc.commit(req_id, order_type, [], "demo-seed")
        return header.order_nb


def _decide(req_id: int, status: str, note: str):
    with session_scope() as s:
        req = s.get(PendingRequest, req_id)
        req.status = status
        req.decision_note = note
        req.decided_by = "demo-seed"


def main():
    print("Seeding scripted-command demo requests...")

    # 1. Clean place_order, everything resolves - ready to commit.
    clean_vid = _ingest(
        "place order for Test Trading items one medica pull ups large "
        "14x6 quantity ten cartons two medica undrpad 80x60 20x4 "
        "quantity five cartons the end")
    clean_req = _pending_request_for(clean_vid)
    print(f"  [{clean_req.status}] place_order, clean -> request {clean_req.id}")
    order_nb = _commit(clean_req.id)
    print(f"    committed as order {order_nb}")

    # 2. place_order naming a duplicate-description item (real catalog
    #    duplicate: TENDREX ADULT MED 12X4 exists under two item numbers) -
    #    must stay ambiguous, not silently resolved.
    ambiguous_vid = _ingest(
        "place order for Test Trading items one tendrex adult med 12x4 "
        "quantity three cartons the end")
    ambiguous_req = _pending_request_for(ambiguous_vid)
    print(f"  [{ambiguous_req.status}] place_order, ambiguous duplicate "
         f"-> request {ambiguous_req.id} flags={ambiguous_req.flags}")

    # 3. place_order for a customer name that isn't in the database.
    unknown_cust_vid = _ingest(
        "place order for Beirut General Traders items one medica pull "
        "ups large 14x6 quantity two cartons the end")
    unknown_cust_req = _pending_request_for(unknown_cust_vid)
    print(f"  [{unknown_cust_req.status}] place_order, unknown customer "
         f"-> request {unknown_cust_req.id} flags={unknown_cust_req.flags}")
    _decide(unknown_cust_req.id, RequestStatus.rejected.value,
           "customer_not_found: no matching account on file")

    # 4. Full return_order against the order just committed above.
    full_return_vid = _ingest(f"return order {order_nb} the end")
    full_return_req = _pending_request_for(full_return_vid)
    print(f"  [{full_return_req.status}] return_order, full -> request "
         f"{full_return_req.id}")

    # 5. Partial return_order - only one of the two items.
    partial_return_vid = _ingest(
        f"return order {order_nb} item medica pull ups large 14x6 "
        "quantity two cartons the end")
    partial_return_req = _pending_request_for(partial_return_vid)
    print(f"  [{partial_return_req.status}] return_order, partial -> "
         f"request {partial_return_req.id}")
    _decide(partial_return_req.id, RequestStatus.callback.value,
           "confirm partial-return quantity with the customer")

    # 6. Reorder "same as last time" - resolves to the order committed above.
    reorder_vid = _ingest(
        "reorder for Test Trading same order last time the end")
    reorder_req = _pending_request_for(reorder_vid)
    print(f"  [{reorder_req.status}] reorder, last time -> request "
         f"{reorder_req.id} target_order_nb={reorder_req.target_order_nb}")

    # 7. Not a recognized scripted command at all - manual review only.
    unrecognized_vid = _ingest("hi, is this the right number to call?")
    unrecognized_req = _pending_request_for(unrecognized_vid)
    print(f"  [{unrecognized_req.status}] unrecognized transcript -> "
         f"request {unrecognized_req.id} flags={unrecognized_req.flags}")

    # 8. Fully Arabic-script place_order - the whole command grammar, not
    #    just item words, is now bilingual (config.ANCHOR_PHRASES).
    arabic_vid = _ingest(
        "اطلب طلبية لـ Test Trading اصناف واحد medica pull ups large "
        "14x6 كمية عشرة كرتونة النهاية",
        language="ar")
    arabic_req = _pending_request_for(arabic_vid)
    print(f"  [{arabic_req.status}] place_order, Arabic script -> request "
         f"{arabic_req.id}")
    arabic_order_nb = _commit(arabic_req.id)
    print(f"    committed as order {arabic_order_nb}")

    # 9. Fully Arabic-script return_order against the order just above.
    arabic_return_vid = _ingest(
        f"رجاع طلبية {arabic_order_nb} النهاية", language="ar")
    arabic_return_req = _pending_request_for(arabic_return_vid)
    print(f"  [{arabic_return_req.status}] return_order, Arabic script -> "
         f"request {arabic_return_req.id}")

    # 10. Fully Arabic-script reorder ("same order, last time").
    arabic_reorder_vid = _ingest(
        "اعادة طلبية لـ Test Trading نفس الطلبية آخر مرة النهاية",
        language="ar")
    arabic_reorder_req = _pending_request_for(arabic_reorder_vid)
    print(f"  [{arabic_reorder_req.status}] reorder, Arabic script -> "
         f"request {arabic_reorder_req.id} "
         f"target_order_nb={arabic_reorder_req.target_order_nb}")

    # 11. Known limitation, shown deliberately: English grammar with the
    #     customer name spoken in Arabic script. Customer matching only
    #     fuzzy-compares against Latin-script names in the database, so
    #     this correctly (not a bug) comes back customer_not_found rather
    #     than silently guessing which Latin-script customer was meant.
    arabic_customer_vid = _ingest(
        "place order for تست تريدينغ items one tendrex adult kbeer 12x4 "
        "quantity khamse cartons the end",
        language="ar")
    arabic_customer_req = _pending_request_for(arabic_customer_vid)
    print(f"  [{arabic_customer_req.status}] place_order, Arabic-script "
         f"customer name (known gap) -> request {arabic_customer_req.id} "
         f"flags={arabic_customer_req.flags}")
    _decide(arabic_customer_req.id, RequestStatus.rejected.value,
           "customer name was spoken in Arabic script - database only has "
           "a Latin-script name on file for this account")

    # 12. Numeric pack-code conflict: the spoken pack code (99x9) doesn't
    #     match any real catalog variant's pack code, even though the rest
    #     of the text is a near-perfect text match - must not be silently
    #     accepted on text similarity alone.
    numeric_conflict_vid = _ingest(
        "place order for Test Trading items one tendrex adult med 99x9 "
        "quantity two cartons the end")
    numeric_conflict_req = _pending_request_for(numeric_conflict_vid)
    print(f"  [{numeric_conflict_req.status}] place_order, numeric pack "
         f"conflict -> request {numeric_conflict_req.id}")
    _decide(numeric_conflict_req.id, RequestStatus.callback.value,
           "spoken pack code (99x9) does not match any real variant - "
           "confirm the correct pack size with the customer")

    # 13. ASR noise in the command start ("order fer" instead of "order
    #     for") - the fuzzy anchor matcher should absorb this and resolve
    #     cleanly anyway, same as a clean transcript would.
    asr_noise_vid = _ingest(
        "place order fer Test Trading items one medica undrpad 80x60 "
        "20x4 quantity four cartons the end")
    asr_noise_req = _pending_request_for(asr_noise_vid)
    print(f"  [{asr_noise_req.status}] place_order, ASR noise in command "
         f"start -> request {asr_noise_req.id} flags={asr_noise_req.flags}")

    # 14. Missing "items" delimiter - a different, more specific parse
    #     failure than case 7's "no command recognized at all".
    missing_delimiter_vid = _ingest(
        "place order for Test Trading tendrex adult med 12x4 quantity "
        "two cartons the end")
    missing_delimiter_req = _pending_request_for(missing_delimiter_vid)
    print(f"  [{missing_delimiter_req.status}] place_order, missing "
         f"'items' delimiter -> request {missing_delimiter_req.id} "
         f"flags={missing_delimiter_req.flags}")

    print("\nDone. Open /console to review the queue.")


if __name__ == "__main__":
    main()
