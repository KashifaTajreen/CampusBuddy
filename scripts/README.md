# Operator and verification utilities

Run Python modules from the repository root (`python -m scripts.NAME`). Read a
tool's source/help and its target guards before running it. A script's presence
does not authorize production access, migrations, seeding or model downloads.

| Group | Entry points and boundaries |
| --- | --- |
| Routine verification | `verify_frontend`: copied frontend, synthetic loopback Auth/API/database. `verify_postgres`: new labeled tmpfs container only, no `.env` target. `audit_repository`: sanitized candidate/index/history scan. `verify.py`: core checks in place; can rebuild the current frontend cache, so use the isolated runner beside a running website. |
| Optional offline assets | `setup_local_assets`, `setup_semantic`; explicit reviewed download/install steps. `evaluate_ocr`, `evaluate_semantic` measure local assets on generated fixtures. Never assume synthetic results prove human/real-photo quality. |
| Disposable focused checks | `rehearse_events_publication`, `rehearse_timetable_publication`, `rehearse_import_policy`, `verify_events_browser`, `run_product_demo`, `timetable_acceptance`, `kt_rehearsal_scenario`. Read required asset/database guards first. |
| Benchmark tooling | `benchmark_*`, `original_*_scenario`, `prepare_original_benchmark`, `inspect_original_benchmark`, `model_resources`, `phase7_models`, `shared_llama_*`, `run_shared_small_benchmark`, `download_shared_llama`, `report_*`. Configuration, large downloads and shared-data authorization are separate approvals. Results stay ignored. Retained `benchmarks/` tests verify these utilities without running new benchmarks. |
| Staging/deployment preparation | `staging_probe` defaults to plan-only; `audit_phase7`, `validate_phase7_deploy`, `verify_phase7_frontend`. Phase 7 deployment remains paused. Templates are not evidence of a deployed service. |
| Project-specific maintenance | `configure_campusos_kt`, `backup_campusos_kt_docker`, `rehearse_campusos_kt`, `migrate_campusos_kt`, `provision_campusos_kt_runtime`, `bootstrap_campusos_kt_admin`, `migrate_import_review`, `configure_test_student`, `configure_local_qwen`, `start_campusos_kt_worker`. These deliberately pin an approved operator project/profile. They are **not** teammate quick-start scripts; do not remove guards or run them against another project. Actual env profiles and execution/approval/backup evidence are ignored local files. |
| Historical verification helpers | `verify_databases`, `verify_redis`, `benchmark_phase6` contain phase-specific assumptions. Review supported revisions/target checks before use; prefer the current isolated PostgreSQL runner for a clean checkout. |

One-off browser/CDP and live-draft repair scripts were removed from publishable
source after checking their dependencies. Their original files and historical
machine-specific reports are preserved in the operator's ignored
`.scratch/github-cleanup/retired-local-tools/`. No test fixture, model, original
upload or local environment is removed by this organization.
