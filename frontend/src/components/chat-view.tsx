"use client";
import { useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  FileText,
  Plus,
  Trash2,
  Sparkles,
  LoaderCircle,
} from "lucide-react";
import {
  api,
  streamChat,
  ApiError,
  type Answer,
  type DocumentPage,
  type SelectableDocument,
  type DocumentSelection,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { RecordList } from "@/components/result-cards";
import { VoiceControls, SpokenAnswer, answerSpeechText } from "@/components/voice-controls";
import {
  type Workspace,
  useRemote,
  ErrorBox,
  QuickActions,
} from "@/components/campus-app";
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
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [voiceGeneration, setVoiceGeneration] = useState(0);
  const [voiceAnswer, setVoiceAnswer] = useState(false);
  const [language, setLanguage] = useState<"auto" | "en" | "hi" | "kn">("auto");
  const [conversation, setConversation] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [partial, setPartial] = useState("");
  const [error, setError] = useState("");
  const [historyRevision, setHistoryRevision] = useState(0);
  const [documentRevision, setDocumentRevision] = useState(0);
  const [documents, setDocuments] = useState<SelectableDocument[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [selected, setSelected] = useState<SelectableDocument | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [documentError, setDocumentError] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [opening, setOpening] = useState(false);
  const [savingSelection, setSavingSelection] = useState(false);
  const epoch = useRef(0);
  const openRequest = useRef(0);
  const lock = useRef(false);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const handled = useRef("");
  const history = useRemote<{ id: string; title: string }[]>(
    "/v1/conversations",
    { ...w, revision: w.revision + historyRevision },
  );
  const documentList = useRemote<DocumentPage>("/v1/documents?limit=100", {
    ...w,
    revision: w.revision + documentRevision,
  });
  useEffect(() => {
    setDocuments(documentList.data?.documents || []);
    setNextOffset(documentList.data?.next_offset ?? null);
  }, [documentList.data]);
  useEffect(() => {
    epoch.current += 1;
    openRequest.current += 1;
    abort.current?.abort();
    lock.current = false;
    setBusy(false);
    setOpening(false);
    setSavingSelection(false);
    setConversation(null);
    setTurns([]);
    setSelected(null);
    setUnavailable(false);
    setError("");
    setVoiceAnswer(false);
  }, [
    w.tenant,
    w.profile.user_id,
    w.profile.role,
    w.profile.department,
    w.profile.semester,
    w.profile.section,
  ]);
  async function loadMoreDocuments() {
    if (nextOffset === null || loadingMore) return;
    const requestEpoch = epoch.current;
    setLoadingMore(true);
    setDocumentError("");
    try {
      const page = await api<DocumentPage>(
        `/v1/documents?limit=100&offset=${nextOffset}`,
        w.session,
        w.tenant,
      );
      if (requestEpoch !== epoch.current) return;
      setDocuments((old) => [
        ...new Map([...old, ...page.documents].map((d) => [d.id, d])).values(),
      ]);
      setNextOffset(page.next_offset);
    } catch (e) {
      if (requestEpoch === epoch.current)
        setDocumentError((e as Error).message);
    } finally {
      if (requestEpoch === epoch.current) setLoadingMore(false);
    }
  }
  async function changeSelection(document: SelectableDocument | null) {
    if (busy || opening || savingSelection) return;
    const requestEpoch = epoch.current;
    const request = ++openRequest.current;
    setSelected(document);
    setUnavailable(false);
    setError("");
    if (!conversation) return;
    setSavingSelection(true);
    try {
      const selection = await api<DocumentSelection>(
        `/v1/conversations/${conversation}/selection`,
        w.session,
        w.tenant,
        {
          method: "PATCH",
          body: JSON.stringify({ document_id: document?.id ?? null }),
        },
      );
      if (request !== openRequest.current || requestEpoch !== epoch.current)
        return;
      setSelected(selection.document);
    } catch (e) {
      if (request === openRequest.current && requestEpoch === epoch.current) {
        setUnavailable(true);
        setError((e as Error).message);
      }
    } finally {
      if (request === openRequest.current && requestEpoch === epoch.current)
        setSavingSelection(false);
    }
  }
  async function ask(question: string) {
    if (
      lock.current ||
      voiceBusy ||
      opening ||
      savingSelection ||
      unavailable ||
      !question.trim()
    )
      return;
    const requestEpoch = epoch.current;
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
        (delta) => {
          if (requestEpoch === epoch.current) setPartial((p) => p + delta);
        },
        abort.current.signal,
        selected?.id ?? null,
        language,
      );
      if (requestEpoch !== epoch.current) return;
      setConversation(answer.conversation_id);
      setSelected(answer.selected_document || null);
      setTurns((t) => [...t, { role: "assistant", answer }]);
      setHistoryRevision((n) => n + 1);
    } catch (e) {
      if (requestEpoch !== epoch.current) return;
      if (selected && e instanceof ApiError && e.status === 404)
        setUnavailable(true);
      if (!(e instanceof DOMException && e.name === "AbortError"))
        setError(
          e instanceof Error ? e.message : "Could not answer. Try again.",
        );
    } finally {
      if (requestEpoch === epoch.current) {
        setBusy(false);
        setPartial("");
        lock.current = false;
      }
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
    if (busy || savingSelection) return;
    setVoiceGeneration((n) => n + 1);
    setVoiceAnswer(false);
    const request = ++openRequest.current;
    const requestEpoch = epoch.current;
    setOpening(true);
    setError("");
    try {
      const [items, selection] = await Promise.all([
        api<{ role: string; content: Record<string, unknown> }[]>(
          `/v1/conversations/${id}`,
          w.session,
          w.tenant,
        ),
        api<DocumentSelection>(
          `/v1/conversations/${id}/selection`,
          w.session,
          w.tenant,
        ),
      ]);
      if (request !== openRequest.current || requestEpoch !== epoch.current)
        return;
      setConversation(id);
      const lastAnswer = [...items].reverse().find((m) => m.role === "assistant");
      setLanguage((lastAnswer?.content as unknown as Answer)?.language_preference || "auto");
      setSelected(selection.document);
      setUnavailable(selection.unavailable);
      setTurns(
        items.map((m) =>
          m.role === "user"
            ? { role: "user", text: String(m.content.text || "") }
            : { role: "assistant", answer: m.content as unknown as Answer },
        ),
      );
    } catch (e) {
      if (request === openRequest.current && requestEpoch === epoch.current)
        setError((e as Error).message);
    } finally {
      if (request === openRequest.current && requestEpoch === epoch.current)
        setOpening(false);
    }
  }
  async function remove() {
    if (!conversation || busy || savingSelection) return;
    try {
      await api(`/v1/conversations/${conversation}`, w.session, w.tenant, {
        method: "DELETE",
      });
      setTurns([]);
      setConversation(null);
      setSelected(null);
      setUnavailable(false);
      setHistoryRevision((n) => n + 1);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function newChat() {
    if (busy) return;
    setVoiceGeneration((n) => n + 1);
    setConversation(null);
    setVoiceAnswer(false);
    openRequest.current += 1;
    setOpening(false);
    setSavingSelection(false);
    setSelected(null);
    setUnavailable(false);
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
        <Button variant="outline" onClick={newChat} disabled={busy}>
          <Plus size={15} />
          New chat
        </Button>
      </div>
      <div className="conversation-bar">
        <div className="filter-field">
          <label htmlFor="response-language">Response language</label>
          <select id="response-language" value={language} disabled={busy || opening}
            onChange={(e) => setLanguage(e.target.value as typeof language)}>
            <option value="auto">Automatic</option>
            <option value="en">English</option>
            <option value="kn">ಕನ್ನಡ</option>
            <option value="hi">हिन्दी</option>
          </select>
        </div>
        <div className="filter-field">
          <label htmlFor="conversation">Your conversations</label>
          <select
            id="conversation"
            value={conversation || ""}
            onChange={(e) =>
              e.target.value ? void open(e.target.value) : newChat()
            }
            disabled={busy || savingSelection}
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
            disabled={busy || opening || savingSelection}
            aria-label="Delete this conversation"
          >
            <Trash2 size={17} />
          </Button>
        )}
      </div>
      <div className="document-selector">
        <div className="filter-field">
          <label htmlFor="chat-document">Search a document</label>
          <select
            id="chat-document"
            value={unavailable ? "unavailable" : selected?.id || ""}
            disabled={
              busy || opening || savingSelection || documentList.loading
            }
            onChange={(e) =>
              void changeSelection(
                documents.find((d) => d.id === e.target.value) || null,
              )
            }
          >
            <option value="">All authorized campus sources</option>
            {unavailable && (
              <option value="unavailable" disabled>
                Selected document unavailable
              </option>
            )}
            {selected &&
              !unavailable &&
              !documents.some((d) => d.id === selected.id) && (
                <option value={selected.id}>{selected.title}</option>
              )}
            {documents.map((d) => (
              <option key={d.id} value={d.id}>
                {d.title}
              </option>
            ))}
          </select>
        </div>
        {(selected || unavailable) && (
          <Button
            variant="outline"
            disabled={busy || opening || savingSelection}
            onClick={() => void changeSelection(null)}
          >
            Clear document selection
          </Button>
        )}
        {nextOffset !== null && (
          <Button
            variant="outline"
            onClick={loadMoreDocuments}
            disabled={busy || opening || savingSelection || loadingMore}
          >
            {loadingMore ? "Loading documents…" : "Load more documents"}
          </Button>
        )}
      </div>
      <p className="subtle document-scope" role="status">
        {documentList.loading
          ? "Loading authorized documents…"
          : unavailable
            ? "This document is unavailable. Choose another document or clear the selection to continue."
            : selected
              ? `Searching only: ${selected.title}`
              : !documents.length && !documentList.error
                ? "No published documents available. You can still ask general campus questions."
                : "Searching all authorized campus sources."}
      </p>
      <ErrorBox message={documentList.error || documentError} />
      {(documentList.error || documentError) && (
        <Button
          variant="outline"
          onClick={() => {
            setDocumentError("");
            setDocumentRevision((n) => n + 1);
          }}
        >
          Retry document list
        </Button>
      )}
      {opening && <p role="status">Opening conversation…</p>}
      {savingSelection && <p role="status">Saving document selection…</p>}
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
              {turn.answer && <SpokenAnswer text={answerSpeechText(turn.answer)} language={turn.answer.language || language} autoSpeak={voiceAnswer && i === turns.length - 1} />}
              {turn.answer?.answer_mode === "generated" && <p className="subtle">AI explanation based on the evidence below.</p>}
              {turn.answer?.answer_mode === "extractive" && <p className="subtle">Source excerpts are shown in their original language.</p>}
              {turn.answer?.generated_claims?.map((claim, ix) => (
                <blockquote key={ix}>{claim.quote}<footer>{claim.title} · Page {claim.page}</footer></blockquote>
              ))}
              {turn.answer?.sections?.map((section, ix) => (
                <section key={ix} aria-label={`Answer part ${ix + 1}`}>
                  <h3>Part {ix+1}: {section.question}</h3>
                  <p>{section.answer}</p>
                  {section.generated_claims?.map((claim, index) => <blockquote key={index}>{claim.quote}<footer>{claim.title} · Page {claim.page}</footer></blockquote>)}
                  <RecordList records={section.records} kind={section.kind} onSource={w.download} limit={4} />
                </section>
              ))}
              {turn.answer?.selected_document && (
                <p className="subtle">
                  From: {turn.answer.selected_document.title}
                </p>
              )}
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
                    disabled={s.download_allowed === false}
                    aria-label={
                      s.download_allowed === false
                        ? `${s.filename}: original restricted to institution administrators`
                        : undefined
                    }
                    title={`${s.demo ? "Demo Data · " : ""}Imported ${new Date(s.created_at).toLocaleDateString("en-IN")}`}
                  >
                    <FileText size={12} />
                    {ix + 1}. {s.title || s.filename}
                    {s.pages?.length ? ` · p. ${s.pages.join(", ")}` : ""}
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
        <VoiceControls key={`${voiceGeneration}:${w.tenant}:${w.profile.user_id}:${w.profile.role}:${w.profile.department}:${w.profile.semester}:${w.profile.section}:${language}:${conversation}:${selected?.id || ""}`} session={w.session} tenant={w.tenant} language={language}
          disabled={busy || opening || savingSelection || unavailable} onActivity={setVoiceBusy}
          onTranscript={(transcript) => { setInput(transcript); setVoiceAnswer(true); }} />
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
            placeholder="Ask anything about your campus..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            maxLength={2000}
            disabled={busy || voiceBusy || opening || savingSelection || unavailable}
          />
          <Button
            type="submit"
            disabled={
              busy || voiceBusy || opening || savingSelection || unavailable || !input.trim()
            }
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
