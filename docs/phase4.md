# Phase 4: campus routing, languages and grounded answers

> Historical phase notes: current setup/schema is **0009**. The mandatory second-reviewer requirement was superseded by scoped explicit same-admin approval in 0009. Original-machine revision/status statements and artifact links below are historical, not the state of a teammate checkout. See [current setup](../README.md), [publication](publication.md), [verification](verification.md) and [limitations](limitations.md).

## Audit and engineering decisions

The existing application already had verified memberships, SQLAlchemy domain
queries, fourteen schema-validated tools shared with MCP, publication gates,
private originals, durable ingestion, selected-document retrieval and optional
Gemini synthesis. It had no LangGraph integration. Its handwritten router handled
clear English queries well, so we extended it instead of adding an agent graph.
LangGraph has **no role in the running application**. The model never owns a
database connection, generates executable SQL or chooses an authorization scope.

The audit found four concrete gaps: generation could send private evidence to
Gemini merely because a key existed; unknown named faculty could return a broad
department directory; subject lookup was not tied to the student's section; and
there was no multilingual or multi-part orchestration. Source IDs alone also did
not establish which chunk supported a generated sentence.

No schema changes or migrations are needed. The original development SQLite
remains at `0004`. Database verification backs it up using SQLite's backup API,
migrates only copies, and compares schema and row digests. Existing Phase 1–3
publication, storage and RLS controls remain the authority.

## Code map

| File / function | Responsibility |
| --- | --- |
| `backend/app/router.py`: `Router.ask`, `_ask_single`, `generate` | Normalize language, bounded multi-part routing, context, optional advisory plans, grounded synthesis and fallback |
| `backend/app/language.py`: `normalize`, `localize` | Common campus vocabulary in Kannada/Hindi/romanized forms; controlled response labels without inventing translations of records |
| `backend/app/ai.py`: `get_provider`, `JsonProvider.allowed/request/plan/synthesize` | Explicit provider choice, privacy gates, bounded JSON contracts and safe failure |
| `backend/app/ai.py`: `validate_grounded` | Validate retrieved chunk IDs, exact supporting quotes and numeric values |
| `backend/app/services.py`: `faculty`, `local_now`, `next_class` | Verified section teaching assignments, name-only lookup and campus-zone time |
| `backend/app/tools.py`: `FacultyInput`, `NextClassInput`, `ToolKit.call` | Typed inputs; the same fourteen tools and one eight-call budget |
| `backend/app/retrieval.py`: `Retrieval.search` | Existing authorization-first retrieval; server-derived private evidence classification |
| `backend/app/main.py`: `answer_chat`, `change_conversation_selection` | Persist language preference alongside authorized conversation selection |
| `backend/app/config.py`, `schemas.py`, `.env.example`, `config/staging/*.env.example` | Validated provider/policy settings, optional chat language, disabled-by-default templates; no development credential changes |
| `frontend/src/components/chat-view.tsx`, `frontend/src/lib/api.ts` | Language selection/restoration, multi-part results, generated explanation and quoted evidence |
| `backend/tests/test_ai_providers.py`, `test_phase4.py`, `fixtures/campus_queries.json` | Mock provider contracts, routing/security/grounding tests and reproducible evaluation |
| `scripts/verify_databases.py`, `verify_frontend.py`, process fixtures | Force providers off during isolated regression runs |

Executed results and preservation evidence are in [the Phase 4 verification report](verification.md).

## Student question to answer

1. The Supabase browser client supplies a JWT. FastAPI verifies signature, issuer,
   audience and expiry, then resolves an active membership in `auth.py`.
2. `answer_chat` verifies conversation ownership and document selection. A change
   of membership scope invalidates old routing context. Changing documents clears
   the previous document's question anchor but preserves a valid language choice.
3. `normalize` interprets common campus words, Unicode digits and course aliases.
   Automatic detection recognizes scripts and a small romanized vocabulary.
   Proper names, codes, dates, rooms and actual returned records remain as stored.
4. Selected-document mode wins over all other routing. Otherwise, clear queries
   use deterministic rules. Conjoined recognizable campus requests split into at
   most four parts; each receives the context produced by the previous part.
5. Uncertain campus questions, and long complex campus questions not already
   split, may request a schema-constrained advisory plan. Intents map to known
   server-side query triggers. Unknown intents, extra fields or invalid JSON
   reject the plan. The plan cannot supply tenant/user/document IDs or raw SQL.
6. All parts share one `ToolKit`: at most eight tool calls and a twenty-second
   tool window. At most two provider requests occur per question, each with a
   configurable timeout capped at twelve seconds. There are no automatic model
   retries. Exhausted budgets ask the student to narrow the request.
7. Domain queries use `CampusService.scope` and the verified actor. PostgreSQL RLS
   provides a second boundary under the restricted API role. A model plan never
   replaces these checks. SQLite development uses application authorization.
