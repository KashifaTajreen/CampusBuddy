# CampusOS

CampusOS (repository name CampusBuddy) is a campus assistant with student and administrator interfaces. It answers authorized timetable, food, faculty, examination, event and document questions using structured queries and source-backed retrieval.

## Features and stack

- Next.js App Router, React, TypeScript and Tailwind frontend; FastAPI and SQLAlchemy backend.
- Supabase Google OAuth, verified memberships, scoped roles and section join-code onboarding. Students cannot choose an administrator role.
- Timetable, menu, event and general-document imports with editable previews, validation, persistent jobs and explicit publication. The same authorized administrator may review and publish under migration **0009**.
- XLSX/CSV, digital PDF and DOCX parsing; local OCR for images/scanned PDFs when language packs are installed. Uncertain OCR needs human review. Originals remain private.
- Deterministic SQL tools and bounded multi-tool routing; authorized document RAG, source citations and optional local multilingual E5 embeddings/pgvector.
- Optional local Ollama synthesis, speech recognition and device read-aloud. Models and language packs are separate downloads, never bundled or downloaded at startup.
- PostgreSQL RLS in addition to application authorization. SQLite/local files support development.

Implementation and tests do not prove that a new deployment's OAuth, storage, worker or model configuration works. See [limitations](docs/limitations.md), [security](docs/security.md) and the [verification record](docs/verification.md).

## Repository layout

| Path | Contents |
| --- | --- |
| `frontend/src/` | Student/admin UI, browser Supabase client and typed API client |
| `backend/app/` | API, auth, domain services, tools, ingestion, retrieval and worker |
| `backend/migrations/` | Alembic revisions 0001?0009 and reviewed PostgreSQL policy SQL |
| `backend/tests/`, `frontend/tests/` | Unit, API, RLS, browser and accessibility tests |
| `benchmarks/` | Tests for offline benchmark tooling; not model weights or private results |
| `scripts/` | Local verification, asset setup, benchmark and guarded operator utilities |
| `config/staging/`, `deploy/`, `render.yaml` | Configuration/deployment templates; no credentials |
| `docs/` | Architecture, setup, publication, security and operational guidance |
| `data/`, `artifacts/`, `.scratch/` | Ignored local data, generated results and downloaded assets |

## Requirements

Python **3.11**, Node.js **22**, npm and Git. Docker with an installed pgvector PostgreSQL image is optional for isolated database tests. GPU/model installation is optional. The frontend lockfile is committed; Python dependency version ranges are in `backend/requirements.txt` (not a fully pinned Python lock).

## Local setup (PowerShell)

```powershell
git clone https://github.com/KashifaTajreen/CampusBuddy.git
cd CampusBuddy
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item frontend/.env.example frontend/.env.local
cd frontend
npm ci
cd ..
```

On Linux/macOS use `python3.11 -m venv .venv`, `.venv/bin/python` and `cp` instead. Do not overwrite an existing `.env`, `.env.local` or database. Enter credentials only in ignored local files.

Configure matching `SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_URL` and publishable/anon keys from **your approved project**. Set `FRONTEND_ORIGIN=http://localhost:3001` and `NEXT_PUBLIC_API_URL=http://localhost:8001`. Only public configuration belongs in `NEXT_PUBLIC_*`. Database passwords, service-role keys and OAuth client secrets are server-only.

For Google sign-in, enable the provider in Supabase, configure Google's Supabase callback `https://YOUR_PROJECT.supabase.co/auth/v1/callback`, and allow the application's `http://localhost:3001` redirect in Supabase. Configure asymmetric Supabase signing keys. Normal student access requires a verified `@rvce.edu.in` Google identity. The exact pinned Gmail admin exception still requires an active administrator membership. The optional development-only pinned test student is disabled by default; see [test-account controls](docs/TEMPORARY_TEST_STUDENT.md).

There is no shared password, automatic admin creation or public admin-registration endpoint. A trusted operator must provision the initial institution/admin membership after verifying the Auth UUID; students use scoped, expiring join codes. See [deployment setup](docs/deployment.md).

## Database setup and migrations

The current Alembic head is **0009**. For a **new, empty local SQLite database only**, leave the example `DATABASE_URL=sqlite:///./data/campus.db` and run:

```powershell
.\.venv\Scripts\python.exe -m alembic heads
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
```

