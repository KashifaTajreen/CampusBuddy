"use client";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { Session } from "@supabase/supabase-js";
import { api } from "@/lib/api";

export const MAX_RECORDING_SECONDS = 60;
const FORMATS = [
  { mime: "audio/webm;codecs=opus", name: "question.webm" },
  { mime: "audio/mp4", name: "question.mp4" },
  { mime: "audio/ogg;codecs=opus", name: "question.ogg" },
];
type VoiceState = "idle" | "recording" | "transcribing";

function canRecord() {
  // mediaDevices is undefined on pages not served over HTTPS or localhost.
  return (
    typeof navigator.mediaDevices?.getUserMedia === "function" &&
    typeof MediaRecorder !== "undefined"
  );
}
const noSubscribe = () => () => {};

function microphoneError(error: unknown) {
  const name = error instanceof DOMException ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError")
    return "Microphone access is blocked. Allow it in your browser settings, then try again.";
  if (name === "NotFoundError") return "No microphone was found. Connect one, or type your question.";
  if (name === "NotReadableError") return "Another app is using your microphone. Close it, then try again.";
  return "Could not start the microphone. Try again, or type your question.";
}

export function useVoiceInput({
  session,
  tenant,
  onText,
  onError,
}: {
  session: Session;
  tenant: string;
  onText: (text: string) => void;
  onError: (message: string) => void;
}) {
  // False during server rendering, so the button appears only where recording works.
  const supported = useSyncExternalStore(noSubscribe, canRecord, () => false);
  const [state, setState] = useState<VoiceState>("idle");
  const [seconds, setSeconds] = useState(0);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const request = useRef<AbortController | null>(null);
  // Each recording gets a number, so results from a cancelled one are ignored.
  const attempt = useRef(0);
  const callbacks = useRef({ onText, onError, session, tenant });
  useEffect(() => {
    callbacks.current = { onText, onError, session, tenant };
  });

  const release = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
  }, []);

  const cancel = useCallback(() => {
    attempt.current += 1;
    recorder.current = null;
    request.current?.abort();
    release();
    setState("idle");
    setSeconds(0);
  }, [release]);

  const stop = useCallback(() => {
    if (recorder.current?.state === "recording") recorder.current.stop();
  }, []);

  async function send(blob: Blob, filename: string, current: number) {
    const { session: activeSession, tenant: activeTenant } = callbacks.current;
    const body = new FormData();
    body.append("file", blob, filename);
    request.current = new AbortController();
    try {
      const result = await api<{ text: string }>("/v1/speech/transcribe", activeSession, activeTenant, {
        method: "POST",
        body,
        signal: request.current.signal,
      });
      if (current !== attempt.current) return;
      if (result.text.trim()) callbacks.current.onText(result.text.trim());
      else callbacks.current.onError("I didn't catch that. Try again, a little closer to the microphone.");
    } catch (error) {
      if (current !== attempt.current) return;
      callbacks.current.onError(
        error instanceof Error ? error.message : "Could not transcribe that. Try again.",
      );
    } finally {
      if (current === attempt.current) setState("idle");
    }
  }

  async function start() {
    const current = ++attempt.current;
    callbacks.current.onError("");
    setSeconds(0);
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (current !== attempt.current) {
        media.getTracks().forEach((track) => track.stop());
        return;
      }
      stream.current = media;
      const format = FORMATS.find((f) => MediaRecorder.isTypeSupported(f.mime));
      const next = new MediaRecorder(media, format ? { mimeType: format.mime } : undefined);
      const parts: Blob[] = [];
      next.ondataavailable = (event) => {
        if (event.data.size) parts.push(event.data);
      };
      next.onstop = () => {
        release();
        if (current !== attempt.current) return;
        recorder.current = null;
        setState("transcribing");
        void send(new Blob(parts, { type: next.mimeType }), format?.name ?? "question.webm", current);
      };
      next.start();
      recorder.current = next;
      setState("recording");
      const began = Date.now();
      timer.current = setInterval(() => {
        const elapsed = Math.floor((Date.now() - began) / 1000);
        setSeconds(elapsed);
        if (elapsed >= MAX_RECORDING_SECONDS) stop();
      }, 250);
    } catch (error) {
      if (current !== attempt.current) return;
      release();
      setState("idle");
      callbacks.current.onError(microphoneError(error));
    }
  }

  // Never carry a recording, or its transcript, across a tenant change or page exit.
  useEffect(() => cancel, [tenant, cancel]);

  return {
    supported,
    state,
    seconds,
    toggle: () => {
      if (state === "recording") stop();
      else if (state === "idle") void start();
    },
  };
}
