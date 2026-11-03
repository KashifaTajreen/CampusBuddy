# Scope, limitations and roadmap

Implemented features are backed by executable services and tests. The following are explicit limits, not simulated functionality.

## Current limits

- No Supabase cloud project, deployed Vercel frontend, hosted Python service or live OAuth connection was created. Those require configuration and acceptance tests.
- SQLite supports local development. Production requires PostgreSQL; RLS/pgvector tests run only with a disposable PostgreSQL service.
- Without configuration, retrieval uses lexical hashing, which is not semantic search. Semantic retrieval needs a 1024-dimensional model such as `bge-m3` through Ollama. No large model is bundled or automatically downloaded.
- Model choices and retrieval floors come from a small Demo Data benchmark (14 LLM cases, 10 answerable and 3 unanswerable retrieval questions, in `artifacts/model-benchmark.json`). The reranker floor was set on those same questions. Re-measure all of them with real institutional documents and questions.
- Migration `0003_local_ai_search` clears stored embeddings, so documents must be re-indexed after upgrading. It was tested up and down on an empty Supabase PostgreSQL 17 project, not on a database holding existing data. The PostgreSQL tests there used lexical hashing embeddings, not `bge-m3`.
- Voice input was measured only on clean synthetic speech in WAV and WebM/Opus. Real microphone audio, accents, and Safari and Firefox recordings are untested.
- Reading tables from PDFs and the Docling parser are covered only by tests with mocked models. Docling may download its own models on first use.
- Optional Gemini and Google HTTP adapters are implemented but live provider calls are unverified without credentials. Their UI reports configuration and errors rather than pretending to connect.
- Conflict detection uses administrator-supplied topic/claim metadata. It does not infer every contradiction in arbitrary prose.
- Faculty linkage uses a stable code and service validation. Academic organizational entities currently live in validated payloads/memberships rather than a complete normalized academic registry.
- Timetable exceptions support cancellation or room changes on a particular date. Complex rotating weeks, substitute faculty, holiday calendars and team-wide free-time optimization need further work.
- Meal combinations are bounded to 60 available candidate items for CPU control. They are ranked by estimated protein and do not promise globally optimal large-menu nutrition planning.
- Email summaries expose authorized message excerpts and extracted ISO dates; natural-language date extraction, threading and semantic email retrieval are limited.
- Google sync is a bounded explicit import. Repeated imports upsert stable natural keys, but deletion/rescheduling reconciliation and continuous sync are incomplete.
- Student/faculty views share the same domain screens; faculty-specific teaching-load and approval workflows are not implemented.
- Membership administration can assign/update roles. Complete invitation, suspension and institution lifecycle workflows need expansion.
- Developer metrics are scoped to the current administrator's activity. No invented global accuracy/latency values are shown.
- No malware scanning, OCR, full audit immutability, disaster recovery automation, organization SSO or compliance certification.
- Login uses Supabase's browser session handling. JWT verification does not itself perform online revocation introspection on every request; short token lifetimes and provider account controls matter.
- Conversation and imported-source retention is an operator responsibility; automatic tenant retention policies are not enabled.
- Docker Compose configuration is supplied; Docker was unavailable on the build workstation.
- The initial Alembic schema revision references ORM metadata. Before the first released migration baseline is frozen, replace it with a reviewed immutable schema snapshot; subsequent revisions must be explicit migration operations.

## Next milestones

1. Connect a staging Supabase project; verify actual JWT, bucket, restricted-role RLS, backup/restore and cloud deployment.
2. Complete academic registry normalization and composite tenant foreign keys, with a migration review.
3. Add granular document ACLs, institution-specific retention/purge workflows and membership revocation controls.
4. Run live Google OAuth and Gemini acceptance tests; add encrypted-token rotation and synchronization reconciliation.
5. Add holiday/rotation timetables, faculty dashboards and multi-student meeting coordination.
6. Add parser process isolation, security/dependency scanning, load tests and independent penetration testing.
7. Expand retrieval evaluation with institution-approved data, calibrated abstention and model-specific regression suites.
