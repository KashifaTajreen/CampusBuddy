# CampusOS AI · CampusBuddy

A production-oriented campus intelligence **vertical slice**: Next.js + TypeScript, FastAPI, verified Supabase JWTs, tenant memberships, deterministic domain tools, document retrieval, durable ingestion and authenticated MCP.

**Status:** implemented and locally tested; not a completed enterprise release. Supabase cloud, live Google OAuth, live Gemini and deployment require operator configuration and acceptance testing. See [verification](docs/verification.md) and [limitations](docs/limitations.md). No Streamlit application or role simulation is included.

## What works

- Student dashboard, streaming chat, saved conversations, cited sources and contextual faculty follow-ups.
- Effective-dated section timetables, date exceptions, next class, free periods, calendar overlaps, faculty directory.
- Available-menu filtering and affordable combinations of distinct items with estimated nutrition.
- PDF/TXT/Markdown ingestion, page citations, local retrieval, expiry, version provenance and explicit claim conflicts.
- CSV/XLSX/JSON column mapping, validation, import confirmation, idempotency, background worker and re-indexing.
- Server-side institution/department/owner permissions, membership administration, private email imports and query traces.
- Official MCP SDK with 14 shared read-only tools over stdio and bearer-authenticated Streamable HTTP.
- Optional local AI through Ollama: semantic retrieval with rank fusion and reranking, grounded answers and a bounded tool-calling agent. See [deployment](docs/deployment.md).
- Voice questions transcribed locally, a timetable week grid, an administrator timetable editor with clash checks, and faculty profiles.
- Optional encrypted Google read-only connections and optional Gemini synthesis. Missing configuration produces an actionable error, never a simulated connection.

## Quick start · Windows PowerShell

Python 3.11+ and Node.js 22+ are required. Use a current supported patch version. Run from the repository root.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
Copy-Item frontend/.env.example frontend/.env.local
```

Configure Supabase Auth using [the deployment guide](docs/deployment.md). A real Supabase user and a database membership are required even when the database is local SQLite. An unconfigured installation displays setup instructions; it does not manufacture a login.

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m backend.app.seed --user-id YOUR_SUPABASE_AUTH_USER_UUID
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

In separate terminals:

```powershell
.\.venv\Scripts\python.exe -m backend.app.worker
```

```powershell
cd frontend
npm ci
npm run dev
```

Open [CampusOS locally](http://localhost:3000). Set `FRONTEND_ORIGIN` to the exact frontend origin you use. API documentation is available at [local API docs](http://localhost:8000/docs) outside production.

Linux/macOS use `.venv/bin/python` in place of `.venv\Scripts\python.exe`.

## Database and deployment

SQLite is a local fallback only. Production requires Supabase PostgreSQL, pgvector, a restricted database login inheriting `campus_api`, private Supabase Storage, HTTPS and configured Auth. Production startup rejects SQLite, superuser/BYPASSRLS API credentials, table-owner API credentials and incomplete storage configuration.

```sh
docker compose up --build
```

The Compose configuration runs **local PostgreSQL, migrations, API and worker**. It uses documented development-only passwords and still requires Supabase Auth configuration. Run the frontend separately. Do not expose this Compose configuration to the internet. Docker was not installed on the build workstation, so Compose execution is not claimed as verified.

## Tests

```powershell
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m pytest --junitxml=artifacts/backend-tests.xml
cd frontend
npm run typecheck
npm run build
npx playwright install chromium
npm run test:e2e
```

If Chrome is already installed on Windows, use `$env:PLAYWRIGHT_CHANNEL='chrome'` before Playwright. The browser suite starts an **isolated test identity provider** and test database. It verifies real RSA JWT checking and real application calls; it does not verify Supabase's live service. Never deploy `backend/tests/e2e_server.py` or use its test credentials for actual accounts.

PostgreSQL integration tests require a disposable migrated database in `TEST_POSTGRES_URL`. They exercise pgvector, full-text search, and RLS under `SET ROLE campus_api`. They skip explicitly when unavailable. GitHub Actions includes a disposable pgvector service.

## MCP

Set `CAMPUS_ACCESS_TOKEN` to a valid short-lived Supabase access token and `CAMPUS_TENANT_ID` to an institution where that user has active membership. Keep tokens in the ignored `.env` file or your client's secret store.

```powershell
.\.venv\Scripts\python.exe -m backend.app.mcp_server
.\.venv\Scripts\python.exe -m backend.app.mcp_client
```

The API also serves `/mcp/`. Every HTTP request requires `Authorization: Bearer …` and `X-Tenant-ID`. See [MCP integration](docs/mcp.md).

## Project map

```text
frontend/src/app/          Next.js App Router and responsive styles
frontend/src/components/  Student, chat, admin and settings interfaces
backend/app/              Auth, services, schemas, router, retrieval, ingestion, API, MCP
backend/migrations/       Alembic schema, RLS and search indexes
backend/tests/            Domain, API, tenant, retrieval, worker, MCP and evaluation tests
frontend/tests/           Playwright flows and axe accessibility checks
docs/                     Architecture, deployment, security, demo and limitations
artifacts/                Executed verification reports
```

All seed data is synthetic and labeled **Demo Data**. Seed contacts use `example.invalid`. The seed command uses the same ingestion/validation services as real files, and never supplies hardcoded answers to chat questions.

Start with [architecture](docs/architecture.md), [security](docs/security.md), [deployment and troubleshooting](docs/deployment.md), or the [five-minute demonstration](docs/demo-script.md).
