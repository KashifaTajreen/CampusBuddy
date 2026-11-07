# Phase 6B product acceptance

> Historical phase notes: current setup/schema is **0009**. The mandatory second-reviewer requirement was superseded by scoped explicit same-admin approval in 0009. Original-machine revision/status statements and artifact links below are historical, not the state of a teammate checkout. See [current setup](../README.md), [publication](publication.md), [verification](verification.md) and [limitations](limitations.md).

This is the historical Phase 6B checkpoint. See the current
[pre-Phase-7 corrective report](verification.md) for subsequent fixes,
regression evidence and migration `0007` requirements. The original database
remains `0005`; neither later revision has been applied to it in this pass.

Local implementation and verification are recorded here. Production acceptance is
pending: the original development database is still `0005`, and no Supabase,
production credentials or hosted services were used. Migration `0006` requires a
separate approval before changing that database. Earlier phase reports are historical.

## Feature checklist

| Capability | Implemented and locally exercised | Acceptance limit |
| --- | --- | --- |
| Five roles | API, service checks, SQLite constraints, PostgreSQL RLS and role-aware UI | Review legacy role conversions before deployment |
| Regular timetable | Effective dates, section assignments, date exceptions, next class | No elective or lab-batch enrollment |
| Exam timetable | Subject, date, start/end time, venue, department/section; validated event storage | No automatic interpretation of every possible PDF layout |
| Academic calendar | Holidays, academic/important dates, date ranges; cancellations affect classes | Missing calendar evidence does not prove college is open |
| Official documents | Editable extraction/mapping, scope, dates, independent approval, publication and citations | OCR still needs human review |
| Faculty | Directory SQL and teaching assignments from the student's effective timetable | Biographies/research require uploaded evidence |
| Events | Authorized dates, topics, venues and schedule-conflict tools | No event-registration feature |
| Single canteen | Prices, availability, safe published-menu editing and reviewed replacement | Existing development records are not automatically rewritten |
| Background duplicates | Checksums, natural keys, timetable/exam/calendar overlap, logical key and title warnings | Bounded heuristics, not universal contradiction detection |
| Multilingual retrieval | Real local quantized E5, hybrid ranking, English/Hindi/Kannada evaluation | Synthetic evaluation is not institutional accuracy certification |
| Cache | Shared authorization views, scoped and personalized partitions, invalidation and SQL fallback | Optional; no new production speed claim |
| Integrated queries | SQL, document retrieval, latest-circular selection, bounded multi-tool execution | Unavailable evidence produces an honest empty/insufficient answer |
| Files and workers | Existing deletion/restore, retry, independent publication and separate worker preserved | Hosted restart/load and storage checks remain pending |

## Role-permission matrix

College Admin is stored as `institution_admin` for compatibility. The five stored
roles are `institution_admin`, `department_admin`, `section_admin`,
`canteen_manager` and `student`; there is no sixth application role.

| Operation | College Admin | Department Admin | Section Admin | Canteen Manager | Student |
| --- | --- | --- | --- | --- | --- |
| Read published institutional information | College | Own department + college-wide | Own section + department/college-wide | Authorized published information | Own section + department/college-wide |
| Upload official documents, schedules, calendars, events, faculty | College | Own department | Own department, semester and section | No | No |
| Manage menu/prices/availability | Yes | No | No | Single college-wide canteen | No |
| Independently approve documents | Yes | Own authorized scope | Own authorized scope | No official documents | No |
| Independently approve structured sources | Yes | No | No | No | No |
| Publish successfully processed, independently approved source | Authorized scope | Own scope | Own scope | Own menu workflow | No |
| Edit published menu uploaded by another admin | Yes | No | No | Validated menu fields only | No |
| Download another uploader's unredacted structured original | Yes | No | No | No | No |
| Change memberships | Yes; cannot edit own authority | No | No | No | No |
| Read another user's private documents/email | No implicit permission | No | No | No | No |

An uploader cannot approve their own source. Structured review remains a College
Admin responsibility because a source can contain unmapped columns or mixed
departments. Filtering its validated rows does not make the original file safe.
Canteen Managers receive sanitized published menu rows, including unavailable
items, rather than another uploader's raw file or ingestion preview. A legacy
department-restricted menu needs College Admin review; it is not exposed through
the college-wide editing endpoint.

## End-to-end scenarios

1. **Section circular:** Section Admin uploads a Section A notice, corrects the
   preview and confirms. A separate worker extracts/chunks it, runs duplicate
   checks and leaves it `pending_review`. Another authorized admin approves; the
   uploader publishes. Student A can select/query it; Student B cannot enumerate,
   select or download it. Deletion immediately hides it and invalidates saved access.
2. **Menu revision:** College Admin uploads and publishes a menu. Canteen Manager
   loads its validated rows, changes price/availability, selects the original as
   the replacement and submits a new file. College Admin independently approves.
   Publishing atomically switches sources; a student's cached food answer returns
   the revised price. A failed, rejected or unfinished revision leaves the old
   published menu available. Replacement is a complete version: omitted items
   disappear when the new version publishes.
