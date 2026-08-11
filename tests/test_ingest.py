import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import get_audio
from app.db import SessionLocal
from app.main import app
from app.models import VoiceMessage
from app.services.audio_store import AudioStore

client = TestClient(app)


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
                                       "audio/wav")})
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
                       files={"audio": ("call.wav", io.BytesIO(b""), "audio/wav")})
    assert resp.status_code == 400


def test_ingest_voice_too_large_413(tmp_audio_store):
    big = b"0" * (25 * 1024 * 1024 + 1)
    resp = client.post("/ingest/voice",
                       data={"phone": "03123456"},
                       files={"audio": ("call.wav", io.BytesIO(big), "audio/wav")})
    assert resp.status_code == 413


def test_ingest_voice_disallowed_extension_falls_back_to_default(tmp_audio_store):
    resp = client.post("/ingest/voice",
                       data={"phone": "03123456"},
                       files={"audio": ("call.exe", io.BytesIO(b"data"),
                                       "application/octet-stream")})
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
                       files={"audio": ("call.wav", io.BytesIO(b"data"), "audio/wav")})
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
                       files={"audio": ("call.wav", io.BytesIO(b"data"), "audio/wav")})
    assert resp.status_code == 422
