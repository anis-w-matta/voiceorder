from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_audio, get_db, get_phone
from app.models import VoiceMessage
from app.services.activity_log import log as log_activity

router = APIRouter(tags=["ingest"])
MAX_BYTES = 25 * 1024 * 1024

# The stored extension comes straight off the uploaded filename, so it has to
# be constrained: it previously accepted anything the client sent, including
# ".php"/".html", separators such as ".\evil" (which made AudioStore create
# nested directories), and null-byte tricks like "a.wav\x00.php".
ALLOWED_EXT = {".ogg", ".oga", ".opus", ".mp3", ".m4a", ".mp4", ".aac",
               ".wav", ".webm", ".amr", ".3gp", ".flac"}
DEFAULT_EXT = ".ogg"


def safe_ext(filename: str | None) -> str:
    ext = PurePosixPath((filename or "").replace("\\", "/")).suffix
    ext = ext.split("\x00")[0].strip().lower()
    return ext if ext in ALLOWED_EXT else DEFAULT_EXT


@router.post("/ingest/voice", status_code=202)
def ingest_voice(phone: str = Form(...), audio: UploadFile = File(...),
                 s: Session = Depends(get_db), store=Depends(get_audio),
                 phones=Depends(get_phone)):
    data = audio.file.read()
    if not data:
        raise HTTPException(400, "empty audio")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "audio too large")
    rel = store.save(data, safe_ext(audio.filename))
    vm = VoiceMessage(phone_raw=phone, phone_e164=phones.to_e164(phone),
                      audio_path=rel, status="received")
    s.add(vm)
    s.flush()
    log_activity(s, "voice_received", f"voice message {vm.id} received",
                voice_message_id=vm.id)
    return {"id": vm.id, "status": "received"}
