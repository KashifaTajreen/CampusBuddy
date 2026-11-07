# Phase 5: secure ingestion and administrative file management

> Historical phase notes: current setup/schema is **0009**. The mandatory second-reviewer requirement was superseded by scoped explicit same-admin approval in 0009. Original-machine revision/status statements and artifact links below are historical, not the state of a teammate checkout. See [current setup](../README.md), [publication](publication.md), [verification](verification.md) and [limitations](limitations.md).

## Audit and scope

CampusOS already had verified Supabase JWTs and memberships, tenant/department/
owner filters, restricted PostgreSQL RLS, private originals, durable worker jobs,
independent approval, atomic publication/replacement, hybrid retrieval, selected
documents and privacy-gated model adapters. Those components remain in use.

The audit found CSV/XLSX/JSON structured imports, PDF/TXT/Markdown documents and
column mapping. Document preview showed only metadata; structured rows could not
be corrected in the UI. There were no DOCX/PPTX readers or file deletion/restore
operations. Phase 5 implementation left the original development SQLite at `0004`.
Following separate approval on November 2, it was backed up and upgraded to `0005`,
preserving all pre-existing application values. The execution report is
`artifacts/development-migration-0005.json`; the earlier Phase 5 test report remains
a historical record of pre-migration verification.

No new framework, SDK, package, external model or OCR download was introduced.
DOCX/PPTX use bounded OOXML readers; PDF and image processing reuse PyMuPDF;
XLSX reuses pandas/openpyxl. Institutional uploads still require an authorized
administrator membership. A `faculty` or student role alone receives no new
shared-write privilege; delegation uses the existing administrator membership
mechanism. Owners may manage their own private files, without giving administrators
access to other students' private content.

## Files and functions

| File | Purpose |
| --- | --- |
| `backend/app/parsing.py`: `parse_file`, `parse_inline`, `archive`, `table_rows`, `detect_kind` | Isolated parsing, actual-format checks, resource bounds, tables and advisory type detection |
| `backend/app/ingestion.py`: `receive`, `preview`, `validate_records`, `process`, `natural_key` | Pre-storage validation, editable corrected rows, duplicate/interval checks, provenance, revalidation and restored indexing |
| `backend/app/schemas.py`: `TimetableData`, `ImportOptions`, `MappingRequest`, `FileAction` | Lab-batch labels, bounded correction payloads and explicit lifecycle action contract |
| `backend/app/files.py`: `Files.change`, `_transition` | Authorization, deletion/restore/rollback, durable purge stages and content cleanup |
| `backend/app/storage.py`: `ObjectStore.delete` | Exact-key private object deletion and idempotent local cleanup |
| `backend/app/models.py`: `Source.file_state` | Independent file state, separate from publication approval |
| `backend/migrations/versions/0005_file_lifecycle.py`: `upgrade` | Additive SQLite/PostgreSQL schema migration and restricted PostgreSQL lifecycle function |
| `backend/app/publication.py`: `sources`, `describe`, `_transition` | Paginated management metadata and prevention of publishing deleted files |
| `backend/app/services.py`: `scope`, `original_source` | Active-file requirement before search/download visibility |
| `backend/app/retrieval.py`: `extract_pages` | Reuse the isolated parser; retain existing bounded chunking and authorized hybrid ranking |
| `backend/app/main.py`: `admin_files`, `file_action`, `update_mapping`, `job_json`, `reindex_source` | Authorized REST actions, pagination and deleted-preview suppression |
| `frontend/src/components/admin-view.tsx`, `frontend/src/lib/api.ts` | File pages, corrections, warnings, restore/rollback and permanent confirmation UI |
| `backend/app/config.py`, `.env.example`, staging templates | `OCR_ENABLED=false` by default |
| `backend/tests/test_file_parsing.py`, `test_files.py`, `test_file_migration.py` | Real format fixtures, lifecycle/API denials, recovery and migration preservation |
| Existing publication, storage, PostgreSQL and browser tests | Deletion concurrency, private storage contract and regression coverage |
| `scripts/verify_databases.py` | Verify all original column values survive additive `0005` on a disposable copy |

## Upload to published information

1. FastAPI verifies the JWT and active membership. Shared uploads call
   `check_department_write`; private imports are restricted to the owner and
   supported personal kinds. The request cannot grant itself a role or tenant.
