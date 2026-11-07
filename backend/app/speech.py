"""Bounded local speech inference. Raw audio stays in request/process memory."""
import base64
import io
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unicodedata
import re
from fastapi import HTTPException
from backend.app.config import settings

MAX_AUDIO_BYTES = 2 * 1024 * 1024
SLOTS = threading.BoundedSemaphore(2)


def capabilities():
    config = settings()
    ready = (config.speech_enabled and importlib.util.find_spec("faster_whisper") is not None
             and all((Path(config.speech_model_path) / name).is_file()
                     for name in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt")))
    return dict(stt="local_whisper" if ready else "browser_local", local_stt_ready=ready,
        languages=["auto", "en", "hi", "kn"], max_seconds=30, max_bytes=MAX_AUDIO_BYTES,
        tts="browser_local", raw_audio_persisted=False, external_processing=False)


def transcribe(content, language="auto"):
    if not capabilities()["local_stt_ready"]:
        raise HTTPException(503, "Local speech recognition is not configured. Type your question or use supported on-device recognition.")
    if language not in {"auto", "en", "hi", "kn"} or not content or len(content) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Use at most 30 seconds / 2 MB of supported audio.")
    if not SLOTS.acquire(blocking=False):
        raise HTTPException(429, "Speech recognition is busy; retry shortly.", headers={"Retry-After": "2"})
    try:
        env = {k:v for k,v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
        env.update(PYTHONIOENCODING="utf-8", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", OMP_NUM_THREADS="2")
        payload = dict(audio=base64.b64encode(content).decode(), language=language, model=settings().speech_model_path)
        try:
            result = subprocess.run([sys.executable, "-m", "backend.app.speech"], input=json.dumps(payload),
                capture_output=True, text=True, encoding="utf-8", env=env,
                cwd=Path(__file__).resolve().parents[2], timeout=45)
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, "Local transcription timed out. Try a shorter recording.") from exc
        if result.returncode or len(result.stdout) > 16000:
            raise HTTPException(503, "Local speech recognition failed safely. Try text input.")
        try:
            output = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise HTTPException(503, "Local speech recognition returned an invalid response. Use text input.") from exc
        if output.get("error"):
            raise HTTPException(400, output["error"])
        if output.get("language") not in {"en","hi","kn"} or not output.get("text","").strip():
            raise HTTPException(400,"Speech was unclear in the supported languages. Choose English, Kannada or Hindi and retry.")
        return output
    finally:
        SLOTS.release()


def infer(audio, model, language):
    import av
    import numpy as np
    from faster_whisper import WhisperModel
    from faster_whisper.vad import get_speech_timestamps, VadOptions
    if language not in {"auto","en","hi","kn"}:
        raise ValueError("Choose English, Kannada, Hindi or Auto.")
    format_name = "wav" if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE" else "ogg" if audio.startswith(b"OggS") else "matroska" if audio.startswith(b"\x1aE\xdf\xa3") else None
    if format_name is None:
        raise ValueError("Use a WAV, WebM or Ogg audio recording.")
    samples = []
    total = 0
    with av.open(io.BytesIO(audio), format=format_name, options={"protocol_whitelist": "file,pipe"}) as container:
        if not container.streams.audio:
            raise ValueError("The upload has no audio stream.")
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                total += converted.samples
                if total > 16000 * 30:
                    raise ValueError("Recording exceeds 30 seconds.")
                samples.append(converted.to_ndarray().reshape(-1))
    if total < 6400:
        raise ValueError("Recording is too short. Speak a complete question and retry.")
    waveform = np.concatenate(samples)
    if not np.isfinite(waveform).all() or float(np.sqrt(np.mean(waveform ** 2))) < 0.001:
        raise ValueError("No audible speech detected. Record a spoken question or use text input.")
    spans=get_speech_timestamps(waveform,VadOptions(threshold=.6,min_speech_duration_ms=250,
        min_silence_duration_ms=200,speech_pad_ms=120))
    speech_samples=sum(span["end"]-span["start"] for span in spans)
    if speech_samples < 4800 or speech_samples/max(total,1)<.1:
        raise ValueError("No clear speech detected. Move away from noise, speak a complete question and retry.")
    spoken=np.concatenate([waveform[s["start"]:s["end"]] for s in spans])
    recognizer = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=2,
                             num_workers=1, local_files_only=True)
    result=recognize_supported(recognizer,spoken,language)
    return dict(**result,provider="local_whisper",seconds=round(total/16000,2),
        speech_seconds=round(speech_samples/16000,2),requires_review=True,raw_audio_persisted=False)


def recognize_supported(recognizer, waveform, language):
    """Auto compares only explicitly forced supported-language decodes, never language=None."""
    candidates=[]
    for forced in ("en","hi","kn") if language=="auto" else (language,):
        segments,_info=recognizer.transcribe(waveform,language=forced,beam_size=3,best_of=1,
            temperature=0,vad_filter=False,condition_on_previous_text=False,
            log_prob_threshold=-1.1,no_speech_threshold=.6,compression_ratio_threshold=2.4)
        segments=list(segments)
        text=" ".join(s.text.strip() for s in segments).strip()
        letters=[c for c in text if c.isalpha()]
        allowed=all("LATIN" in unicodedata.name(c,"") or
            (forced=="hi" and "DEVANAGARI" in unicodedata.name(c,"")) or
            (forced=="kn" and "KANNADA" in unicodedata.name(c,"")) for c in letters)
        words=text.lower().split()
        if not segments or len(letters)<3 or not allowed or (len(words)>=6 and len(set(words))/len(words)<.3):
            continue
        weight=sum(max(1,len(s.text)) for s in segments)
        score=sum(s.avg_logprob*max(1,len(s.text)) for s in segments)/weight
        if score < -1.1 or any(s.no_speech_prob>.6 or s.compression_ratio>2.4 for s in segments):
            continue
        candidates.append(dict(text=text[:2000],language=forced,decode_score=round(score,4)))
    candidates.sort(key=lambda r:r["decode_score"],reverse=True)
    if not candidates:
        raise ValueError("Speech was unclear in English, Kannada or Hindi. Choose the spoken language and retry in a quieter place.")
    consensus=len({re.sub(r"\W+"," ",c["text"].casefold()).strip() for c in candidates})==1
    if language=="auto" and consensus and all(c["text"].isascii() for c in candidates):
        english=next((c for c in candidates if c["language"]=="en"),None)
        if english:
            candidates.remove(english)
            candidates.insert(0,english)
    if language=="auto" and len(candidates)>1 and not consensus and candidates[0]["decode_score"]-candidates[1]["decode_score"]<.12:
        raise ValueError("The spoken language is ambiguous. Choose English, Kannada or Hindi and retry.")
    return dict(**candidates[0],language_strategy="three_forced_decodes" if language=="auto" else "explicit_language",
                quality_gate_passed=True,transcript_consensus=consensus)


if __name__ == "__main__":
    from contextlib import redirect_stdout, redirect_stderr
    try:
        from backend.app.process_limits import apply_limits
        apply_limits(memory_mb=1536, cpu_seconds=40)
        data = json.loads(sys.stdin.read(3_000_000))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = infer(base64.b64decode(data["audio"], validate=True), data["model"], data["language"])
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc)[:250] if isinstance(exc, ValueError) else "Audio could not be transcribed locally. Try text input."}))
