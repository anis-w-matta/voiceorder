import os
import tempfile
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_audio, get_db, get_operator, get_phone
from app.models import PendingRequest, VoiceMessage
from app.services.activity_log import log as log_activity
from app.services.audio_formats import AUDIO_MIME_BY_EXT
from app.services.gemini_transcriber import GeminiTranscriber

router = APIRouter(tags=["ingest"])
MAX_BYTES = 25 * 1024 * 1024
# Constructed once at import time, same pattern as app/worker.py's
# module-level `stt = GeminiTranscriber()` - avoids re-creating the genai
# client (and its api_key lookup) on every preview request.
_transcriber = GeminiTranscriber()

# The stored extension comes straight off the uploaded filename, so it has to
# be constrained: it previously accepted anything the client sent, including
# ".php"/".html", separators such as ".\evil" (which made AudioStore create
# nested directories), and null-byte tricks like "a.wav\x00.php". Sourced
# from AUDIO_MIME_BY_EXT (not a second hand-maintained literal) so this list
# and gemini_transcriber.py's MIME lookup can't silently diverge.
ALLOWED_EXT = set(AUDIO_MIME_BY_EXT)
DEFAULT_EXT = ".ogg"


def safe_ext(filename: str | None) -> str:
    ext = PurePosixPath((filename or "").replace("\\", "/")).suffix
    ext = ext.split("\x00")[0].strip().lower()
    return ext if ext in ALLOWED_EXT else DEFAULT_EXT


@router.post("/ingest/voice", status_code=202)
def ingest_voice(phone: str = Form(...), audio: UploadFile = File(...),
                 transcript: str | None = Form(default=None),
                 language: str | None = Form(default=None),
                 # "accept" (default) or "draft" - which button the
                 # salesman tapped on the Record screen (RecordViewModel).
                 # Carried into the voice_received log's details so the
                 # LOG QUERY screen can show a submission the salesman
                 # deliberately left for later, distinct from one they
                 # opened straight away.
                 submit_mode: str | None = Form(default=None),
                 s: Session = Depends(get_db), store=Depends(get_audio),
                 phones=Depends(get_phone),
                 operator: str = Depends(get_operator)):
    data = audio.file.read()
    if not data:
        raise HTTPException(400, "empty audio")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "audio too large")
    rel = store.save(data, safe_ext(audio.filename))
    vm = VoiceMessage(phone_raw=phone, phone_e164=phones.to_e164(phone),
                      audio_path=rel, status="received")
    # Audio is always stored either way (archival/audit trail). A caller
    # that already has a transcript (the Android app's Gemini preview +
    # salesman-edited text, see RecordViewModel.kt) can skip the
    # server-side Gemini call entirely by supplying the text here - see the
    # transcript_source branch in app/pipeline.py.
    if transcript and transcript.strip():
        vm.transcript = transcript.strip()
        vm.transcript_source = "client_whisper"
        if language and language.strip():
            vm.language = language.strip()
    s.add(vm)
    s.flush()
    log_activity(s, "voice_received", f"voice message {vm.id} received",
                voice_message_id=vm.id,
                details={"operator": operator,
                        "submit_mode": submit_mode or "accept"})
    return {"id": vm.id, "status": "received"}


@router.post("/ingest/transcribe-preview")
def transcribe_preview(audio: UploadFile = File(...),
                       operator: str = Depends(get_operator)):
    """Synchronous Gemini transcription with no side effects - doesn't
    create a VoiceMessage/PendingRequest, doesn't touch the worker queue.
    Lets the Android app show the transcript for review/editing before the
    salesman commits to submitting it: the (possibly hand-edited) text then
    goes to POST /ingest/voice's `transcript` field, which the pipeline
    treats as already-transcribed and skips a second Gemini call for.
    Requires a logged-in salesman (like every other ingest/queue/review
    endpoint) so an unauthenticated caller can't run up the Gemini bill."""
    data = audio.file.read()
    if not data:
        raise HTTPException(400, "empty audio")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "audio too large")
    ext = safe_ext(audio.filename)
    fd, tmp_path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        tr = _transcriber.transcribe(tmp_path)
    finally:
        os.unlink(tmp_path)
    return {"transcript": tr.text, "language": tr.language}


@router.get("/ingest/voice/{voice_id}")
def voice_status(voice_id: int, s: Session = Depends(get_db)):
    """Lightweight polling target for a client that just uploaded audio and
    wants to know when the worker (app/worker.py) has picked it up and
    finished processing - status moves received -> transcribing ->
    transcribed -> classified -> drafted (or failed/too_long)."""
    vm = s.get(VoiceMessage, voice_id)
    if not vm:
        raise HTTPException(404)
    req = s.scalars(select(PendingRequest)
                    .where(PendingRequest.voice_message_id == voice_id)
                    ).first()
    return {"id": vm.id, "status": vm.status, "error": vm.error,
           "transcript": vm.transcript, "language": vm.language,
           "request_id": req.id if req else None}
