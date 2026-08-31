"""Seeds the review dashboard (Android app's queue) with scripted
place_order demo requests for customer 58466 (Economena Analytics sarl) -
2 per QRA scenario, each once in English and once in Latin-script
(romanized) Arabic. Currently: baseline (no QRA) and type P (corrected to
a blanket price override, no item reference) - see SCENARIOS below for why
type T/B aren't re-seeded here.

Commands reference the customer by number ("58466"), not name - the real
catalog has a near-duplicate ("Economena Analytics SAL", 580163, 97.9%
fuzzy match) that makes the name itself genuinely ambiguous
(match_customer's tie-safety policy correctly refuses to guess between
them), so the number is the only way to resolve unambiguously by scripted
command here.

Requires seed_qra_demo's QRA agreement already set up for 58466 (see the
one-off setup this was run alongside: qra_header + 3 qra_detail rows for
types P/T/B). QRA itself only applies at commit time (app/services/
qra_engine.py) - these requests are deliberately left uncommitted, so
they show up as pending review items in the dashboard for a human to
accept themselves.

Same real pipeline as seed_dashboard_demo.py: IntakePipeline.process()
with a canned transcript (zero-cost, deterministic) but real Gemini
command classification/extraction - this script makes live Gemini API
calls, one per scenario.

Run against the same DATABASE_URL the app uses:

    .venv/Scripts/python seed_qra_demo.py
"""
import io
import wave

from sqlalchemy.orm import selectinload

import app.pipeline as pipeline_module
from app.db import SessionLocal, session_scope
from app.models import PendingRequest, VoiceMessage
from app.pipeline import IntakePipeline
from app.schemas.transcript import Transcript
from app.services.audio_store import AudioStore
from app.services.gemini_command_extractor import GeminiCommandExtractor
from app.services.qra_engine import preview_qra

pipeline_module.duration_seconds = lambda path: 4.0

_AUDIO_STORE = AudioStore()
_COMMAND_EXTRACTOR = GeminiCommandExtractor()

CUST_NB = "58466"
CUST_NAME = "Economena Analytics sarl"


def _placeholder_wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 4800)
    return buf.getvalue()


class _CannedSTT:
    def __init__(self, text: str, language: str):
        self._text = text
        self._language = language

    def transcribe(self, audio_path: str, duration: float | None = None
                  ) -> Transcript:
        languages = ["ar", "en"] if self._language == "ar" else ["en"]
        return Transcript(text=self._text, language=self._language,
                          languages=languages, duration=4.0, confidence=0.92)


def _ingest(text: str, language: str) -> int:
    audio_path = _AUDIO_STORE.save(_placeholder_wav_bytes(), ext=".wav")
    with session_scope() as s:
        vm = VoiceMessage(phone_raw="70000000", audio_path=audio_path,
                          status="received")
        s.add(vm)
        s.flush()
        vid = vm.id

    IntakePipeline(_CannedSTT(text, language), _AUDIO_STORE,
                  _COMMAND_EXTRACTOR).process(vid)
    return vid


def _pending_request_for(vid: int) -> PendingRequest:
    s = SessionLocal()
    try:
        req = s.query(PendingRequest).options(selectinload(
            PendingRequest.lines)).filter(
            PendingRequest.voice_message_id == vid).one()
        s.expunge(req)
        return req
    finally:
        s.close()


# language="ar" tells the pipeline the transcript is Arabic-family (even
# though it's Latin-script here, not Arabic script) - same signal
# seed_dashboard_demo.py's Arabic-script cases use, so bilingual command
# grammar handling (config.ANCHOR_PHRASES) applies the same way.

# uom_text must canonicalize via app/services/quantity_uom.py's
# UOM_SYNONYMS, which (per gemini_command_extractor.py's prompt) only
# recognizes "each"/"packets" - this business's only two real units.
# "cartons" is NOT in that table and never canonicalizes, so it must not be
# used here even though older docs (seed_dashboard_demo.py) used it. No
# Arabizi word for "packets" is registered yet either (deliberate TODO in
# scripted/config.py - never guess it), so the Latin-Arabic sentences below
# keep "packets" in English, the same way seed_dashboard_demo.py's
# Arabic-script cases keep item names in English/Latin text.

# NOTE: type T and type B are not re-seeded here - they were already
# demonstrated (and Accepted, for real, by the user on their own device)
# as orders 260000098-260000101. Only baseline and the corrected type P
# (see below) are redone this run.
#
# Type P now has NO item reference at all (blanket price override on any
# line reaching qty_buy=2 in PKT, whichever item it is - see
# app/services/qra_engine.py) - so "baseline/no QRA" only stays QRA-free by
# staying BELOW that threshold (qty=1). Ordering qty>=2 of anything for
# this customer now triggers the P override, which is exactly what the
# type P scenario below demonstrates.
SCENARIOS = [
    ("baseline (no QRA - qty below P's threshold)",
     "place order for 58466 items one club lid pp 12oz wh 50x20 "
     "quantity one packets the end", "en"),
    ("baseline (no QRA - qty below P's threshold)",
     "badde eftah talbiye la 58466 sinf wahad club lid pp 12oz "
     "wh 50x20 kamiye wahad packets khalas", "ar"),

    ("type P (blanket price override, qty_buy=2, any item)",
     "place order for 58466 items one club lid pp 12oz wh 50x20 "
     "quantity three packets the end", "en"),
    ("type P (blanket price override, qty_buy=2, any item)",
     "badde eftah talbiye la 58466 sinf wahad club lid pp 12oz "
     "wh 50x20 kamiye tlete packets khalas", "ar"),
]


def main():
    print(f"Seeding QRA demo requests for {CUST_NAME} ({CUST_NB})...\n")
    for label, text, language in SCENARIOS:
        vid = _ingest(text, language)
        req = _pending_request_for(vid)
        lines_desc = ", ".join(
            f"{l.item_nb or '???'} x{l.qty}{l.uom or '?'}" for l in req.lines
        ) or "(none)"
        print(f"[{label} / {language}] request {req.id} "
             f"status={req.status} flags={req.flags} lines=[{lines_desc}]")
        with session_scope() as s:
            db_req = s.get(PendingRequest, req.id)
            previews, bonuses = preview_qra(s, db_req.cust_nb, db_req.lines)
            for p in previews:
                print(f"    QRA preview line {p.line_nb}: price={p.unit_price} "
                     f"free={p.is_free} substitutes_to={p.substituted_item_nb}")
            for b in bonuses:
                print(f"    QRA preview bonus: {b.item_nb} x{b.qty}{b.uom}")
    print("\nDone - all left uncommitted in the queue. Open the Android "
         "app's dashboard (or GET /queue) to review/accept them.")


if __name__ == "__main__":
    main()
