# CampusOS final acceptance checklist

> Historical phase notes: current setup/schema is **0009**. The mandatory second-reviewer requirement was superseded by scoped explicit same-admin approval in 0009. Original-machine revision/status statements and artifact links below are historical, not the state of a teammate checkout. See [current setup](../README.md), [publication](publication.md), [verification](verification.md) and [limitations](limitations.md).

This checklist separates working local behavior from hosted acceptance. A checked
local result does not approve cloud access, migration, paid resources or a release.
Final machine-derived counts will be recorded in `artifacts/phase7-verification.json`.

## Local implementation

- [x] Existing framework, five roles, database/worker, publication and private owners preserved.
- [x] Compact five-role login selection; active memberships still establish authority.
- [x] Student conversational dashboard and existing timetable/menu/notices/document flows retained.
- [x] Actual file preview, validation/editing, independent approval, publication and immutable file actions retained.
- [x] Technical intelligence navigation limited to College Admin in the interface; API authorization unchanged.
- [x] Manifest, 192/512/maskable/apple icons, install prompt where supported and public-only offline help implemented.
- [x] Service worker excludes API/auth/private answers; live campus data requires connectivity.
- [x] Existing E5 384-dimensional index preserved; benchmark BGE 1024-dimensional arrays isolated.
- [x] Exact reference/USN routing precedes similarity; authorized SQL handles structured questions.
- [x] Hybrid semantic/keyword RRF keeps prior relevance admission and selected-document scope.
- [x] Language selection, saved preference, constrained Whisper, OCR and speech-text normalization retained.
- [x] Separate production API/worker descriptors, baseline asset checks and zero-data DB readiness implemented.

## Executed evidence

- [x] Focused backend retrieval/deployment checks passed; raw reports are under `artifacts/phase7-*-focused.xml`.
- [x] Focused production Next.js browser role/PWA checks passed after rerunning with process cleanup permissions.
- [ ] Final full backend/PostgreSQL/SQLite/Redis verification (populate from executed artifacts).
- [ ] Final responsive production-browser suite, TypeScript/build and dependency checks.
- [ ] Approved local model benchmark and resource report.
- [ ] Final diff check and original SQLite hash/revision preservation.

## Explicit external and human gates

- [ ] Confirm exact non-production Vercel/Render/Supabase resources and spending ceiling.
- [ ] Review/approve compatible migration through `0007`, verified backups and restore rehearsal.
- [ ] Review role conversion, legacy spreadsheet restrictions, identity provisioning and fresh reviewed replacements.
- [ ] Real staging login, refresh, JWT signature rejection and same-token membership revocation/restoration.
- [ ] Separately approved signing-key rotation/retirement.
- [ ] Private bucket metadata plus direct anonymous/other-student/admin cross-owner storage/API denials.
- [ ] Deployed HTTPS/CORS, five roles and complete upload-to-cited-student-answer workflow.
- [ ] Hosted persistent worker completion after browser close, restart/lease recovery and concurrent owned jobs.
- [ ] Deployed asset identity, memory/load, database pool/TLS and model inference latency acceptance.
- [ ] Real HTTPS mobile installation, keyboard/touch behavior and public-only offline recovery.
- [ ] Human English/Hindi/Kannada and code-switching recordings; noisy microphones and spoken identifiers.
- [ ] Audible TTS including times/dates/prices and actual Hindi/Kannada local voice availability.
- [ ] Representative campus OCR/retrieval and native-reviewed generation/translation quality.
- [ ] Qwen evaluation/license permission (not approved; no Qwen download or run).

No hosted checklist item is passed by a localhost fixture or synthetic model metric.
Human recordings remain unavailable. No new translation model is installed.