2. `receive` checks extension/size, checksum and authorized idempotency. A parser
   subprocess checks actual content **before storing the original**. Malformed
   input does not create a stored orphan. Valid files with invalid domain rows
   remain drafts with visible validation errors.
3. Format-specific extraction produces logical pages and header-based rows.
   A deterministic header heuristic proposes timetable/menu/faculty/event/document
   type. This suggestion never changes the administrator's selected import kind
   or permissions automatically.
4. The preview shows format/type, rows, mapping, warnings and errors. Administrators
   may edit extracted rows as JSON, including department, section and `lab_batch`,
   then validate again. Corrections are explicitly stored in job options, separate
   from original bytes. The audit records the actor/job/count, not copied cells.
5. The same Pydantic schemas validate preview and worker input. Faculty codes must
   resolve; dates/times/prices must be valid; duplicate natural keys and overlapping
   timetable intervals within an import block confirmation. Existing published
   key matches produce an update warning. No subset of an invalid import publishes.
6. Confirmation queues a persistent job. The worker re-resolves current membership,
   locks source/job, verifies active state and original checksum, reparses and
   revalidates corrected rows. Structured records are staged, not activated.
   Documents receive chunks and vectors behind the unpublished source boundary.
7. Successful shared processing moves to `pending_review`. A different authorized
   administrator approves; a currently authorized publisher publishes. The existing
   publication service/SQL function rechecks participants and activates atomically.
   Private data becomes readable only to its owner after successful processing.
8. Replacements keep the current version available until the new version passes
   processing, review and publication. Failure never partially replaces valid data.

### Supported formats and exact limits

| Format | Handling and limits |
| --- | --- |
| PDF | Check PDF signature; reject locked files; at most 200 pages; extract text and ruled/header tables; support pipe/tab-delimited text tables |
| CSV | UTF-8, consistent row width, unique nonempty headers; no spreadsheet formula execution |
| XLSX | Validate OOXML package; bounded archive expansion; openpyxl through pandas, external workbook links disabled; include all worksheets with sheet-number locations (at most 200) |
| JSON | List of row objects; no arbitrary object-as-table guessing |
| DOCX | Paragraph text and tables from `word/document.xml`; no macros, links or embedded code execution |
| PPTX | Slide text and tables, slide number as source location; at most 200 slides |
| TXT / Markdown | UTF-8 text; pipe/tab-delimited tables and ordinary notice passages |
| PNG / JPEG / scanned PDF pages | Signature/dimension checks, then opt-in local PyMuPDF/Tesseract OCR; otherwise clear unsupported-scan error |

All files are nonempty and at most 15 MB. Office packages have at most 2,000
members, 80 MB total declared expansion, 20 MB per member, no encrypted entries,
macros, unsafe member paths or XML entities/DOCTYPE. Extracted text is at most
2,000,000 characters; serialized rows/corrections at most 4 MB; at most 100 columns
and 10,000 rows (1,000 for timetable validation). Image/page area is bounded.

Each parser subprocess has a 30-second parent timeout and receives no application
credentials, provider settings or database connection. POSIX adds CPU/address-space
limits. On Windows, timeout/input/output/format limits apply; a hard OS RAM limit
and filesystem/network sandbox are **not** provided by this subprocess mechanism.
Production should impose container/process memory and network restrictions.
Parsing in another process contains routine crashes but is not a security sandbox
for a compromised native parser. Embedding execution still uses the trusted worker;
deployment resource limits remain necessary.