3. **Holiday:** Upload a calendar with a holiday on a teaching day. After review
   and publication, the timetable and next-class tool skip canceled classes and
   cite the calendar when answering the holiday question. Teaching-assignment
   questions still identify the assigned professor. Deleting the calendar restores
   the regular schedule. A holiday defaults to cancellation unless explicitly false.
4. **Exam:** Import subject/date/start/end/venue rows as examination events. A
   malformed row prevents confirmation/publication. Students ask for their next
   exam or tomorrow's start time using SQL, with relevant circular conflict warnings.
5. **Latest circular:** Metadata selects the newest authorized, date-eligible
   examination circular. Retrieval is restricted to that one document. Explicit
   document selection continues to take precedence and never broadens on failure.
6. **Conflicting upload:** A second timetable/calendar with overlapping keys or
   dates is staged, not silently upserted. Duplicate results appear in review;
   blocking conflicts require an explicit matching replacement. Document title/topic
   warnings remain visible without pretending every differing document is invalid.
7. **Restart and denial:** Queued jobs survive API restart; a separate worker
   completes them without publishing. Concurrent workers claim safely; an interrupted
   lease can recover. Revoked memberships cannot approve/publish. Direct student
   API writes and forged PostgreSQL scope writes are denied.

Executable scenarios are in `test_product_workflows.py`, `test_product_api.py`,
`test_cache.py`, `test_postgres.py`, `test_worker_processes.py` and
`frontend/tests/product.spec.ts`. Existing publication, private-owner, selected
document, provider failure, OCR and speech regressions remain in the full suite.

## Actual verification

The machine-derived [Phase 6B report](../artifacts/phase6b-verification.json)
aggregates the final executed artifacts, including failures and skip reasons.
The backend total already includes the PostgreSQL and Redis cases; do not add
these overlapping counts together.

| Check | Evidence |
| --- | --- |
| Full backend with disposable PostgreSQL/Redis: 333 passed, zero failed/skipped | [JUnit](../artifacts/backend-database-verification.xml) |
| Dedicated PostgreSQL/RLS/pgvector: 20 passed, zero failed/skipped | [JUnit](../artifacts/postgres-tests.xml) |
| SQLite migration preservation: 3 dedicated cases passed, zero failed/skipped | [Database report](../artifacts/database-verification.json), [JUnit](../artifacts/sqlite-migration-tests.xml) |
| TypeScript, production build, all 54 browser cases across three viewports passed | [Frontend report](../artifacts/frontend-verification.json) |
| Ruff and whitespace checks | [Phase 6B report](../artifacts/phase6b-verification.json) |
| Real local E5 retrieval | [Evaluation](../artifacts/phase6b-semantic-evaluation.json) |
| API + separate worker + frontend smoke with E5 | [Smoke report](../artifacts/phase6b-demo-smoke.json) |

Real integration: local ONNX/tokenizer inference, pgvector queries, restricted
PostgreSQL RLS, authenticated Redis, SQLite migrations/copies, persistent worker
processes, browser/API publication workflows and RSA-signed test identity. Existing
local OCR and speech regressions also execute. The identity server is a **test
fixture**, not live Supabase. Supabase Storage, external model generation and Google
providers use local contract tests/mocks; no live-provider success is claimed.

The semantic evaluation uses six English passages and six translated questions
per language. Results:

| Language | Lexical top result | Semantic top result | Actual hybrid retrieval top result |
| --- | --- | --- | --- |
| English | 66.7% | 100% | 100% |
| Hindi | 0% | 100% | 83.3% |
| Kannada | 16.7% | 83.3% | 83.3% |

The actual path includes relevance thresholds, authorization and source metadata.
One Hindi query abstains; a Kannada query ranks the wrong passage. The local gate
was at least 75% for each language and passed. Scores are not calibrated confidence.
Use a larger, human-reviewed campus corpus before a production quality claim.

Failures found during development were investigated: PostgreSQL source `RETURNING`
visibility required row-argument scope checks; test savepoint setup was rolling back
its own queued job; repeated test documents correctly triggered duplicate rejection;
manager replacement needed a sanitized metadata path instead of raw-source management;
SQLite query-trace autoflush could precede its explicit write reservation; browser
projects shared a rate-limit identity; and two final regression expectations used
the old route and inconsistent clocks. Fixes preserve authorization and rate limits.
Final review also added a per-caller original-download flag on shared menu cache
hits and a publication-time tie-breaker for same-day circulars. An initial joined
ordering query caused a SQLAlchemy auto-correlation error; explicit correlated
subqueries fixed it and the focused routing suite passed before full verification.
Final pass/fail/skip counts are recorded in the generated report; unperformed live
acceptance checks are blockers, not silently counted as passing tests.
There were no skipped tests in the final combined verification, so no final skip
reasons apply. Nine backend warnings concern Alembic's existing legacy
`prepend_sys_path` separator configuration; they are not test failures.

## Migration and deployment blockers

- **Original database remains `0005`.** The report checks its SHA-256, revision,
  integrity and foreign keys read-only. Disposable, consistently backed-up copies
  upgrade to `0006` and preserve all old application values. Do not start the new
  application against the original until migration is separately approved.
