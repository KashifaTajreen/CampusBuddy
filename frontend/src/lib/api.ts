import { createClient, type Session } from "@supabase/supabase-js";
// The SDK consumes callback URL errors during initialization, before React effects.
export const AUTH_CALLBACK_ERROR = typeof window === "undefined" ? "" : (
  new URLSearchParams(window.location.hash.slice(1)).get("error_description") ||
  new URLSearchParams(window.location.search).get("error_description") || ""
).slice(0, 300);
const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
export const supabase =
  url && key
    ? createClient(url, key, {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: true,
        },
      })
    : null;
export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
// Public enrollment scope configured by the institution; it grants no membership.
export const ONBOARDING_TENANT = process.env.NEXT_PUBLIC_ONBOARDING_TENANT_ID || "";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  session: Session,
  tenant: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(API + path, {
    ...options,
    cache: "no-store",
    headers: {
      Authorization: `Bearer ${session.access_token}`,
      "X-Tenant-ID": tenant,
      ...(!(options.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "The server could not be reached." }));
    throw new ApiError(
      typeof error.detail === "string"
        ? error.detail
        : "Check your information and try again.",
      response.status,
    );
  }
  return response.json() as Promise<T>;
}
export type DataRecord = {
  id?: string;
  source_id?: string;
  source_ids?: string[];
  demo?: boolean;
  [key: string]: unknown;
};
export interface Citation {
  id: string;
  filename: string;
  demo: boolean;
  created_at: string;
  url: string;
  download_allowed?: boolean;
  title?: string;
  document_id?: string;
  pages?: number[];
  chunk_ids?: string[];
}
export interface SelectableDocument {
  id: string;
  title: string;
  source_id: string;
  category: string;
  published: string;
  expires: string | null;
}
export interface DocumentPage {
  documents: SelectableDocument[];
  next_offset: number | null;
}
export interface DocumentSelection {
  document: SelectableDocument | null;
  unavailable: boolean;
}
export interface Answer {
  question?: string;
  language?: "en" | "hi" | "kn";
  language_preference?: "auto" | "en" | "hi" | "kn";
  answer_mode?: "structured" | "extractive" | "generated" | "abstained";
  provider_status?: string;
  sections?: Answer[];
  generated_claims?: { text: string; quote: string; chunk_id: string; title: string; page: number }[];
  answer: string;
  kind: string;
  records: DataRecord[];
  sources: Citation[];
  followups: string[];
  conversation_id: string;
  selected_document?: SelectableDocument | null;
  evidence_status?: "retrieved_evidence" | "insufficient_evidence" | null;
}
export interface Profile {
  user_id: string;
  tenant_id: string;
  name: string;
  role: string;
  department: string;
  semester: number;
  section: string;
  diet: string;
  interests: string;
  institution: string;
  demo: boolean;
  timezone: string;
  needs_onboarding?: boolean;
}
export interface Membership {
  tenant_id: string;
  institution: string;
  role: string;
  name: string;
  demo: boolean;
  needs_onboarding?: boolean;
}
export interface Overview {
  next_class: DataRecord | null;
  classes: DataRecord[];
  notices: DataRecord[];
  events: DataRecord[];
  exams: DataRecord[];
}
export interface Job {
  id: string;
  source_id: string;
  filename?: string;
  demo?: boolean;
  kind?: string;
  department?: string;
  semester?: number | null;
  section?: string;
  source_status: string;
  status: string;
  error: string | null;
  attempts: number;
  created_at: string;
  preview: {
      needs_ocr?: boolean;
      schema_mismatch?: boolean;
      suggested_kind?: string;
      error_groups?: {message:string;count:number;rows:number[]}[];
    duplicate_check?: {checked: boolean; blocking: boolean; warnings: string[]; exact_sources: string[]; conflicting_sources: string[]};
    ocr?: boolean;
    ocr_reviewed?: boolean;
    ocr_pages?: number[];
    ocr_issues?: { line?: number; page?: number; message: string }[];
    detected_type?: string;
    format?: string;
    warnings?: string[];
    editable_rows?: DataRecord[];
    course_mapping_text?: string;
    skipped_count?: number;
    skipped_rows?: {sheet?:string;row:number;reason:string}[];
    requires_demo?: boolean;
    overlap_warnings?: string[];
      parallel_records?: {day:number;start_time:string;subject:string}[];
    overlap_reviewed?: boolean;
    defaults?: {effective_from?:string;effective_to?:string;menu_date?:string;menu_location?:string};
    columns?: string[];
    fields?: { name: string; required: boolean }[];
    mapping?: Record<string, string>;
    records?: DataRecord[];
    raw?: DataRecord[];
    errors?: { row: number; message: string }[];
    error_count?: number;
      review_count?: number;
      review_errors?: {row:number;message:string;fields?:string[]}[];
      review_rows?: number[];
      ready_only?: boolean;
      published_count?: number;
      event_workflow?: boolean;
      event_reviewed?: boolean;
      event_index_ready?: boolean;
      event_rows?: DataRecord[];
      event_pages?: {page:number;text:string}[];
      indexed_chunks?: number;
      embedding_model?: string;
      qr_supported?: boolean;
      qr_url?: string | null;
    valid_count?: number;
    imported_count?: number;
    message?: string;
    passages?: { page: number; text: string }[];
      editable_pages?: { page: number; text: string }[];
  };
}