8. Results include structured records or evidence, authorized sources, page/chunk
   metadata and an explicit answer mode. The UI renders multi-part record cards
   and separates an AI explanation from its exact supporting quotes.

The document path retains the existing hybrid search: permission and publication
filters precede lexical/vector ranking and conflict expansion. Selected-document
filters apply throughout. PostgreSQL stores 384-dimensional vectors through
pgvector; SQLite ranks authorized candidates locally. The default hashing vector
is lexical, **not multilingual semantic understanding**. An installed compatible
local sentence-transformer can replace it after reindexing. A model cannot make
an inaccessible source retrievable.

## SQL versus documents: examples

| Question | Execution |
| --- | --- |
| Who teaches Operating Systems to my section? | Effective own-section timetable → referenced faculty codes → authorized directory; never a department-wide guess |
| When is their next class? | Resolved faculty code + student's timetable, up to fourteen days ahead; unresolved pronoun returns no guessed faculty |
| Where is their office? | Published directory and prior faculty code; explicitly named faculty overrides context |
| Is there a recent notice involving this faculty member? | Resolve directory/context, then authorized documents; require the resolved surname in returned passages |
| Professor Nobody Everknown | Name-only lookup with no broad department fallback |
| Faculty research/publications | Authorized document evidence, without inventing missing biography/contact details |
| Vegetarian food under 100 and my next class | Two typed SQL operations sharing one budget |
| Library policy / selected handbook | Authorized document retrieval, optional grounded generation |
| ಇಂದು ನನ್ನ ತರಗತಿಗಳು / आज मेरी classes | Deterministic timetable routing, Kannada/Hindi labels |
| Explain black holes | Unsupported campus request; no general tutoring answer |

`local_now` converts aware clocks into the institution timezone (Asia/Kolkata by
default). Today/tomorrow/weekdays and current-class filtering use campus time.
Current class is a time interval query; next class is a future start-time query.
Hindi `कल`/romanized `kal` means tomorrow in this bounded vocabulary; use an ISO
date or an explicit yesterday phrase when the past is intended.

## Providers and privacy policy

Providers remain **off by default**, including when an old Gemini key exists.
`.env.example` documents these settings; do not copy secrets into documentation.

| Setting | Meaning |
| --- | --- |
| `LLM_ENABLED=false`, `LLM_PROVIDER=none` | Required no-provider fallback; opt in explicitly to `gemini`, `openrouter` or `ollama` |
| `LLM_DATA_POLICY=disabled` | No model requests |
| `shared_only` | Only explicitly shared payloads; private passages fail closed |
| `private_local` | Private payloads allowed only to the explicitly configured loopback Ollama adapter |
| `approved_private` | Private payloads require an approved policy; external requests also require a nonempty `LLM_POLICY_REFERENCE` |
| `LLM_POLICY_REFERENCE` | Operator-controlled record of approval, **not** a user-provided consent bypass |
| `LLM_TIMEOUT_SECONDS=8` | Per-call timeout, validated range 0.1–12 seconds |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Existing Gemini API configuration |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | Explicit OpenRouter configuration; requests require structured-output support and deny provider data collection |
| `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Both required; loopback host only, no redirects, URL credentials, query or fragment |

Raw student questions can contain private facts even when the apparent topic is
public. Therefore routing/generation treats the question-bearing request as
private: cloud chat requires `approved_private` and a policy reference;
`shared_only` alone does not enable cloud chat. This deliberately conservative
choice avoids relying on a keyword classifier as a privacy boundary. Private
evidence is also classified from server-side ownership, never a browser flag.
Only the current question and permitted evidence are supplied; no JWT, membership,
email inbox or full conversation history is included in model payloads.

A policy reference is an operator assertion, not legal approval or technical
proof of provider retention guarantees. Setting one requires the institution's
actual review. No approval or credential is assumed by this implementation.
Existing Ollama/Honeywell resources are never discovered, started, pulled,
stopped or modified. Use a separately approved endpoint for live tests.

All adapters use the existing HTTP client with JSON schemas, bounded output,
timeouts and redirects disabled. No new SDK, paid dependency or mandatory model
download is introduced. Responses with HTTP errors, malformed JSON, unknown
intents, foreign citations or fabricated quantities fall back safely. Statuses
distinguish disabled, blocked, unavailable and invalid output.

Contract references: [Gemini structured output](https://ai.google.dev/gemini-api/docs/generate-content/structured-output),
[OpenRouter chat API](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion),
[Ollama chat API](https://docs.ollama.com/api/chat). Public documentation was
consulted; these references do not constitute live integration verification.

## Grounding, abstention and multilingual limits

Each generated claim must identify a retrieved, non-suspicious chunk and give an
exact supporting quote. Its numeric values must occur in that chunk. The server
attaches the original title/page/document/source metadata after validation.
Prompts mark passages as untrusted, known injection patterns exclude passages
from generation, and selected-document conflict expansion cannot introduce other
documents. Conflicting claims retain the existing extractive warning. Missing
evidence or an explicit model abstention yields no invented institutional fact.

These checks establish traceable evidence, **not semantic entailment**. A model
could attach a real quote to an unsupported nonnumeric paraphrase. Injection
patterns are defense in depth, not complete prompt-injection detection. Human
review and a separately approved live multilingual evaluation are still required
before claiming production answer quality.

Without a provider, English/Kannada/Hindi campus vocabulary and common romanized
queries use deterministic routing. Kannada/Hindi replies use fixed labels; record
values and source excerpts remain in their original language. This is useful
bounded support, not fluent universal translation. Names written in an unrelated
script are not automatically transliterated into directory names. Optional models
can interpret broader phrasing and generate requested-language explanations once
policy and credentials are approved. Do not advertise universal language support
from the small synthetic evaluation.

## Verification and demonstration

From the inner repository root (`CampusBuddy/CampusBuddy` in this workspace):

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_databases --postgres
.\.venv\Scripts\python.exe -m ruff check backend scripts
.\.venv\Scripts\python.exe -m scripts.verify_frontend
git diff --check
```

