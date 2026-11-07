"""Voice authorization, bounded processing and real optional offline recognition."""
import io
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from backend.app import speech
from backend.app.models import Membership
from backend.tests.test_auth_api import client as client, key as key


@pytest.mark.parametrize("path", ["/v1/voice/capabilities", "/v1/voice/transcribe"])
def test_voice_requires_verified_login(client, path):
    client.headers.pop("Authorization")
    response = client.post(path, content=b"invalid") if path.endswith("transcribe") else client.get(path)
    assert response.status_code == 401


def test_voice_membership_revocation_and_tenant_forgery(client, env):
    client.headers["X-Tenant-ID"] = env.other_tenant
    assert client.post("/v1/voice/transcribe", content=b"x").status_code == 403
    client.headers["X-Tenant-ID"] = env.tenant
    membership = env.db.scalar(select(Membership).where(Membership.user_id == env.user, Membership.tenant_id == env.tenant))
    membership.active = False
    env.db.commit()
    assert client.get("/v1/voice/capabilities").status_code == 403


def test_audio_bounds_and_language_validation_before_inference(client, monkeypatch):
    monkeypatch.setattr(speech, "transcribe", lambda *_: pytest.fail("Oversized upload reached recognizer"))
    assert client.post("/v1/voice/transcribe", content=b"x"*(speech.MAX_AUDIO_BYTES+1)).status_code == 413
    assert client.post("/v1/voice/transcribe?language=unknown", content=b"x").status_code == 422


def test_failed_attempts_are_rate_limited(client, monkeypatch):
    def fail(*_args):
        raise HTTPException(503, "Test-only model unavailable")
    monkeypatch.setattr(speech, "transcribe", fail)
    assert [client.post("/v1/voice/transcribe", content=b"x").status_code for _ in range(6)] == [503]*6
    response = client.post("/v1/voice/transcribe", content=b"x")
    assert response.status_code == 429


@pytest.mark.parametrize("failure", ["timeout", "crash", "json"])
def test_recognition_failure_returns_safe_error_and_releases_slot(monkeypatch, failure):
    monkeypatch.setattr(speech, "capabilities", lambda: {"local_stt_ready": True})
    def run(*_args, **_kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("local speech", 45)
        return SimpleNamespace(returncode=1 if failure == "crash" else 0, stdout="invalid")
    monkeypatch.setattr(speech.subprocess, "run", run)
    for _ in range(3):
        with pytest.raises(HTTPException) as exc:
            speech.transcribe(b"RIFFtest")
        assert exc.value.status_code == (504 if failure == "timeout" else 503)


def test_recognition_backpressure(monkeypatch):
    monkeypatch.setattr(speech, "capabilities", lambda: {"local_stt_ready": True})
    assert speech.SLOTS.acquire(False) and speech.SLOTS.acquire(False)
    try:
        with pytest.raises(HTTPException) as exc:
            speech.transcribe(b"RIFFtest")
        assert exc.value.status_code == 429
    finally:
        speech.SLOTS.release()
        speech.SLOTS.release()


def test_real_silent_audio_is_rejected_without_hallucinated_text():
    pytest.importorskip("faster_whisper", reason="Optional local speech dependencies are not installed.")
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0"*32000)
    with pytest.raises(ValueError, match="No audible"):
        speech.infer(stream.getvalue(), "unused", "auto")


def test_real_offline_english_speech():
    fixture = Path(__file__).parent / "fixtures/speech-question.wav"
    if not speech.capabilities()["local_stt_ready"] or not fixture.exists():
        pytest.skip("Optional local Whisper-tiny assets/dependencies or synthetic WAV fixture unavailable.")
    output = speech.transcribe(fixture.read_bytes(), "en")
    assert "next class" in output["text"].lower()
    assert output["language"] == "en" and not output["raw_audio_persisted"]