export interface SourceInfo {
  student_records?: boolean;
  publication_status?: string;
  version?: number | null;
  replaced_by?: string | null;
  semester?: number | null;
  section?: string;
  file_state?: "active" | "deleted" | "purge_pending" | "purged";
  id: string;
  filename: string;
  kind: string;
  department: string;
  demo: boolean;
  created_at: string;
  status: string;
  approved_by: string | null;
  published_by: string | null;
  review_note: string;
  replaces_source_id: string | null;
  original_download_allowed: boolean;
  actions: ("approve" | "publish" | "reject" | "revoke")[];
}

export async function streamChat(
  question: string,
  conversation: string | null,
  session: Session,
  tenant: string,
  onDelta: (text: string) => void,
  signal?: AbortSignal,
  documentId?: string | null,
  language?: "auto" | "en" | "hi" | "kn",
): Promise<Answer> {
  const response = await fetch(API + "/v1/chat/stream", {
    method: "POST",
    signal,
    headers: {
      Authorization: `Bearer ${session.access_token}`,
      "X-Tenant-ID": tenant,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      question,
      conversation_id: conversation,
      document_id: documentId,
      language,
    }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new ApiError(
      error.detail || "Could not answer your question. Try again.",
      response.status,
    );
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Streaming is unavailable. Try again.");
  const decoder = new TextDecoder();
  let buffer = "";
  let result: Answer | undefined;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      const event = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const data = event.split("\n").find((line) => line.startsWith("data: "));
      if (!data) continue;
      const payload = JSON.parse(data.slice(6));
      if (event.startsWith("event: delta")) onDelta(payload.text);
      if (event.startsWith("event: result")) result = payload as Answer;
    }
  }
  if (!result)
    throw new Error(
      "The response was interrupted. Your conversation is saved; reopen it to recover.",
    );
  return result;
}

export function campusDate(timezone = "Asia/Kolkata") {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  return `${parts.find((p) => p.type === "year")?.value}-${parts.find((p) => p.type === "month")?.value}-${parts.find((p) => p.type === "day")?.value}`;
}
export function prettyDate(value: unknown) {
  if (!value) return "";
  const s = String(value);
  return new Date(s.length === 10 ? s + "T12:00:00" : s).toLocaleDateString(
    "en-IN",
    { day: "numeric", month: "short", weekday: "short" },
  );
}
export function text(value: unknown) {
  return value === null || value === undefined ? "" : String(value);
}