The first command backs up the original read-only, upgrades disposable SQLite
copies, creates one isolated tmpfs/loopback PostgreSQL container with generated
test-only credentials, runs migrations/RLS/full backend tests and removes only
that container. It checks pre-existing Docker inventory. The frontend runner
uses fake local identities and fresh data on unused loopback ports, performs TS,
production build and browser tests, then restores generated Next type references.
Both force all model providers off. No production or staging service is used.

For a focused evaluation with no original SQLite writes:

```powershell
$env:DATABASE_URL='sqlite:///:memory:'
.\.venv\Scripts\python.exe -m pytest backend/tests/test_ai_providers.py backend/tests/test_phase4.py backend/tests/test_router_evaluation.py -q --junitxml=artifacts/phase4-focused.xml
```

`artifacts/phase4-evaluation.json` records actual routing and source-authorization
results for 24 synthetic questions, quote/ID/quantity validation controls and a
private-document denial. It explicitly excludes semantic entailment measurement.
Provider tests replace HTTP transport with mocks; they never contact a model.
The original 35 routing cases remain as a separate regression corpus. Backend
JUnit and browser JSON reports record final counts; overlapping runs are not
added together. See the final verification report for actual results.

Manual local demonstration (using fixture accounts, not real credentials): run
the frontend verification runner to exercise these actions automatically. For an
interactive development session use the documented isolated fixture process in
`backend/tests/e2e_server.py`, a free loopback port and a fresh fixture directory;
do not point fixture seeding at an existing institutional database.

1. Sign into the local fixture as a student; open Ask CampusOS.
2. Ask who teaches Machine Learning, then their next class and office. Verify the
   section, directory evidence and unchanged faculty name.
3. Ask for that teacher and their office in one question. Inspect both result parts.
4. Select Hindi, ask an English query, reopen the conversation and check that the
   preference persists. Switch to automatic and ask `ಇಂದು ನನ್ನ ತರಗತಿಗಳು`.
5. Select Selection Handbook Alpha and ask about its Zirconium scholarship.
   Follow up and check every citation stays in Alpha. Switch to Beta, then clear.
6. Ask for nonexistent faculty or unsupported study content. Verify honest missing
   information. With providers disabled, excerpts still work and no model is called.

Live acceptance still needs separately approved staging accounts/credentials:
Supabase login/refresh/key rotation and membership revocation; actual private
bucket upload/download denial; deployed API/worker separation, restarts and
concurrent claims; each model's JSON-schema compatibility, multilingual quality,
latency and retention policy. No staging approval has been granted for this work.

## Interview questions

**Why not let the model write SQL?** A generated query could read beyond the
student's scope or perform a mutation. Here the model selects a small intent;
typed tools and the domain service retain control of queries and permissions.

**Why both service filters and RLS?** Service checks work across SQLite/PostgreSQL
and make intent explicit. RLS protects PostgreSQL when an application query misses
a predicate, provided the API uses its restricted role rather than an owner role.

**Does a valid citation prove an answer?** No. It proves a retrieved chunk exists.
Exact quotes and numeric checks strengthen traceability; factual entailment still
requires model evaluation or human judgment.

**Why not invoke an LLM on every query?** A timetable lookup needs SQL and campus
time, not generation. Deterministic paths reduce latency, expense and transmitted
data, while preserving operation without an external provider.

**Why not LangGraph?** There is no existing graph to preserve. Four bounded parts,
fourteen typed tools and a two-call model budget do not require another runtime.
Adding one would add complexity without fixing authorization or evidence quality.

**What happens if the model fails midway?** Completed deterministic queries remain
available; generation falls back to authorized excerpts. Invalid plans do not run
SQL. There are no writes to institutional data in the answer pipeline.
