"""Local speech-to-text. Audio is transcribed in memory and is never stored or logged."""

import io
import threading
from functools import lru_cache
from pathlib import Path
from backend.app.config import ROOT, settings

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_AUDIO_SECONDS = 65  # The browser stops recording at 60 seconds; this leaves room for rounding.
BUSY_TIMEOUT_SECONDS = 20
VOCABULARY = "CampusOS, timetable, canteen, faculty, semester, internal assessment, circular, hostel."
# Transcription is CPU-heavy, so requests wait in line rather than overloading the server.
_slots = threading.BoundedSemaphore(2)


class SpeechUnavailable(RuntimeError):
    """Voice input is not configured, or the server is too busy to take this recording."""


@lru_cache(maxsize=1)
def speech_model():
    path = Path(settings().speech_model)
    path = path if path.is_absolute() else ROOT / path
    if not path.is_dir():
        raise SpeechUnavailable("Voice input is not set up on this server. Type your question instead.")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SpeechUnavailable(
            "Voice input is not set up on this server. Type your question instead."
        ) from exc
    # CPU keeps the GPU's memory available for the LLM.
    return WhisperModel(str(path), device="cpu", compute_type="int8", local_files_only=True)


def transcribe(content):
    if not content:
        raise ValueError("The recording was empty. Try again.")
    if len(content) > MAX_AUDIO_BYTES:
        raise ValueError("Keep recordings under 10 MB. Try a shorter question.")
    model = speech_model()
    if not _slots.acquire(timeout=BUSY_TIMEOUT_SECONDS):
        raise SpeechUnavailable("Voice input is busy right now. Try again in a moment.")
    try:
        language = settings().speech_language
        segments, info = model.transcribe(
            io.BytesIO(content),
            language=None if language == "auto" else language,
            initial_prompt=VOCABULARY,
            vad_filter=True,
        )
        too_long = info.duration > MAX_AUDIO_SECONDS
        # Segments are generated lazily, so decoding happens while this slot is held.
        text = "" if too_long else " ".join(segment.text.strip() for segment in segments).strip()
    except Exception as exc:
        # The decoder raises several library-specific errors for unusable audio, some of them
        # ValueErrors whose raw text must not reach the client.
        raise ValueError("That recording could not be read. Try again.") from exc
    finally:
        _slots.release()
    if too_long:
        raise ValueError("Keep recordings to one minute. Try a shorter question.")
    return dict(text=text, language=info.language, duration=round(info.duration, 2))
