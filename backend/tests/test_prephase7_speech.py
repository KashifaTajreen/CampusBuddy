import io
from types import SimpleNamespace
import wave
import pytest
from backend.app.speech import recognize_supported, infer, transcribe, capabilities


class Recognizer:
    def __init__(self, scores=None, transcripts=None):
        self.calls=[]
        self.scores=scores or {"en":-.15,"hi":-.8,"kn":-.9}
        self.transcripts=transcripts or {"en":"What is my next class?","hi":"मेरी next class कब है?","kn":"ನನ್ನ next class ಯಾವಾಗ?"}
    def transcribe(self, _waveform, **options):
        forced=options["language"]
        self.calls.append(options)
        return iter([SimpleNamespace(text=self.transcripts[forced],avg_logprob=self.scores[forced],
            no_speech_prob=.01,compression_ratio=1.2)]),SimpleNamespace(language=forced)


@pytest.mark.parametrize("language",["en","hi","kn"])
def test_explicit_language_is_forced_and_supports_code_switching(language):
    model=Recognizer()
    result=recognize_supported(model,[],language)
    assert result["language"]==language and result["quality_gate_passed"]
    assert [c["language"] for c in model.calls]==[language]


def test_auto_compares_only_three_forced_languages():
    model=Recognizer()
    result=recognize_supported(model,[],"auto")
    assert result["language"]=="en"
    assert [c["language"] for c in model.calls]==["en","hi","kn"]
    assert all(c["condition_on_previous_text"] is False for c in model.calls)


def test_ambiguous_and_foreign_script_outputs_request_retry():
    with pytest.raises(ValueError,match="ambiguous"):
        recognize_supported(Recognizer(scores={"en":-.15,"hi":-.16,"kn":-.18}),[],"auto")
    with pytest.raises(ValueError,match="unclear"):
        recognize_supported(Recognizer(transcripts={"en":"こんにちは世界","hi":"こんにちは世界","kn":"こんにちは世界"}),[],"auto")


def test_real_noise_and_short_audio_do_not_reach_decoding():
    import numpy as np
    pytest.importorskip("faster_whisper",reason="Optional local speech runtime unavailable")
    for samples in (np.random.default_rng(17).normal(0,.08,16000*3),np.zeros(1000)):
        out=io.BytesIO()
        with wave.open(out,"wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes((samples*32767).astype("<i2").tobytes())
        with pytest.raises(ValueError,match="speech|short"):
            infer(out.getvalue(),"unused","auto")


def test_real_english_auto_language_selection():
    from pathlib import Path
    if not capabilities()["local_stt_ready"]:
        pytest.skip("Approved local Whisper-tiny model/runtime unavailable")
    result=transcribe((Path(__file__).parent/"fixtures/speech-question.wav").read_bytes(),"auto")
    assert result["language"]=="en" and "next class" in result["text"].lower()
    assert result["language_strategy"]=="three_forced_decodes"
