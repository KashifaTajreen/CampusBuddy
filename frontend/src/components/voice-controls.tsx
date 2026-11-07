"use client";
import { useEffect, useRef, useState } from "react";
import { Mic, Square, Volume2 } from "lucide-react";
import { Button } from "./ui/button";
import type { Session } from "@supabase/supabase-js";
import { api, type Answer } from "@/lib/api";
import { BrowserLocalSpeech, LocalServerSpeech, DeviceSpeech, type Capture, type CaptureState, type SpeechLanguage } from "@/lib/speech";
import type { SpeechDateFormat } from "@/lib/speech-text";

export function VoiceControls({ session, tenant, language, disabled, onTranscript, onActivity }: {
  session: Session; tenant: string; language: SpeechLanguage; disabled: boolean;
  onTranscript: (text: string) => void; onActivity: (active: boolean) => void;
}) {
  const [state, setState] = useState<CaptureState>("idle");
  const [error, setError] = useState("");
  const capture = useRef<Capture | null>(null);
  const epoch = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const activity = useRef(onActivity);
  activity.current = onActivity;
  useEffect(() => () => { epoch.current++; pending.current?.abort(); capture.current?.cancel(); activity.current(false); }, []);
  async function start() {
    if (state !== "idle" || disabled) return;
    const current = ++epoch.current;
    pending.current = new AbortController();
    new DeviceSpeech().stop();
    setError(""); setState("requesting"); onActivity(true);
    try {
      const capabilities = await api<{ local_stt_ready: boolean }>("/v1/voice/capabilities", session, tenant);
      if (current !== epoch.current) return;
      const provider = capabilities.local_stt_ready ? new LocalServerSpeech(session, tenant) : new BrowserLocalSpeech();
      const next = await provider.start(language, (nextState) => {
        if (current !== epoch.current) return;
        setState(nextState); onActivity(nextState !== "idle");
      }, (text) => { if (current === epoch.current) onTranscript(text); }, (message) => { if (current === epoch.current) setError(message); }, pending.current.signal);
      if (current !== epoch.current) next.cancel(); else capture.current = next;
    } catch (e) {
      if (current === epoch.current) { setError((e as Error).name === "NotAllowedError" ? "Microphone permission denied. Use text input." : (e as Error).message); setState("idle"); onActivity(false); }
    }
  }
  return <div className="voice-controls">
    <Button type="button" variant="outline" aria-label={state === "recording" ? "Stop recording" : error ? "Retry microphone" : "Start microphone"}
      disabled={disabled || state === "requesting" || state === "transcribing"}
      onClick={() => state === "recording" ? capture.current?.stop() : void start()}>
      {state === "recording" ? <Square size={17} /> : <Mic size={17} />}
      {state === "recording" ? "Stop recording" : error ? "Retry microphone" : "Microphone"}
    </Button>
    {state !== "idle" && <Button type="button" variant="ghost" onClick={() => { epoch.current++; pending.current?.abort(); capture.current?.cancel(); setState("idle"); onActivity(false); }}>Cancel recording</Button>}
    <span role="status">{state === "idle" ? "Press to record. Audio is processed locally; review the transcript before sending." : state === "recording" ? "Recording · maximum 30 seconds" : state === "transcribing" ? "Transcribing locally…" : "Requesting microphone…"}</span>
    {error && <p role="alert">{error}</p>}
  </div>;
}

export function answerSpeechText(answer: Answer): string {
  if(answer.sections?.length) return answer.sections.map(answerSpeechText).join(". ").slice(0,4000);
  const details = answer.records.slice(0, 3).map((record) =>
    ["subject", "name", "title", "start", "end", "room", "faculty", "office", "price", "location"]
      .filter((key) => typeof record[key] === "string" || typeof record[key] === "number")
      .map((key) => `${key === "price" ? "Price in rupees" : key}: ${record[key]}`).join(". "));
  return [answer.answer, ...details].filter(Boolean).join(". ").slice(0, 4000);
}

export function SpokenAnswer({ text, language, autoSpeak = false }: { text: string; language: SpeechLanguage; autoSpeak?: boolean }) {
  const [speaking, setSpeaking] = useState(false);
  const [error, setError] = useState("");
  const output = useRef(new DeviceSpeech());
  const [dateFormat,setDateFormat]=useState<SpeechDateFormat|undefined>();
  function play() {
    setError("");
    try { setSpeaking(true); output.current.speak(text, language, () => setSpeaking(false), setError); }
    catch (e) { setError((e as Error).message); setSpeaking(false); }
  }
  useEffect(() => { const provider = output.current; if (autoSpeak) { try { setError("");setSpeaking(true); provider.speak(text, language, () => setSpeaking(false), setError); } catch (e) { setError((e as Error).message); setSpeaking(false); } } return () => provider.stop(); }, [autoSpeak, text, language]);
  return <div className="spoken-answer"><Button variant="ghost" type="button" onClick={() => speaking ? (output.current.stop(), setSpeaking(false)) : play()} aria-label={speaking ? "Stop spoken answer" : "Read answer aloud"}><Volume2 size={16} />{speaking ? "Stop speech" : "Read aloud"}</Button><span className="subtle">Local device voice; quality and language support vary.</span>
    <details><summary>Speech options</summary><label>Numeric date format for speech<select aria-label="Numeric date format for speech" value={dateFormat||""} onChange={(e)=>{output.current.stop();setSpeaking(false);const format=(e.target.value||undefined) as SpeechDateFormat|undefined;setDateFormat(format);output.current=new DeviceSpeech(format);}}><option value="">Unspecified — do not interpret ambiguous dates</option><option value="DMY">DD-MM-YYYY</option><option value="MDY">MM-DD-YYYY</option></select></label></details>
    {error && <p role="status">{error}</p>}</div>;
}
