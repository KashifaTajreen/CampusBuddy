# Environment and deployment setup

Use [README](../README.md) for a new checkout. Current Alembic head: **0009**. Never infer a live database revision from source files; verify your approved target separately. The original local database and live Supabase resources are not migration targets for routine tests.

## Authentication

Configure the same approved Supabase project URL and public publishable/anon key in backend `.env` and frontend `.env.local`. For local operation use frontend `http://localhost:3001`, API `http://localhost:8001`, and matching `FRONTEND_ORIGIN`/`NEXT_PUBLIC_API_URL`.

Enable Google in Supabase Auth. In Google Cloud configure the OAuth client's authorized redirect as `https://YOUR_PROJECT.supabase.co/auth/v1/callback`. In Supabase allow the app's localhost:3001 redirect (and the exact deployed URL when approved). Supabase issues/refreshes browser sessions; FastAPI verifies asymmetric JWT signature, issuer, audience, expiry and subject, then resolves active database memberships. A valid Google token alone grants no institutional data.

Normal student login requires a verified `@rvce.edu.in` Google identity. A pinned admin Gmail identity additionally requires `GOOGLE_ADMIN_EMAIL`, `GOOGLE_ADMIN_USER_ID` and an active institution-admin membership. Students cannot create admin memberships. A separate test-student exception is development-only, disabled by default and described [here](TEMPORARY_TEST_STUDENT.md). Do not use the browser-test identity server outside tests.

## PostgreSQL, migrations and roles

1. Confirm the project/database is the approved target. Stop writers, take a consistent backup, verify restoration on a disposable target and inspect its existing revision/schema.
2. Review `backend/migrations/versions/0001` through `0009` and policy SQL. Existing data can be classified/quarantined by compatibility migrations; do not treat an upgrade as a cosmetic revision stamp.
3. Obtain explicit approval for existing/live database changes. Generate/review SQL with the same environment and PostgreSQL version, rehearse, then run `python -m alembic upgrade 0009` using a trusted migration login. Verify revision, pgvector, RLS, grants and data preservation before resuming traffic. Do not downgrade blindly; restore/recover from a verified backup with owner approval.
4. Provision a separate API login inheriting `campus_api`. It must not own app tables, be superuser, have BYPASSRLS or inherit the owner. Production startup checks these properties. Migrations/worker credentials remain server-only and separate.
5. Remove unintended `anon`/`authenticated` direct app-table grants. Application data goes through FastAPI. Review SECURITY DEFINER function ownership/search paths and FORCE RLS, not just table SELECT policies.

The API sets transaction-local identity only after JWT verification. PostgreSQL RLS is defense in depth, not an independent JWT verifier. Possession of owner/API credentials remains a sensitive operational trust boundary. The worker rechecks current uploader permissions and processes persisted jobs with database locking.

`compose.yaml` is an opt-in local development stack, including a migration service and persistent named volume. Review target/environment before starting it. `scripts.verify_postgres` instead uses new loopback/tmpfs databases and no existing volumes. Project-specific `scripts/*campusos_kt*` helpers are guarded operator tools, not general onboarding commands; do not change their target guards to reuse them elsewhere.

## Storage and local assets

For Supabase Storage use a **private** bucket (`STORAGE_BACKEND=supabase`, `SUPABASE_STORAGE_BUCKET`, server-only `SUPABASE_SERVICE_ROLE_KEY`). Do not create public download policies. FastAPI authorizes every original download; structured originals have stricter permissions than their filtered rows. Check anonymous, cross-tenant, draft and private-owner denials on an approved staging project before using real uploads.

For local operation the object store uses ignored `data/objects`. Protect files, database and backups with OS permissions. API and worker must share the object store and identical compatible embedding assets/provider. E5 ONNX requires the optional runtime and explicit model installation. OCR language data and Whisper weights are also explicit, separate assets. No normal startup performs downloads, migrations or seeding. Event validation queues private semantic indexing; publication waits for compatible indexed content.

## Hosting

The repository includes `frontend/vercel.json`, `render.yaml`, `deploy/Dockerfile` and staging templates. They are preparation, not proof of a deployed service. Render automatic deployment is disabled in the template. A Git push does not authorize provisioning, migrations, paid resources or changes to live service settings.

Use HTTPS public origins, exact OAuth redirects, private bucket access, a restricted API connection, trusted worker connection and durable model/object paths. Build frontend public variables for the selected environment. Backend production checks require PostgreSQL, HTTPS Supabase/frontend and configured private storage. `backend.scripts.start_service` validates preinstalled assets and starts the selected API/worker process without migrations or seeds. See [runbook](DEPLOYMENT_RUNBOOK.md), [security](security.md) and [limitations](limitations.md).