- `0006_product_scopes.py` adds nullable semester and default `All` section to
  sources/documents/records, backfills timetable scope, restricts roles/scopes and
  updates PostgreSQL RLS/lifecycle functions. Legacy `faculty` becomes `student`;
  `platform_admin` becomes `institution_admin`. Unknown roles and unbounded admin
  departments stop migration before DDL. Review those intentional conversions.
- Before an approved rollout: stop this application's API/workers, confirm the
  exact local database URL, make and verify a fresh consistent backup, test its
  disposable copy, then use the intended interpreter with
  `python -m alembic upgrade 0006` and `python -m alembic current`. **These are
  future approved maintenance steps, not authorization to run them now.** Do not
  use `stamp`, a reset or an automatic downgrade. Rollback requires restoring the
  reviewed backup with compatible code during maintenance.
- Deploy API/worker with the same model assets and configuration. Dimensions stay
  384; model fingerprints prevent mixing incompatible vectors. Existing documents
  retain lexical retrieval until an authorized re-index rebuilds their embeddings.
  Existing approved extracted text and scope are reused. Missing/broken model
  assets fall back to lexical reads; inspect evaluation before relying on semantics.
  Deployment images need the optional `backend/requirements-semantic.txt` runtime
  and a read-only mount/copy of verified model assets at the configured path; the
  workspace model is not bundled into an image automatically.
- Staging still needs explicit approval and secure configuration: real Supabase
  login/refresh, issuer/JWKS rotation and membership revocation; private bucket
  privacy, direct anonymous/unauthorized denial, authorized upload/download/delete;
  deployed restricted DB login/pooler and worker restart/concurrency/resource tests.
- No production or staging resources were accessed. Human OCR/voice quality,
  real mobile HTTPS microphone behavior, live external models and deployment-level
  parser isolation remain unverified. See [Phase 6 limits](verification.md).

## Run the complete local product

Use PowerShell from the **inner repository**
`<repository>`. Dependencies and approved E5
assets are already installed locally. This demo never opens the original database:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_product_demo --semantic
```

Open the printed loopback frontend URL. It starts synthetic Auth, a fresh migrated
SQLite database/local objects, FastAPI, an independent worker and Next.js. Data is
labelled Demo Data. Test-only accounts use `@example.invalid`: `admin`, `reviewer`,
`department`, `section`, `canteen`, `student` and `section-b`. The script prints the
fixture password. Never deploy this identity fixture. Ctrl+C stops only this
invocation's processes and retains scratch data/logs. Omit `--semantic` for lexical
mode. Existing `.env`, original database and unrelated processes are untouched.

For a demonstration: upload a section notice as `section`; confirm it; independently
approve/publish as `reviewer`; select/query it as `student`; confirm it is absent
for `section-b`. As `canteen`, load a published menu, edit prices/availability and
preview a replacement; as `reviewer`, approve/publish it, then ask the student food
question again. Upload a calendar/exam JSON using the documented schema and repeat
review/publication to demonstrate calendar-aware SQL. Use an unambiguous unique
title/logical key for unrelated documents; choose an explicit replacement for revisions.

For example, save this as `holiday.json`, choose Academic calendar, and set a
college-wide source as College Admin (change the date to the teaching day to test):

```json
[{"title":"Demo campus holiday","start_on":"2026-11-03","entry_kind":"holiday","classes_cancelled":true}]
```

Save this as `exam.csv`, choose Exam timetable, department `AIML`, semester `3`,
section `A`. Adjust the date before asking about tomorrow:

```csv
subject,date,start_time,end_time,venue
Operating Systems,2026-11-04,09:30,11:30,Demo Exam Hall
```

Preview alone changes no student answer. Confirm, wait for background validation,
independently approve and publish each source. An end before start is a useful
invalid-input demonstration; correct it in preview before confirmation.

## Exact local verification commands

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_redis --databases
.\.venv\Scripts\python.exe -m scripts.evaluate_semantic
.\.venv\Scripts\python.exe -m scripts.verify_frontend
.\.venv\Scripts\python.exe -m scripts.run_product_demo --semantic --smoke
.\.venv\Scripts\python.exe -m scripts.report_phase6b
```

The first command requires Docker Desktop's Linux engine and the pgvector/Redis
images. It creates generated test-only credentials, loopback-only ports and tmpfs
storage, uses no existing volumes, and removes only its own containers. It checks
the pre-existing Docker inventory afterward. Do not run two database verifiers at
the same time. The frontend verifier runs TypeScript, a production build and the
browser suite with isolated accounts/resources and restores `next-env.d.ts`.
The report command runs Ruff/`git diff --check` and reads the original database
without migrating it. Bare `pytest` intentionally skips external-resource cases
without the owned disposable test resources; use the combined command for full coverage.

For a new checkout, install optional `backend/requirements-semantic.txt` into its
intended venv and explicitly approve/run `python -m scripts.setup_semantic` before
semantic evaluation. This is a network download; normal startup never downloads
model assets. See [engineering details](ENGINEERING_WALKTHROUGH.md).