For an existing database: stop its API/worker, make a consistent backup, rehearse on a copy, review SQL/grants and obtain the owner's approval before upgrading. These commands do not authorize changes to an existing Supabase project or the original development database.

Production requires PostgreSQL/pgvector. Apply reviewed migrations using a migration owner, then give the API a separate non-owner login inheriting `campus_api`, without superuser or BYPASSRLS. The worker uses its separately protected trusted connection. Do not use owner credentials in browser configuration or the API. [Database/security setup](docs/deployment.md) explains the separation. `compose.yaml` is a development template and can migrate its configured database; inspect its environment and volumes before running it. Do not point it at an existing institutional database.

No institutional data or model weights are shipped. Optional synthetic seeding (`python -m backend.app.seed --help`) creates Demo Data and an admin membership; use it **only on your own disposable database**, never a shared/live database without separate approval.

## Run the website and worker

Run each command from the repository root in a separate terminal. Configure the worker's `DATABASE_URL` in its process environment if it differs from the API's restricted connection; never put a trusted worker credential in the frontend.

```powershell
# API
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001 --no-access-log

# Separate ingestion worker (same database and object store)
.\.venv\Scripts\python.exe -m backend.app.worker

# Frontend
cd frontend
npm run dev -- --port 3001
```

Open **http://localhost:3001**. API health: **http://localhost:8001/health**; readiness: `/ready`. A healthy API does not imply successful live OAuth, model readiness or a running worker. Keep the worker alive for queued extraction/indexing. Google login alone does not grant campus access: membership/onboarding must succeed too.

Optional ONNX E5 dependencies: `python -m pip install -r backend/requirements-semantic.txt`. Optional local speech: `python -m pip install -r backend/requirements-speech.txt`. Follow [local model/OCR/speech setup](docs/phase6.md) and `python -m scripts.setup_semantic` only after reviewing its download budget/licenses. Configure the same embedding model/provider on API and worker; re-index authorized documents when changing embedding identity. Lexical fallback is not semantic E5 retrieval. Event publication requires compatible semantic indexing. Local Ollama is enabled only with `LLM_ENABLED=true`, `LLM_PROVIDER=ollama`, loopback `OLLAMA_BASE_URL`, an installed `OLLAMA_MODEL` and an explicit approved `LLM_DATA_POLICY`; no model is installed by these settings.

## Verification

```powershell
.\.venv\Scripts\python.exe -m ruff check backend scripts benchmarks
.\.venv\Scripts\python.exe -m pytest --junitxml=artifacts/backend-tests.xml
.\.venv\Scripts\python.exe -m pytest benchmarks
cd frontend
npm run typecheck
npm run build
npx playwright install chromium
cd ..
.\.venv\Scripts\python.exe -m scripts.verify_frontend --production --project desktop
.\.venv\Scripts\python.exe -m scripts.verify_postgres
.\.venv\Scripts\python.exe -m scripts.audit_repository --history
```

`verify_frontend` copies frontend source into ignored scratch space, uses fresh loopback API/Auth/database fixtures and does not load real frontend env files or replace the running `.next` cache. Omit `--project desktop` for desktop/mobile/tablet. `verify_postgres` requires Docker and an **already installed** `pgvector/pgvector:pg17` image; it creates tmpfs databases with generated credentials and removes only its own labeled container. It never uses `.env` database URLs. Individual `TEST_*` PostgreSQL suites seed data and must **never** target a real database.

Optional asset/real-upload tests skip with explicit reasons when their local inputs are absent. The dedicated E5/OCR Events browser suite is `python -m scripts.verify_events_browser`; it is not included in the generic Playwright config because it needs separately installed assets. Browser identity fixtures are synthetic and must never be deployed. See [verification](docs/verification.md) and [scripts guide](scripts/README.md) for boundaries and known issues.

## Team workflow

```powershell
git switch main
git pull --ff-only origin main
git switch -c feature/your-topic
# Make changes and run relevant checks.
git add <reviewed-files>
git commit -m "feat: describe the change"
git push -u origin feature/your-topic
```

Open a Pull Request into `main`; review authentication/data changes carefully. Do not force-push main or commit `.env`, private uploads, databases, model weights or generated results. GitHub Actions runs automated checks, not a live deployment. Deployment templates have automatic deployment disabled. Existing project-specific operator scripts are guarded maintenance tools, not teammate quick-start commands.
