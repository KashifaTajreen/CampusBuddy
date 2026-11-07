import type { Session } from "@supabase/supabase-js";
import { API, ApiError } from "./api";
import { normalizeSpeechText, speechLanguage, type SpeechDateFormat } from "./speech-text";

export type SpeechLanguage = "auto" | "en" | "hi" | "kn";
export type CaptureState = "idle" | "requesting" | "recording" | "transcribing";
export interface Capture { stop(): void; cancel(): void }
export interface SpeechInput {
  start(language: SpeechLanguage, state: (state: CaptureState) => void,
    transcript: (text: string) => void, error: (message: string) => void, signal?: AbortSignal): Promise<Capture>;
}
export interface SpeechOutput { speak(text: string, language: SpeechLanguage, done: () => void, failed?: (message: string) => void): void; stop(): void }
const LOCALES = { en: "en-IN", hi: "hi-IN", kn: "kn-IN", auto: "en-IN" };
export const MAX_RECORDING_SECONDS = 30;
export const MAX_AUDIO_BYTES = 2 * 1024 * 1024;

export class LocalServerSpeech implements SpeechInput {
  constructor(private session: Session, private tenant: string) {}
  async start(language: SpeechLanguage, state: (state: CaptureState) => void,
    transcript: (text: string) => void, error: (message: string) => void, signal?: AbortSignal): Promise<Capture> {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined")
      throw new Error("Microphone recording is unavailable. Use text input.");
    state("requesting");
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (signal?.aborted) { stream.getTracks().forEach((t) => t.stop()); throw new DOMException("Capture cancelled", "AbortError"); }
    const type = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm"].find((t) => MediaRecorder.isTypeSupported(t));
    if (!type) { stream.getTracks().forEach((t) => t.stop()); throw new Error("This browser has no supported recording format. Use text input."); }
    let recorder: MediaRecorder;
    try { recorder = new MediaRecorder(stream, { mimeType: type, audioBitsPerSecond: 64000 }); }
    catch (e) { stream.getTracks().forEach((t) => t.stop()); throw e; }
    let chunks: Blob[] = [], bytes = 0, cancelled = false;
    const abort = new AbortController();
    const release = () => stream.getTracks().forEach((t) => t.stop());
    const timer = window.setTimeout(() => { if (recorder.state === "recording") recorder.stop(); }, MAX_RECORDING_SECONDS * 1000);
    recorder.ondataavailable = (event) => {
      bytes += event.data.size;
      if (bytes > MAX_AUDIO_BYTES) {
        cancelled = true; chunks = []; release(); window.clearTimeout(timer);
        if (recorder.state === "recording") recorder.stop();
        error("Recording exceeded 2 MB. Try a shorter question."); state("idle");
      } else if (!cancelled) chunks.push(event.data);
    };
    recorder.onerror = () => { cancelled = true; release(); window.clearTimeout(timer); state("idle"); error("Recording failed. Use text input."); };
    recorder.onstop = async () => {
      release(); window.clearTimeout(timer);
      if (cancelled) { chunks = []; return; }
      state("transcribing");
      try {
        const body = new Blob(chunks, { type }); chunks = [];
        const response = await fetch(`${API}/v1/voice/transcribe?language=${language}`, {
          method: "POST", body, signal: abort.signal,
          headers: { Authorization: `Bearer ${this.session.access_token}`, "X-Tenant-ID": this.tenant, "Content-Type": type },
        });
        if (!response.ok) {
          const value = await response.json();
          throw new ApiError(value.detail || "Transcription failed. Use text input.", response.status);
        }
        const value = await response.json();
        if (!cancelled) transcript(String(value.text || "").slice(0, 2000));
      } catch (e) { if (!cancelled) error((e as Error).message); }
      finally { if (!cancelled) state("idle"); }
    };
    try { recorder.start(1000); }
    catch (e) { release(); window.clearTimeout(timer); throw e; }
    state("recording");
    return {
      stop: () => { if (recorder.state === "recording") recorder.stop(); },
      cancel: () => { cancelled = true; abort.abort(); release(); chunks = []; window.clearTimeout(timer); if (recorder.state === "recording") recorder.stop(); },
    };
  }
}

