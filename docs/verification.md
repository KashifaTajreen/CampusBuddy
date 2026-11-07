# Repository cleanup verification ? 7 November 2026

These are executed local checks while preparing the current CampusOS tree for GitHub main. They do not claim a new live OAuth/storage/deployment verification. Detailed generated evidence stays ignored in `artifacts/` and `.scratch/github-cleanup/`.

| Check | Result |
| --- | --- |
| Full backend suite | **495 passed, 0 failed/errors, 69 skipped** (564 collected) |
| Dedicated disposable PostgreSQL | **24 passed, 0 failed/skipped**: core/RLS/legacy migration/worker processes 21, import policy 1, timetable publication 1, Events/E5/pgvector 1 |
| Benchmark utility tests | **61 passed, 0 failed/skipped**; pure synthetic/mock tests, no model benchmark/shared DB access |
| Portable private-reference regression run | **28 passed, 0 failed/skipped**; existing approved local timetable, DOCX/workbook inputs read-only, disposable database and object stores |
| Frontend TypeScript | Passed `next typegen && tsc --noEmit` from a clean copied frontend |
| Frontend production build | Passed `next build --webpack` with loopback test public config, no real frontend env files copied |
| Desktop production browser tests | **28 passed, 0 failed/skipped**; real local API/worker fixture, synthetic RSA identities and disposable SQLite/files; optional installed local Whisper exercised |
| Python lint | Passed `ruff check backend scripts benchmarks` |
| Fresh SQLite migration consistency | Single head **0009**; upgrade/current/check passed; `No new upgrade operations detected` |
| PostgreSQL migrations | All four new disposable databases upgraded to **0009**, pgvector verified; only the owned container removed |
| Credential/size audit | High-signal credential patterns plus copies of current local credentials checked in publishable candidates and **280 reachable historical blobs**, including OOXML document text; no credential findings. Largest publishable file is the ~96 KB synthetic WAV fixture. Final index audit is performed before commit. |
| Protected local files | SHA-256 comparison covers **138 existing environment/data files**; no changes detected. Original SQLite hash remains `b75d914a60764c24063c7873f8eb4ab05191fd223c9706dc4e89a8616fa2bbe9`. |

Counts are separate runs and overlap; do not add them into a unique-test total. The full-suite invocation preceded the final portability/harness edits; subsequent focused private-reference/tooling checks and the final production browser run exercise those final changes. Mobile/tablet and the dedicated Events browser suite were not rerun in this cleanup pass.

## Exact skip reasons

The full backend run had no configured disposable external-service URLs/state:

| Count | Reason |
| --- | --- |
| 20 | `TEST_POSTGRES_URL` not configured: PostgreSQL/RLS fixture required |
| 1 | `TEST_POSTGRES_URL` not configured: disposable legacy migration cluster required |
| 1 | Dedicated Events PostgreSQL URL not configured |
| 1 | Dedicated import-policy PostgreSQL URL not configured |
| 1 | Dedicated timetable PostgreSQL URL not configured |
| 37 | KT security checks require the separately owned disposable rehearsal state (`CAMPUS_KT_REHEARSAL_STATE`) |
| 8 | Disposable Redis URL/ownership marker not configured |

The **24 PostgreSQL skips were subsequently run and passed** with `scripts.verify_postgres`. The separate **37 KT-profile tests and 8 Redis tests remain skipped** in this pass. A teammate without approved local OCR/E5/speech assets or private reference files will see additional explicit optional-asset/reference skips. Missing assets are not replaced by fabricated test evidence.

## Failures investigated during preparation

- Initial backend run: 487 passed, 8 failed, 69 skipped. Stale expectations required a second admin, old Events route labels, empty strings instead of NULL faculty codes, batch assignments removed by the approved workflow, and exactly one timetable query despite the existing break-time explanation lookup. Updated tests preserve denial checks and current contracts; application routing/scoring was not changed.
- The photographed menu's Badam Milk prints **15**, but two OCR layouts can both read **45**. This remains a documented extraction-quality limitation. The regression now checks the actual safety contract: source retained, review/correction required and no automatic confirmation. No claim of perfect real-photo OCR is made.
- Initial PostgreSQL attempt: 17 passed, 4 failed. Two stale policy/head assertions were corrected; two worker subprocesses correctly refused the runner's wrong test DB/user names. The runner now uses the existing `campus_verify` safety guard, which was not weakened. Final dedicated run: all 24 passed.
- Initial desktop browser run: 20 passed, 8 failed. Tests used old labels/forms and a machine-specific private workbook; copied-frontend audio capture pointed at a nonexistent relative fixture. Tests now generate a synthetic workbook, reference the repository audio fixture, match current deletion/import controls and account for segmented/automatic speech. A focused timetable selector also offered unauthorized menu choices/editable scope; it now mirrors the existing role restrictions. Backend permissions are unchanged.
- A subsequent browser startup was stopped by the database ownership assertion because a new fixture import occurred before test env pinning. The import was moved after disposable configuration; no database operation ran on that rejected target. Another run passed 27/28, with only the auto-read control assertion stale. Final desktop run passed all 28.
- No tests were removed to hide failures, no benchmark scoring changed, no new model benchmark/download ran, and no live data was used as a test database.

## Reproduce

From the repository root, after dependencies are installed:

```powershell
.\.venv\Scripts\python.exe -m ruff check backend scripts benchmarks
.\.venv\Scripts\python.exe -m pytest --junitxml=artifacts/backend-tests.xml
.\.venv\Scripts\python.exe -m pytest benchmarks
.\.venv\Scripts\python.exe -m scripts.verify_postgres
.\.venv\Scripts\python.exe -m scripts.verify_frontend --production --project desktop
.\.venv\Scripts\python.exe -m scripts.audit_repository --history
.\.venv\Scripts\python.exe -m scripts.audit_repository --staged
```

`verify_postgres` requires Docker and an already installed `pgvector/pgvector:pg17` image. It never reads `.env` database URLs and uses no existing volumes. Browser verification copies source/cache and uses unused loopback ports, not localhost:3001/8001. `--staged` audits the whole index (run after staging, before commit).

Private reference tests accept `CAMPUS_TIMETABLE_REFERENCE`, `CAMPUS_PRIVATE_WORKBOOK_REFERENCE` and `CAMPUS_PRIVATE_FACULTY_REFERENCE` pointing to operator-approved local files. Without those, ignored `.scratch/private-fixtures/` paths are checked and missing inputs are explicitly skipped. Do not copy private originals into public fixtures.

The scanner checks common key formats, service-role JWTs, credential URLs and copies of current local secrets; it cannot prove that all possible secrets are absent, inspect inaccessible/deleted remote history or replace GitHub secret scanning. No historical credential exposure requiring rotation was detected by these checks. Production/private bucket policies, real OAuth and deployed worker behavior still require target-specific acceptance under owner approval.
