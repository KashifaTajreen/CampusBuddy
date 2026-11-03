import io
import wave
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from backend.app import speech
from backend.app.auth import actor, get_session
from backend.app.config import settings
from backend.app.main import app
from backend.app.speech import MAX_AUDIO_BYTES, SpeechUnavailable, transcribe


def silence(seconds=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000 * seconds)
    return buffer.getvalue()


class FakeModel:
    def __init__(self, text="What is my next class?", duration=2.0):
        self.text = text
        self.duration = duration
        self.options = None

    def transcribe(self, audio, **options):
        self.options = options
        return iter([SimpleNamespace(text=f" {self.text} ")]), SimpleNamespace(
            language="en", duration=self.duration
        )


@pytest.fixture
def model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(speech, "speech_model", lambda: fake)
    return fake


@pytest.fixture
def client(env):
    def session():
        yield env.db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[actor] = lambda: env.service.actor
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_transcribe_returns_trimmed_text(model):
    assert transcribe(silence()) == {"text": "What is my next class?", "language": "en", "duration": 2.0}
    assert model.options["language"] == "en"


def test_auto_language_lets_the_model_detect(model, monkeypatch):
    monkeypatch.setattr(settings(), "speech_language", "auto")
    transcribe(silence())
    assert model.options["language"] is None


def test_empty_and_oversized_audio_are_rejected(model):
    with pytest.raises(ValueError, match="empty"):
        transcribe(b"")
    with pytest.raises(ValueError, match="10 MB"):
        transcribe(b"x" * (MAX_AUDIO_BYTES + 1))


def test_recording_over_one_minute_is_rejected(monkeypatch):
    monkeypatch.setattr(speech, "speech_model", lambda: FakeModel(duration=120))
    with pytest.raises(ValueError, match="one minute"):
        transcribe(silence())


def test_missing_model_reports_setup_state(monkeypatch):
    speech.speech_model.cache_clear()
    monkeypatch.setattr(settings(), "speech_model", "data/models/does-not-exist")
    try:
        with pytest.raises(SpeechUnavailable, match="not set up"):
            transcribe(silence())
    finally:
        speech.speech_model.cache_clear()


def test_busy_server_reports_retry(model, monkeypatch):
    monkeypatch.setattr(speech, "BUSY_TIMEOUT_SECONDS", 0)
    acquired = [speech._slots.acquire(), speech._slots.acquire()]
    try:
        with pytest.raises(SpeechUnavailable, match="busy"):
            transcribe(silence())
    finally:
        for _ in acquired:
            speech._slots.release()


def test_slot_is_released_after_a_decoder_failure(monkeypatch):
    class Broken:
        def transcribe(self, audio, **options):
            raise OSError("invalid data")

    monkeypatch.setattr(speech, "speech_model", lambda: Broken())
    for _ in range(3):
        with pytest.raises(ValueError, match="could not be read"):
            transcribe(b"not audio")


def test_decoder_value_error_text_is_not_shown_to_the_user(monkeypatch):
    class Broken:
        def transcribe(self, audio, **options):
            raise ValueError("[Errno 1094995529] Invalid data found when processing input")

    monkeypatch.setattr(speech, "speech_model", lambda: Broken())
    with pytest.raises(ValueError, match="could not be read"):
        transcribe(b"not audio")


def test_endpoint_requires_a_verified_session():
    assert TestClient(app).post(
        "/v1/speech/transcribe", files={"file": ("a.wav", silence())}
    ).status_code in (
        401,
        403,
    )


def test_endpoint_returns_text(client, model):
    response = client.post("/v1/speech/transcribe", files={"file": ("a.wav", silence())})
    assert response.status_code == 200
    assert response.json()["text"] == "What is my next class?"


def test_endpoint_reports_unavailable_model(client, monkeypatch):
    def missing():
        raise SpeechUnavailable("Voice input is not set up on this server. Type your question instead.")

    monkeypatch.setattr(speech, "speech_model", missing)
    response = client.post("/v1/speech/transcribe", files={"file": ("a.wav", silence())})
    assert response.status_code == 503
    assert "not set up" in response.json()["detail"]


def test_endpoint_rate_limits_each_user(client, model):
    responses = [
        client.post("/v1/speech/transcribe", files={"file": ("a.wav", silence())}) for _ in range(21)
    ]
    assert responses[19].status_code == 200
    assert responses[20].status_code == 429
