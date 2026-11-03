# Architecture

CampusOS is a modular monolith. Next.js is the presentation layer; FastAPI owns authorization, business rules and data access. The worker runs the same Python package in a separate process. MCP is another authenticated adapter to the same approved tool dispatcher.

```mermaid
flowchart TB
  Browser[Next.js · TypeScript · accessible student/admin UI]
  Auth[Supabase Auth · signed JWT]
  API[FastAPI · issuer/audience/signature/expiry verification]
  Scope[Active membership · tenant · department · owner · role]
  Chat[Conversation ownership · constrained query router]
  Tools[14 validated domain tools · call/time budgets]
  Domain[Timetable · meals · faculty · calendar · email]
  RAG[Permission-filtered hybrid retrieval · citations · expiry/conflicts]
  PG[(Supabase PostgreSQL · pgvector · FTS · RLS)]
  Store[(Private source storage)]
  Jobs[Database-backed ingestion queue]
  Worker[Worker · validate · normalize · chunk · embed · atomic commit]
  MCP[Official MCP SDK · stdio / authenticated HTTP]
  Gemini[Optional Gemini synthesis · validated citation IDs]
  Google[Optional read-only Google OAuth · encrypted tokens]
  Browser --> Auth
  Browser --> API
  API --> Scope
  Scope --> Chat --> Tools
  Scope --> Domain
  Scope --> Jobs
  Tools --> Domain
  Tools --> RAG
  Domain --> PG
  RAG --> PG
  RAG -. authorized passages only .-> Gemini
  Jobs --> Worker --> PG
  Worker --> Store
  MCP --> Scope
  MCP --> Tools
  Scope --> Google
```

## Data model

First-class relational tables: institutions, memberships, sources, documents, document_chunks, conversations, messages, ingestion_jobs, integration_connections, query_traces, audit_logs and rate_buckets.

`campus_records` stores Pydantic-validated version-one payloads for faculty, timetable entries, date exceptions, canteen items, events/exams/deadlines and authorized emails. Tenant, kind, natural key, owner, department and source are indexed relational columns. This keeps inconsistent input formats manageable while providing parameterized SQL filtering. Faculty codes join timetable records within the authorized tenant. Natural keys deduplicate/upsert structured records. This is an explicit vertical-slice tradeoff, not a claim that all academic entities already have dedicated normalized tables.

Campus, department, program, semester, section, course and classroom identifiers currently appear in validated domain payloads/memberships. Full administration and referential lifecycle management for these entities is future work. Supabase Auth owns users/passwords; memberships assign campus roles independently of client-supplied identity or JWT user metadata.

## Boundaries

1. Verify RS256/ES256 JWT signature using the configured issuer's JWKS, plus issuer, audience, expiry and subject UUID.
2. Resolve active membership from the verified subject and requested tenant. A header chooses among memberships; it never grants one.
3. Apply tenant, department and private-owner predicates before queries and retrieval. Institution/platform admins remain tenant-bound, and cannot read another owner's private email/calendar.
4. Set transaction-local database identity. PostgreSQL RLS independently restricts the `campus_api` role.
5. Validate every tool input with Pydantic and call fixed service methods. No generated SQL, shell commands or Python execution exists.
6. Cite sources after checking source access. Download originals through an authenticated API route as attachments.

## Retrieval

Chunks preserve page number, document version, publication/expiry, topic/claim and original source. Chunks also record the nearest section heading. Embeddings always have 1024 dimensions. They come from an Ollama model such as `bge-m3`, then a local sentence-transformers directory, then deterministic lexical hashing, which is not presented as semantic intelligence. A configured model that is unavailable fails the import rather than storing invented vectors. The application never downloads a model implicitly.

PostgreSQL retrieves bounded pgvector cosine candidates plus full-text candidates and merges them with Reciprocal Rank Fusion (k=60). An optional local cross-encoder then reorders them. If the embedding model is down at query time, search falls back to keywords and reports `keyword-only`. SQLite uses the same permission-filtered corpus with local ranking. Similarity is a ranking signal, not a confidence probability. Changing models requires re-indexing; mixed-model vectors are not compared. A model fingerprint includes its configured directory name; deployments must not replace model contents in place without re-indexing.

Conflict detection compares **recorded claims for a shared topic**, retaining unresolved alternatives. Explicit supersession is recognized. Arbitrary natural-language contradiction detection is not implemented. Expired documents are excluded unless explicitly requested.

A local Ollama model or Gemini can summarize already-authorized passages. Output must match a schema and cite only supplied source IDs. Failures fall back to extractive passages. This validates provenance, not the factual truth of every generated sentence. Source content never controls tool selection or execution.

With `ROUTING_MODE=hybrid`, longer or unmatched questions go to a bounded tool-calling agent (`backend/app/agent.py`, at most 4 steps) that uses the same approved tools. It refuses when no tool result grounds the answer, and failures fall back to the deterministic rules.

## Voice input

`POST /v1/speech/transcribe` runs faster-whisper on the CPU. Audio is held in memory only and is never stored or logged. The transcript goes into the question box to be checked; it is never sent automatically.

## Ingestion and jobs

Files are bounded, checksummed and stored privately. Structured preview detects columns, accepts mapping and reports row-level validation. Confirmation queues a job. The worker claims with `FOR UPDATE SKIP LOCKED` on PostgreSQL, rechecks the uploader's current membership, and commits domain changes plus job completion atomically. Processing rows are locked during extraction/indexing. A ten-minute claim lease permits recovery from a dead worker. Failed parsing preserves prior institutional records.

An unchanged upload by the same user/kind returns its existing job. Structured natural keys avoid duplicate records across different file versions. Documents retain source/version lineage. Re-indexing replaces the same document's chunks. Large PDFs are extracted in the worker; preview shows metadata before confirmation. Local development should run one SQLite worker.

## Operations

API logs contain request ID, method, status and latency, without query strings or content. Traces contain route/tool names and timings, without question text. Conversation bodies are private persisted data, not application logs. Rate limiting uses atomic database upserts and is shared across API processes. Background processing and Gemini/Google requests use bounded inputs and timeouts. The between-tool execution budget is not an operating-system hard timeout for one expensive parse; use process/container resource limits in production.
