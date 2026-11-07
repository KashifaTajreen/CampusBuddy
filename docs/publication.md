# Source processing and publication

Current lifecycle: `draft ? pending_review ? approved ? published`, with explicit rejection/revocation and file lifecycle controls. Processing and publication are separate; successful parsing, OCR or embeddings do not make a source student-visible.

## Administrator workflow

1. Upload an immutable original under the current tenant/department/semester/section scope. Shared uploads require current administrator permissions.
2. Preview extracted text/rows, correct uncertain values, validate essentials and explicitly acknowledge OCR/overlap warnings. Timetable dates and menu dates are optional; missing information is never invented. Timetable-only partial publication requires explicit ready-row selection and retains incomplete rows for correction.
3. Confirmation queues a persistent job. The separate worker locks/claims it, reloads the uploader's current membership and validates the current source/options. Structured rows are staged; draft document chunks remain inaccessible to students. Failure rolls back processing rather than replacing published data.
4. Under migration **0009**, the **same authorized uploader may explicitly review, approve and publish**. A second administrator is not mandatory. Backend and database permissions still enforce current scope, validation, successful processing and legal state transitions.
5. Specialized Timetable/Event Publish actions request these transitions as one user operation. The worker performs required processing/indexing, then guarded approval/publication. No upload-only action automatically publishes. Events require reviewed text/metadata and compatible private semantic indexing before publication.
6. Publication atomically installs valid structured records and source status. An existing published version stays available until a valid replacement publishes. Failure, revocation or a stale job cannot authorize publication. Demo Data remains visibly labeled and requires explicit acknowledgement.

Knowing a source ID is never sufficient for draft downloads or retrieval. Services, citations, history and PostgreSQL RLS recheck source publication, tenant, department, semester/section and private ownership. Private user-owned documents/email remain restricted to their owner; administrator status does not bypass that boundary.

## Originals and deletion

Row-level filtering cannot make a mixed source file safe. Shared structured originals are conservatively restricted to institution/platform administrators, even when a student can read some normalized rows. Shared documents obey source scope after publication; authorized reviewers can inspect eligible drafts. Private originals remain owner-only.

Draft deletion is scoped, confirmed and restricted to unpublished content, cleaning associated jobs/unpublished data/files without deleting other sources. Published-file removal uses the existing scoped file lifecycle (hide/soft-delete, possible restore and explicit purge controls), not a draft-delete shortcut. Restored content returns through review; deletion does not grant access to an old version.

## Implementation and checks

- `backend/app/ingestion.py`: preview, validation, queueing, worker claim/process/commit.
- `backend/app/publication.py`: guarded approval/publication and replacement.
- `backend/app/event_pipeline.py`, `event_data.py`: reviewed Events workflow/private indexing.
- `backend/app/files.py`, `storage.py`: lifecycle, cleanup and permission-checked originals.
- `backend/app/services.py`, `retrieval.py`: published, membership-scoped queries/citations.
- `backend/migrations/versions/0003*`?`0009*` and `backend/migrations/sql/`: RLS, grants, transition/cleanup functions.
- `backend/tests/test_publication.py`, `test_auth_api.py`, `test_timetable_postgres.py`, `test_events_postgres.py`, `test_import_policy_postgres.py`: success and denial paths.

Legacy migrations preserve/quarantine existing content according to their explicit compatibility rules; they do not establish that an old source received current human review. Review existing data before a live migration. No live migration is part of a Git cleanup or routine test run.
