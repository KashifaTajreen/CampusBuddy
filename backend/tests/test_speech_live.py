"""Runs the real speech model on really synthesized speech. Skips where either is unavailable."""

import re
import shutil
import subprocess
import sys
import wave
import pytest
from backend.app import speech
from backend.app.config import ROOT, settings

MODEL = ROOT / "data" / "models" / "faster-whisper-small"
pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Speech synthesis uses Windows SAPI."),
    pytest.mark.skipif(not MODEL.is_dir(), reason="The speech model is not installed."),
    pytest.mark.skipif(not shutil.which("powershell"), reason="PowerShell is unavailable."),
]


def synthesize(text, path):
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{text}'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, timeout=60)
    return path.read_bytes()


def to_webm_opus(wav_bytes, path):
    """Encode like a browser's MediaRecorder: Opus at 48 kHz in a WebM container, via PyAV."""
    av = pytest.importorskip("av")
    if "libopus" not in av.codecs_available:
        pytest.skip("This PyAV build has no Opus encoder.")
    source = path.with_suffix(".source.wav")
    source.write_bytes(wav_bytes)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=48000, frame_size=960)
    with av.open(str(source)) as reader, av.open(str(path), "w", format="webm") as writer:
        stream = writer.add_stream("libopus", rate=48000)
        stream.layout = "mono"
        for frame in reader.decode(audio=0):
            for resampled in resampler.resample(frame):
                writer.mux(stream.encode(resampled))
        for resampled in resampler.resample(None):
            writer.mux(stream.encode(resampled))
        writer.mux(stream.encode(None))
    data = path.read_bytes()
    assert data[:4] == bytes([0x1A, 0x45, 0xDF, 0xA3]), "not a Matroska/WebM file"
    assert b"A_OPUS" in data[:400], "not an Opus track"
    return data


def words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def word_error_rate(expected, actual):
    """Word-level edit distance divided by the number of expected words."""
    a, b = words(expected), words(actual)
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        previous, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (x != y))
    return row[-1] / len(a)


SENTENCES = [
    "What is my next class tomorrow?",
    "What is on the canteen menu today?",
    "Which professors teach machine learning?",
    "When is the internal assessment for the AIML department?",
    "Show my timetable for Friday.",
]


@pytest.fixture(autouse=True)
def real_model(monkeypatch):
    monkeypatch.setattr(settings(), "speech_model", str(MODEL))
    speech.speech_model.cache_clear()
    yield
    speech.speech_model.cache_clear()


def test_real_speech_becomes_text(tmp_path):
    result = speech.transcribe(synthesize("What is my next class tomorrow?", tmp_path / "question.wav"))
    words = result["text"].lower()
    assert "next class" in words
    assert "tomorrow" in words
    assert result["language"] == "en"
    assert result["duration"] > 0.5


def test_campus_vocabulary_is_recognized(tmp_path):
    result = speech.transcribe(synthesize("What is on the canteen menu today?", tmp_path / "menu.wav"))
    assert "canteen" in result["text"].lower()


@pytest.mark.parametrize("container", ["wav", "webm-opus"])
def test_word_error_rate_by_format(tmp_path, container, capsys):
    """Real SAPI speech through the real model. WebM/Opus is what Chrome's MediaRecorder uploads."""
    rates = []
    for number, sentence in enumerate(SENTENCES):
        wav = synthesize(sentence, tmp_path / f"{number}.wav")
        content = wav if container == "wav" else to_webm_opus(wav, tmp_path / f"{number}.webm")
        result = speech.transcribe(content)
        rates.append(word_error_rate(sentence, result["text"]))
        with capsys.disabled():
            print(f"[{container}] expected={sentence!r} heard={result['text']!r} wer={rates[-1]:.2f}")
    average = sum(rates) / len(rates)
    with capsys.disabled():
        print(f"[{container}] mean WER {average:.3f} over {len(rates)} sentences")
    assert average <= 0.25


def test_silence_gives_no_text(tmp_path):
    path = tmp_path / "silence.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000 * 3)
    assert speech.transcribe(path.read_bytes())["text"] == ""


def test_unreadable_audio_is_reported(tmp_path):
    with pytest.raises(ValueError, match="could not be read"):
        speech.transcribe(b"this is not audio" * 100)
