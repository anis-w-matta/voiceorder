import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_audio
from app.db import SessionLocal, session_scope
from app.main import app
from app.models import Salesman, VoiceMessage
from app.services.audio_store import AudioStore
from app.services.auth import create_token, hash_password

client = TestClient(app)


def _ensure_salesman(login_id, name):
    with session_scope() as s:
        sm = s.get(Salesman, login_id)
        if sm is None:
            s.add(Salesman(login_id=login_id,
                           password_hash=hash_password("testpass123"),
                           name=name, email=f"{login_id}@example.com"))
        else:
            sm.is_active = True


# /ingest/voice and /ingest/transcribe-preview require a logged-in
# salesman (get_operator, app/api/deps.py) same as queue/review - see
# test_ingest_voice_missing_bearer_token_401 below for the negative case.
_ensure_salesman("carol", "Carol")
AUTH = {"Authorization": f"Bearer {create_token('carol')}"}


@pytest.fixture
def tmp_audio_store(tmp_path):
    # ingest_voice writes real files - point AudioStore at a throwaway
    # directory instead of the real C:/voiceorder/audio.
    store = AudioStore(root=str(tmp_path))
    app.dependency_overrides[get_audio] = lambda: store
    yield store
    del app.dependency_overrides[get_audio]


def _cleanup_voice_message(vm_id):
    s = SessionLocal()
    try:
        vm = s.get(VoiceMessage, vm_id)
        if vm:
            s.delete(vm)
            s.commit()
    finally:
        s.close()


def test_ingest_voice_success(tmp_audio_store):
    resp = client.post("/ingest/voice",
                       data={"phone": "03123456"},
                       files={"audio": ("call.wav", io.BytesIO(b"fake-audio-bytes"),
                                       "audio/wav")},
                       headers=AUTH)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "received"
    try:
        s = SessionLocal()
        try:
            vm = s.get(VoiceMessage, body["id"])
            assert vm.phone_e164 == "+9613123456"
            assert vm.audio_path.endswith(".wav")
        finally:
            s.close()
    finally:
        _cleanup_voice_message(body["id"])


def test_ingest_voice_empty_audio_400(tmp_audio_store):
    resp = client.post("/ingest/voice",
                       data={"phone": "03123456"},
                       files={"audio": ("call.wav", io.BytesIO(b""), "audio/wav")},
                       headers=AUTH)
    assert resp.status_code == 400


def test_ingest_voice_too_large_413(tmp_audio_store):
    big = b"0" * (25 * 1024 * 1024 + 1)
    resp = client.post("/ingest/voice",
                       data={"phone": "03123456"},
                       files={"audio": ("call.wav", io.BytesIO(big), "audio/wav")},
                       headers=AUTH)
    assert resp.status_code == 413


def test_ingest_voice_disallowed_extension_falls_back_to_default(tmp_audio_store):
    resp = client.post("/ingest/voice",
                       data={"phone": "03123456"},
                       files={"audio": ("call.exe", io.BytesIO(b"data"),
                                       "application/octet-stream")},
                       headers=AUTH)
    assert resp.status_code == 202
    body = resp.json()
    try:
        s = SessionLocal()
        try:
            vm = s.get(VoiceMessage, body["id"])
            assert vm.audio_path.endswith(".ogg")
        finally:
            s.close()
    finally:
        _cleanup_voice_message(body["id"])


def test_ingest_voice_unparseable_phone_still_accepted(tmp_audio_store):
    # phone_e164 stays null rather than blocking intake - a caller with a
    # garbled/foreign number should still get a voice message recorded for
    # manual follow-up instead of a hard failure.
    resp = client.post("/ingest/voice",
                       data={"phone": "not-a-phone-number"},
                       files={"audio": ("call.wav", io.BytesIO(b"data"), "audio/wav")},
                       headers=AUTH)
    assert resp.status_code == 202
    body = resp.json()
    try:
        s = SessionLocal()
        try:
            vm = s.get(VoiceMessage, body["id"])
            assert vm.phone_e164 is None
            assert vm.phone_raw == "not-a-phone-number"
        finally:
            s.close()
    finally:
        _cleanup_voice_message(body["id"])


def test_ingest_voice_missing_phone_field_422(tmp_audio_store):
    resp = client.post("/ingest/voice",
                       files={"audio": ("call.wav", io.BytesIO(b"data"), "audio/wav")},
                       headers=AUTH)
    assert resp.status_code == 422


def test_ingest_voice_missing_bearer_token_401(tmp_audio_store):
    resp = client.post("/ingest/voice",
                       data={"phone": "03123456"},
                       files={"audio": ("call.wav", io.BytesIO(b"data"), "audio/wav")})
    assert resp.status_code == 401


def test_transcribe_preview_missing_bearer_token_401():
    resp = client.post("/ingest/transcribe-preview",
                       files={"audio": ("call.wav", io.BytesIO(b"data"), "audio/wav")})
    assert resp.status_code == 401
