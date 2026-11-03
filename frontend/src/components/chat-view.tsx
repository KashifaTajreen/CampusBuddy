"use client";
import { useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  FileText,
  Plus,
  Trash2,
  Sparkles,
  LoaderCircle,
  Mic,
  Square,
} from "lucide-react";
import { api, streamChat, type Answer } from "@/lib/api";
import { MAX_RECORDING_SECONDS, useVoiceInput } from "@/lib/use-voice-input";
import { Button } from "@/components/ui/button";
import { RecordList } from "@/components/result-cards";
import {
  type Workspace,
  useRemote,
  ErrorBox,
  QuickActions,
} from "@/components/campus-app";
function formatSeconds(total: number) {
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
interface Turn {
  role: "user" | "assistant";
  text?: string;
  answer?: Answer;
}

export function ChatView({
  w,
  queued,
  consumed,
}: {
  w: Workspace;
  queued: string;
  consumed: () => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [conversation, setConversation] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [partial, setPartial] = useState("");
  const [error, setError] = useState("");
  const [historyRevision, setHistoryRevision] = useState(0);
  const lock = useRef(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const handled = useRef("");
  const field = useRef<HTMLInputElement>(null);
  const focusField = useRef(false);
  const voice = useVoiceInput({
    session: w.session,
    tenant: w.tenant,
    // The transcript goes into the box, so it can be checked before sending.
    onText: (text) => {
      setInput((current) => (current ? `${current} ${text}` : text).slice(0, 2000));
      focusField.current = true;
    },
    onError: setError,
  });
  const voiceActive = voice.state !== "idle";
  // The box is disabled while transcribing, so it can only take focus once that has finished.
  useEffect(() => {
    if (voice.state === "idle" && focusField.current) {
      focusField.current = false;
      field.current?.focus();
    }
  }, [voice.state]);
  const history = useRemote<{ id: string; title: string }[]>(
    "/v1/conversations",
    { ...w, revision: w.revision + historyRevision },
  );
  async function ask(question: string) {
    if (lock.current || !question.trim()) return;
    lock.current = true;
    setBusy(true);
    setPartial("");
    setError("");
    setInput("");
    setTurns((t) => [...t, { role: "user", text: question }]);
    abort.current = new AbortController();
    try {
      const answer = await streamChat(
        question,
        conversation,
        w.session,
        w.tenant,
        (delta) => setPartial((p) => p + delta),
        abort.current.signal,
      );
      setConversation(answer.conversation_id);
      setTurns((t) => [...t, { role: "assistant", answer }]);
      setHistoryRevision((n) => n + 1);
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError"))
        setError(
          e instanceof Error ? e.message : "Could not answer. Try again.",
        );
    } finally {
      setBusy(false);
      setPartial("");
      lock.current = false;
    }
  }
  useEffect(() => {
    let cancelled = false;
    if (queued && handled.current !== queued) {
      queueMicrotask(() => {
        if (cancelled) return;
        handled.current = queued;
        consumed();
        void ask(queued);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [queued]); // A queued quick action is consumed once.
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "auto", block: "end" });
  }, [turns, busy]);
  async function open(id: string) {
    if (busy) return;
    setError("");
    try {
      const items = await api<
        { role: string; content: Record<string, unknown> }[]
      >(`/v1/conversations/${id}`, w.session, w.tenant);
      setConversation(id);
      setTurns(
        items.map((m) =>
          m.role === "user"
            ? { role: "user", text: String(m.content.text || "") }
            : { role: "assistant", answer: m.content as unknown as Answer },
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function remove() {
    if (!conversation || busy) return;
    try {
      await api(`/v1/conversations/${conversation}`, w.session, w.tenant, {
        method: "DELETE",
      });
      setTurns([]);
      setConversation(null);
      setHistoryRevision((n) => n + 1);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function newChat() {
    if (busy) return;
    setConversation(null);
    setTurns([]);
    setError("");
  }
  return (
    <div className="chat-layout">
      <div className="page-heading">
        <div>
          <div className="eyebrow">CAMPUS KNOWLEDGE, IN CONVERSATION</div>
          <h1>Ask CampusOS</h1>
          <p>Less searching. More knowing.</p>
        </div>
        <Button className="new-chat" variant="outline" onClick={newChat} disabled={busy}>
          <Plus size={15} />
          New chat
        </Button>
      </div>
      <div className="conversation-bar">
        <div className="filter-field">
          <label htmlFor="conversation">Your conversations</label>
          <select
            id="conversation"
            value={conversation || ""}
            onChange={(e) =>
              e.target.value ? void open(e.target.value) : newChat()
            }
            disabled={busy}
          >
            <option value="">New conversation</option>
            {history.data?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </select>
        </div>
        {conversation && (
          <Button
            variant="ghost"
            onClick={remove}
            disabled={busy}
            aria-label="Delete this conversation"
          >
            <Trash2 size={17} />
          </Button>
        )}
      </div>
      <ErrorBox message={error || history.error} />
      {!turns.length && (
        <div className="chat-empty">
          <span className="spark-icon">
            <Sparkles size={26} />
          </span>
          <h2>What’s on your mind?</h2>
          <p>
            Ask about your next class, find a meal in your budget, or make sense
            of the latest campus notice.
          </p>
          <QuickActions ask={ask} />
        </div>
      )}
      <div aria-live="polite" aria-busy={busy}>
        {turns.map((turn, i) =>
          turn.role === "user" ? (
            <div className="chat-message chat-user" key={i}>
              <p>{turn.text}</p>
            </div>
          ) : (
            <article className="chat-message" key={i}>
              <div className="assistant-label">
                <span className="brand-mark">
                  <Sparkles size={15} />
                </span>
                CampusOS
              </div>
              <p className="chat-answer">{turn.answer?.answer}</p>
              {turn.answer && turn.answer.records.length > 0 && (
                <RecordList
                  records={turn.answer.records}
                  kind={turn.answer.kind}
                  onSource={w.download}
                  limit={4}
                />
              )}
              <div className="citations">
                {turn.answer?.sources.map((s, ix) => (
                  <button
                    key={s.id}
                    onClick={() => w.download(s.id)}
                    title={`${s.demo ? "Demo Data · " : ""}Imported ${new Date(s.created_at).toLocaleDateString("en-IN")}`}
                  >
                    <FileText size={12} />
                    {ix + 1}. {s.filename}
                  </button>
                ))}
              </div>
              {i === turns.length - 1 && !busy && (
                <div className="quick-actions">
                  {turn.answer?.followups.map((f) => (
                    <button
                      className="quick-action"
                      onClick={() => ask(f)}
                      key={f}
                    >
                      {f}
                      <ArrowUpRight size={12} />
                    </button>
                  ))}
                </div>
              )}
            </article>
          ),
        )}
        {busy && (
          <div className="chat-message">
            <div className="assistant-label">
              <LoaderCircle className="spin" size={17} />
              Finding your answer…
            </div>
            {partial && <p className="chat-answer">{partial}</p>}
          </div>
        )}
      </div>
      <div ref={bottom} />
      <div className="chat-composer">
        <form
          className="ask-form"
          onSubmit={(e) => {
            e.preventDefault();
            void ask(input);
          }}
        >
          <label className="sr-only" htmlFor="chat-question">
            Ask a campus question
          </label>
          <input
            id="chat-question"
            ref={field}
            placeholder={
              voice.state === "recording"
                ? "Listening... tap stop when you finish"
                : voice.state === "transcribing"
                  ? "Turning your speech into text..."
                  : "Ask anything about your campus..."
            }
            value={input}
            onChange={(e) => setInput(e.target.value)}
            maxLength={2000}
            disabled={busy || voiceActive}
          />
          {voice.state === "recording" && (
            <span className="voice-timer" aria-hidden="true">
              {formatSeconds(voice.seconds)}
            </span>
          )}
          {voice.supported && (
            <Button
              type="button"
              variant="outline"
              className={`voice-button ${voice.state === "recording" ? "recording" : ""}`}
              onClick={voice.toggle}
              disabled={busy || voice.state === "transcribing"}
              aria-pressed={voice.state === "recording"}
              aria-label={
                voice.state === "recording" ? "Stop voice input" : "Start voice input"
              }
            >
              {voice.state === "transcribing" ? (
                <LoaderCircle className="spin" size={18} />
              ) : voice.state === "recording" ? (
                <Square size={16} />
              ) : (
                <Mic size={18} />
              )}
            </Button>
          )}
          <span className="sr-only" role="status">
            {voice.state === "recording"
              ? `Recording. Stops automatically after ${MAX_RECORDING_SECONDS} seconds.`
              : voice.state === "transcribing"
                ? "Turning your speech into text."
                : ""}
          </span>
          <Button
            type="submit"
            disabled={busy || voiceActive || !input.trim()}
            aria-label="Send question"
          >
            <ArrowUpRight size={20} />
          </Button>
        </form>
        <p>
          CampusOS uses your authorized sources. Check cited documents for
          important decisions.
        </p>
      </div>
    </div>
  );
}