interface Recognition {
  processLocally: boolean; lang: string; continuous: boolean; interimResults: boolean;
  onresult: ((event: { results: { [index: number]: { [index: number]: { transcript: string }; isFinal: boolean }; length: number } }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void; stop(): void; abort(): void;
}
type RecognitionConstructor = { new(): Recognition; available?: (options: { langs: string[]; processLocally: boolean }) => Promise<string> };

export class BrowserLocalSpeech implements SpeechInput {
  async start(language: SpeechLanguage, state: (state: CaptureState) => void,
    transcript: (text: string) => void, error: (message: string) => void, signal?: AbortSignal): Promise<Capture> {
    if (language === "auto") throw new Error("Choose a language for browser-local recognition; automatic detection needs the local server.");
    const browser = window as unknown as { SpeechRecognition?: RecognitionConstructor; webkitSpeechRecognition?: RecognitionConstructor };
    const Constructor = browser.SpeechRecognition || browser.webkitSpeechRecognition;
    if (!Constructor) throw new Error("On-device speech is unavailable. Type your question.");
    const recognition = new Constructor();
    if (!("processLocally" in recognition) || !Constructor.available)
      throw new Error("This browser cannot guarantee local recognition. Use text input; remote recognition is disabled.");
    state("requesting");
    if (await Constructor.available({ langs: [LOCALES[language]], processLocally: true }) !== "available")
      throw new Error("The on-device language pack is not installed. Use text input or configure local server speech.");
    if (signal?.aborted) throw new DOMException("Capture cancelled", "AbortError");
    recognition.processLocally = true; recognition.lang = LOCALES[language];
    recognition.continuous = false; recognition.interimResults = false;
    let cancelled = false;
    const timer = window.setTimeout(() => recognition.stop(), MAX_RECORDING_SECONDS * 1000);
    recognition.onresult = (event) => {
      if (!cancelled) transcript(Array.from({ length: event.results.length }, (_, i) => event.results[i].isFinal ? event.results[i][0].transcript : "").join(" ").slice(0, 2000));
    };
    recognition.onerror = (event) => { if (!cancelled) error(event.error === "not-allowed" ? "Microphone permission denied. Use text input." : "On-device recognition failed. Use text input."); };
    recognition.onend = () => { window.clearTimeout(timer); if (!cancelled) state("idle"); };
    recognition.start(); state("recording");
    return { stop: () => recognition.stop(), cancel: () => { cancelled = true; window.clearTimeout(timer); recognition.abort(); } };
  }
}

export class DeviceSpeech implements SpeechOutput {
  private utterances: SpeechSynthesisUtterance[] = [];
  private waiting?: number;
  private starting?: number;
  private epoch = 0;
  constructor(private dateFormat?:SpeechDateFormat) {}
  speak(text: string, language: SpeechLanguage, done: () => void, failed?: (message: string) => void) {
    if (!("speechSynthesis" in window)) throw new Error("Speech playback is unavailable. Read the text answer.");
    const chosen=speechLanguage(text,language);
    const normalized = normalizeSpeechText(text.slice(0,4000),chosen,{dateFormat:this.dateFormat}).trim();
    if(!normalized)throw new Error("There is no answer text to read aloud.");
    this.stop();
    const epoch = this.epoch;
    const synthesis = window.speechSynthesis;
    const started = Date.now();
    const fail = (message:string) => {
      if(epoch!==this.epoch)return;
      this.stop(); done(); failed?.(message);
    };
    const play = () => {
      if(epoch!==this.epoch)return;
      const voice = synthesis.getVoices().find(v=>v.localService&&v.lang.toLowerCase().startsWith(chosen));
      if(!voice){
        // Chrome initializes the OS voices asynchronously, often after the first click.
        if(Date.now()-started<2000){this.waiting=window.setTimeout(play,100);return;}
        fail(`No local device voice is installed for ${chosen === "en" ? "English" : chosen === "hi" ? "Hindi" : "Kannada"}. Choose English for this demo or install that language's Windows speech voice.`);
        return;
      }
      this.waiting=undefined;
      const segments:string[]=[];
      for(const word of normalized.split(/\s+/)){
        const last=segments.length-1;
        if(last<0||segments[last].length+word.length+1>180)segments.push(word);
        else segments[last]+=" "+word;
      }
      // Retain utterances until completion; short segments avoid Chrome's long-text stalls.
      this.utterances=segments.map(segment=>{
        const utterance=new SpeechSynthesisUtterance(segment);
        utterance.voice=voice;utterance.lang=voice.lang;utterance.rate=1;
        utterance.onstart=()=>{if(epoch===this.epoch){window.clearTimeout(this.starting);this.starting=undefined;}};
        utterance.onerror=event=>fail(event.error==="not-allowed"
          ? "Chrome blocked audio. Click Read aloud again and allow sound for this site."
          : "Speech playback stopped. Check your speaker volume and click Read aloud to retry.");
        return utterance;
      });
      this.utterances[this.utterances.length-1].onend=()=>{
        if(epoch!==this.epoch)return;
        window.clearTimeout(this.starting);this.starting=undefined;this.utterances=[];done();
      };
      this.starting=window.setTimeout(()=>fail("Chrome did not start audio. Allow site sound, check your speakers, and click Read aloud again."),5000);
      try{
        for(const utterance of this.utterances)synthesis.speak(utterance);
        synthesis.resume?.();
      }catch{fail("Speech playback could not start. Check Chrome's sound settings and retry.");}
    };
    play();
  }
  stop() {
    this.epoch++;
    window.clearTimeout(this.waiting);window.clearTimeout(this.starting);
    this.waiting=undefined;this.starting=undefined;this.utterances=[];
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  }
}