OCR is disabled by default. No real OCR engine/language data was installed or
verified here. The mocked OCR contract test proves fallback wiring only. Mixed
PDFs with an unsupported scanned page reject the entire import rather than
silently omitting that page. OCR text requires manual review. Default OCR language
is the locally available English engine configuration; multilingual OCR quality
is not claimed. See [PyMuPDF OCR/table contracts](https://pymupdf.readthedocs.io/en/latest/page.html).

DOCX has no reliable rendered page boundaries without a layout engine. Its
reported page 1 is a **logical document location**, with an explicit preview
warning. PPTX locations are slide numbers. No `.doc`, `.ppt`, `.xls`, macro-enabled
Office files or arbitrary image formats are supported. Arbitrary timetable grids,
merged headers, handwritten scans and free-form menus may need a header-based
export or manual correction; the application never invents absent faculty codes,
dates or section assignments. Lab-batch labels are preserved, but membership has
no individual batch assignment: students see authorized section rows with their
batch labels, not an inferred personal batch schedule.

## Chunking, embeddings and retrieval

PDF table extraction appends rows as `header: value` text with source page numbers.
This keeps column meaning available when tables become search evidence. CSV/XLSX/
JSON document imports similarly retain labeled row values. Existing chunking uses
about 1,000 characters, prefers newline/sentence boundaries and overlaps 120
characters. Title, source, logical page/slide and document version remain metadata.
This is a bounded improvement, not a general layout reconstruction engine.

Default 384-dimensional hashing vectors are lexical features, not dense semantic
understanding. PostgreSQL combines pgvector candidate ranking and full-text search;
SQLite ranks authorized candidates locally with lexical/TF-IDF scoring. An
operator-supplied compatible local sentence-transformer supports dense embeddings
after explicit reindexing. No model was downloaded or externally called.

Reindexing computes replacement chunks/vectors before deleting old chunks and
commits the replacement atomically with job success. A failed transaction retains
the previous valid index. Every search and conflict expansion still uses tenant,
department, owner, publication and selected-document filters **before ranking**.
There is no corpus/result cache to invalidate; the model-object cache contains
model weights, not student passages.

## File lifecycle and recovery

`file_state` is independent of `publication_status`:

| Action | Result |
| --- | --- |
| Soft-delete | `file_state=deleted`, publication revoked; remove chunks/vectors and linked structured rows; cancel jobs; redact dependent saved assistant answers and clear selected-document context |
| Restore | Only eligible `deleted` originals; return to active draft, clear approval/publisher identity, create a new preview job under the current uploader; require processing and independent review again |
| Roll back | Return an eligible replaced source to draft, identifying the current matching published source; the current source stays readable until the older content is reprocessed, independently approved and published |
| Begin permanent deletion | Require exact `DELETE <source_id>` confirmation and deleted state; commit a durable `purge_pending` tombstone before storage deletion |
| Finish permanent deletion | After storage success, remove document metadata and all job extraction/options/staging data; scrub original filename/key/checksum, retain minimal source/audit tombstones as `purged` |
| Storage or final database failure | File stays hidden; explicitly retry permanent deletion; no automatic destructive retry or republishing |

Soft deletion retains original bytes and import options so restoration is possible.
Purged and purge-pending files cannot be restored. An original without recoverable
import metadata requires a new upload. Purge uses the storage API, not SQL deletion
of storage metadata. Local deletion is exact-path checked and idempotent. Supabase
deletion uses one exact object key; the HTTP adapter is mock-tested, not live-tested.
See [Supabase object deletion](https://supabase.com/docs/guides/storage/management/delete-objects).

The database and object store do not share a transaction. The durable tombstone
closes the visibility gap; it does not claim distributed atomic commit. If object
deletion succeeds but final database cleanup fails, an explicit retry is safe.
Concurrent finalization can return a conflict; refresh state before retrying.
No original-file deletion occurs automatically on upload, rejection or replacement.

Rollback reactivates an existing content version after a fresh review, rather than
inventing a new immutable document. The document ID/content-version identity remains;
chunk IDs are rebuilt. `replaces_source_id` is the target of the most recent
activation; it is not an immutable acyclic history graph. Audit events preserve
operation order. Version graphs must not be recursively traversed using this field.

Previously delivered downloads/browser memory and independently approved copies
cannot be recalled. Deletion redacts saved assistant answers containing the source
ID, including other owners' source-derived answers, without returning their data
to the administrator. It clears source-selected context and physically removes
search chunks. It does not erase unrelated student-authored questions, historical
backups or infrastructure logs; institutional retention/backup policies must cover
those separately. Audit storage is not a forensic tamper-proof ledger.

## Authorization and concurrency

REST uses verified membership. `Files.change` refreshes membership and calls the
same tenant/department/ownership management checks as publication. Administrators
cannot manage another student's private file. Source downloads require active
state and retain the mixed-department original restriction. The worker checks
current permissions, active source state, checksum and job state again.

PostgreSQL keeps general source UPDATE/DELETE privileges revoked. `0005` grants
only the constrained `campus_file_action` function to `campus_api`, revokes PUBLIC
execution, locks the source/current membership and performs permission/state checks.
Permission expressions explicitly handle SQL NULL as denial. Existing visibility/
write helper functions additionally require `file_state=active`. Source tombstones
can remain visible to authorized managers; chunks and student retrieval cannot.

SQLite serializes writers before file/job changes. PostgreSQL uses source/job
locks and worker `SKIP LOCKED`; processing and acknowledgement commit atomically.
Deletion waits for an in-flight source transaction, then removes its output and
cancels the job, preventing resurrection. Persistent restart/concurrent-worker
tests from earlier phases remain in the full regression suite.

The UI paginates file metadata in groups of 50 and exposes status, publication
review, original access, reindexing, soft delete, restore, rollback and explicit
permanent confirmation. Replacement selection retains the existing latest-100
source dropdown; for older replacement targets the authorized REST interface
accepts the ID. No file lifecycle action is authorized by UI visibility alone.

## Database/deployment steps

Migration `0005` adds one non-null `sources.file_state` column defaulting to active.
Existing publication states, approvals, records and source bytes are preserved.
PostgreSQL additionally receives lifecycle/visibility function changes. The migration
is additive; no original data is reset. Automatic downgrade is deliberately refused
because it could discard tombstone protection. Review a backup-based rollback instead.

**The original development database is now at `0005`, following explicit approval
and a verified consistent backup on November 2.** All 13 application tables retained
their pre-existing values, and all 19 sources received the active file-state default.
No CampusOS API/worker process was running; none was started. Other Docker and live
services were unchanged. No credentials or `.env` edits were needed. The executed
commands used the development interpreter and the explicitly checked local file:

```powershell
# Historical approved execution; other databases still require review/approval.
$env:DATABASE_URL='sqlite:///C:/Users/kasht/NewFolder/CampusBuddy/CampusBuddy/data/campus.db'
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic upgrade 0005
.\.venv\Scripts\python.exe -m alembic current
```

This migration was rehearsed on a fresh verified backup before applying it to
the approved original development SQLite and checking the final revision/integrity.
Do not copy this local URL into production. Production migration, private bucket
cleanup and hosted-worker acceptance require separately approved staging resources.

## Reproducible checks and demonstrations

Executed local results are recorded in `artifacts/phase5-verification.json`:

| Check | Passed | Failed/errors | Skipped |
| --- | ---: | ---: | ---: |
| Full backend, including PostgreSQL/RLS | 270 | 0 | 0 |
| Final focused parser/lifecycle/publication/storage checks | 71 | 0 | 0 |
| Dedicated PostgreSQL migration/RLS checks | 16 | 0 | 0 |
| SQLite publication migration checks | 3 | 0 | 0 |
| Browser tests across three widths | 30 | 0 | 0 |

Counts overlap; do not add them. Ruff, TypeScript, production build and diff checks
passed. The final focused checks cover the last pagination, Office-root,
multi-sheet XLSX and lab-batch overlap corrections after the full-run checkpoint.
The genuine legacy-shaped `0005` SQLite test is included in the backend/focused
counts. At that test checkpoint, the original SQLite's bytes, schema and all rows
matched the approved `0004` snapshot; the later approved `0005` upgrade is recorded
separately above. The temporary container was removed, with existing Docker
container/volume inventory preserved. No live credentials were used.

Intermediate failures were investigated rather than skipped: parser diagnostics
on stdout broke JSON; output capture now isolates those diagnostics. The worker failure
fixture now corrupts a previously valid original because malformed PDFs are
correctly rejected before storage. PDF underscore/newline header artifacts now
normalize for mapping. Mocked OCR needed a table-finder guard. The SQL NULL
permission defect described below was corrected before any original/staging
migration. Two initial browser failures reused identical file bytes across widths;
unique synthetic bytes now preserve the intended checksum behavior. A download
denial assertion accepts the existing secure 403/404 contract. Final runs have no
failures, skipped or flaky browser cases. Existing Alembic and Starlette/httpx
deprecation warnings remain; no framework/dependency upgrade was introduced.

Live Supabase Auth/Storage deletion, real OCR, hosted workers and model providers
were not tested. These are unexecuted acceptance activities, not silently skipped
automated tests; they require approved resources or a separately installed OCR
engine. Original SQLite migration `0005` subsequently received explicit approval
and was completed; see the separate execution report above.

From the inner repository root:

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_databases --postgres
.\.venv\Scripts\python.exe -m ruff check backend scripts
.\.venv\Scripts\python.exe -m scripts.verify_frontend
git diff --check
```

The database runner reads/backups the original without modifying it, upgrades
copies to `0005`, compares all pre-existing columns/rows, then uses one unique
loopback/tmpfs pgvector container with generated test-only credentials. It removes
only its own container and checks the previous Docker inventory. The frontend
runner creates fresh local identities/data on unused ports, performs TS/build/
browser checks, and restores `next-env.d.ts`. Both disable providers.

For focused parser, lifecycle, migration and private-storage contracts:

```powershell
$env:DATABASE_URL='sqlite:///:memory:'
.\.venv\Scripts\python.exe -m pytest backend/tests/test_file_parsing.py backend/tests/test_files.py backend/tests/test_file_migration.py backend/tests/test_storage_adapter.py -q
```

The browser suite demonstrates upload/preview, publication and source citations;
the new flow uploads a unique test notice, soft-deletes it, restores a draft,
soft-deletes again, verifies purge is disabled until exact confirmation, then purges.
Tests run at desktop, tablet and mobile widths. OCR alone is mocked explicitly;
PDF table extraction and DOCX/PPTX/XLSX readers use actual parser code.

For a manual interactive session, use **fresh fixture data**, never the existing
development database. In terminal 1, first verify these example loopback ports
are free; if occupied choose different matching ports, without stopping services:

```powershell
Get-NetTCPConnection -LocalPort 48100,48101,48102 -ErrorAction SilentlyContinue
$env:CAMPUS_TEST_DIRECTORY=Join-Path (Get-Location) ('.scratch/phase5-demo-'+[guid]::NewGuid().ToString('N'))
$env:CAMPUS_TEST_API_PORT='48101'
$env:CAMPUS_TEST_AUTH_PORT='48102'
$env:CAMPUS_TEST_FRONTEND_PORT='48100'
$env:CAMPUS_TEST_BACKGROUND_WORKER='1'
.\.venv\Scripts\python.exe -m backend.tests.e2e_server
```

In terminal 2 from `frontend/`:

```powershell
$env:NEXT_PUBLIC_API_URL='http://127.0.0.1:48101'
$env:NEXT_PUBLIC_SUPABASE_URL='http://127.0.0.1:48102'
$env:NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY='test-only-publishable-key'
npm run dev -- --hostname 127.0.0.1 --port 48100
```

Use the clearly synthetic fixture accounts defined in `backend/tests/e2e_server.py`
and `frontend/tests/campus.spec.ts`; never use these identities for deployment.
Open Admin Console, import a header-based menu or timetable, edit a cell and validate.
Confirm and wait for processing; switch to the separate reviewer for approval.
Publish, then verify student retrieval. In Sources & freshness, delete and restore
the file, confirming it is draft and inaccessible to students until republished.
Test replacement/rollback while the current version stays available. Finally,
permanently delete only synthetic data using the exact source-ID confirmation.
Stop only the two fixture processes you started when finished. No Docker service
or existing database is involved in this manual demonstration.

## Interview questions

**CEO: What business risk does independent publication reduce?** A parser can
produce plausible but incorrect rows. Separating upload, validation, processing,
independent review and publication prevents uncertain imports immediately changing
student schedules or institutional answers.

**Why a tombstone instead of deleting the SQL row first?** SQL and object storage
cannot commit together. A durable hidden state allows cleanup retries while
retaining the key needed to finish deleting the original.

**Why cannot restoration simply set published=true?** The institution may have
changed permissions or content. Restoration clears approval and revalidates the
original and corrections before independent review. Private-owner data remains private.

**What does subprocess isolation guarantee?** A routine parser crash or timeout
does not kill the API/worker. It is not a complete native-code sandbox. Deployment
still needs OS/container memory, filesystem and network restrictions.

**How do deleted documents stop appearing in old chat?** Visibility checks reject
inactive sources, chunks are physically deleted, saved source-derived assistant
messages are redacted and selected-document context is cleared. Already downloaded
copies and infrastructure backups require separate retention policies.

**Why not use an LLM to repair every table?** It could invent missing faculty,
dates or departments and transmit private files externally. This phase uses
deterministic parsers and explicit human corrections, with worker revalidation.

**What did PostgreSQL testing find that Python tests missed?** An initial SQL
permission expression evaluated to NULL for a shared file and a student. PL/pgSQL
does not treat NULL as true in an IF guard, so the denial could be skipped. The
predicate now uses explicit fail-closed coalescing; direct restricted-role denial
tests cover the case. This defect was caught before any original/staging migration.
