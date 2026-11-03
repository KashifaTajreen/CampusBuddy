# Local setup, Supabase and deployment

No cloud resources have been provisioned by this build. Configure your own accounts; never commit credentials. Free-tier quotas and availability change, so review the provider dashboards before choosing a hosting plan.

## 1. Supabase Auth

Create a Supabase project and an email/password user. Use asymmetric signing keys (RS256 or ES256); legacy HS256 tokens are intentionally rejected. Configure your frontend Site URL and password recovery redirect (`/reset-password`) in Auth. Invite real users through Supabase Auth; an email alone does not grant an institution membership.

Set in root `.env`:

```dotenv
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_PUBLISHABLE_KEY=YOUR_PUBLISHABLE_KEY
FRONTEND_ORIGIN=http://localhost:3000
```

Set matching **public** values in `frontend/.env.local`:

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=YOUR_PUBLISHABLE_KEY
```

Never put service-role keys, database passwords, Gemini keys or OAuth secrets in `NEXT_PUBLIC_*`. Frontend public variables are embedded at build time; rebuild after changing them. [Supabase Auth JWT verification](https://supabase.com/docs/guides/auth/jwts) and [Supabase Next.js setup](https://supabase.com/docs/guides/auth/quickstarts/nextjs) describe these provider boundaries.

Run migrations and seed with the actual user's Supabase UUID using the commands in the README. The seed creates synthetic data and an institution administrator membership; it does **not** create a password or authenticate that user. The first operator is provisioned by a trusted CLI, never public registration. Institution admins can assign subsequent memberships through the application after users exist in Auth.

## 2. PostgreSQL and RLS

Use a Supabase direct or session-pooler PostgreSQL connection suitable for SQLAlchemy. Keep TLS enabled. Use the migration/owner connection only for `alembic upgrade head`, seeding and the trusted worker. The migration installs pgvector, search indexes, RLS and a NOLOGIN role `campus_api`.

Create a separate login through your database administration console, give it a securely generated password and membership in `campus_api`, and set API `DATABASE_URL` to that login. Do not grant ownership, superuser, BYPASSRLS or the database owner role. Use provider-supported connection/pooling settings and keep the API pool small. Production startup checks the role before serving.

The API's transaction-local `app.user_id` and `app.tenant_id` settings are established only after JWT verification. They are database session settings, not trusted HTTP identities. The backend database login is therefore a sensitive server credential; never expose it through a client connection. PostgreSQL does not authenticate these settings independently of that login.

Migration `0002` revokes public access to application tables. Run migrations in a **dedicated application database/schema**, review grants and back up first. On Supabase, ensure `anon` and `authenticated` have no unintended direct application-table grants; the browser should use FastAPI for campus data. The security-definer membership helpers require a trusted migration owner able to bypass FORCE RLS. Do not deploy with unreviewed custom grants or an unprivileged function owner.

To run the disposable PostgreSQL tests:

```powershell
$env:DATABASE_URL='postgresql+psycopg://OWNER:ENCODED_PASSWORD@localhost:5432/campus_test'
.\.venv\Scripts\python.exe -m alembic upgrade head
$env:TEST_POSTGRES_URL=$env:DATABASE_URL
.\.venv\Scripts\python.exe -m pytest backend/tests/test_postgres.py
```

Never point this test suite at a production database. CI uses `pgvector/pgvector:pg16` with disposable credentials.

On Supabase the owner login is not a superuser, so the tests first need `GRANT campus_api TO postgres WITH SET TRUE, INHERIT FALSE;` to switch into the restricted role. Seeding over a slow connection can take about 20 minutes the first time.

## 3. Private storage

Create a **private** Supabase Storage bucket named `campus-sources`. Do not add public download policies. The server uses its service-role key to store files and streams originals only after tenant/owner/department authorization. Keep the key in server secrets.

```dotenv
STORAGE_BACKEND=supabase
SUPABASE_STORAGE_BUCKET=campus-sources
SUPABASE_SERVICE_ROLE_KEY=SERVER_ONLY_KEY
```

Confirm bucket privacy manually and test cross-tenant source downloads before importing actual institutional files. Local storage defaults to `data/objects`; protect that directory and the database with OS permissions and encrypted disk. [Supabase private storage access](https://supabase.com/docs/guides/storage/security/access-control) explains bucket and policy controls.

## 4. Frontend and Python hosting

For Vercel, import the GitHub repository with root directory `frontend`. Install with `npm ci`, build with `npm run build`, and set the three public environment values for each environment. Point the API URL to an HTTPS backend. No Vercel deployment was created by this task.

Build the Python Dockerfile from the repository root. Run separate API and worker processes with a shared database and private object store. A self-hosted laptop/server is the zero-infrastructure-cost fallback. Do not assume hosted Python workers have an unlimited free plan. A backend with optional sentence-transformers needs more memory than the lexical fallback.

API command:

```sh
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --no-access-log
```

Worker command:

```sh
python -m backend.app.worker
```

Set `APP_ENV=production` only after PostgreSQL, restricted API credentials, HTTPS, Supabase Auth and private Storage are configured. Use a separate privileged worker connection; never give the API that connection. Put an HTTPS reverse proxy in front of the API with request-size limits (20 MB), header/body timeouts, per-IP abuse limits and process resource limits. Disable proxy request/query-string logs on OAuth callbacks. Expose only the API through the proxy, not PostgreSQL or a stdio MCP process.

## 5. Optional AI and integrations

The core assistant requires neither an LLM key nor a model download. To run local AI, install [Ollama](https://ollama.com) and `requirements-semantic.txt`, then:

```sh
ollama pull qwen3.5:9b
ollama pull bge-m3
```

Set these in the server environment and re-index documents. Changing the embedding model always requires re-indexing:

```sh
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen3.5:9b
OLLAMA_EMBEDDING_MODEL=bge-m3
ROUTING_MODE=hybrid
RERANKER_MODEL_PATH=data/models/bge-reranker-v2-m3
SPEECH_MODEL=data/models/faster-whisper-small
```

In the benchmark on an 8 GB GPU, `qwen3.5:9b` (recorded as `qwen3.5:latest` in the benchmark file, the same model digest) ran 88% on the GPU and the rest in system memory; `qwen3.5:2b` is the lower-memory option. Download the reranker and faster-whisper models to the folders above; they are never fetched automatically. Compare models with `python scripts/benchmark_models.py retrieval` and `python scripts/benchmark_models.py llm <models>`. Check each model's license before use.

For optional synthesis, provide a Gemini API key and enabled model in the server environment. Requests are sent only for permitted document passages. Obtain institutional approval for sending those passages to an external provider. Requests have timeouts, validated output and extractive fallback. Live provider behaviour was not verified without credentials.

For Gmail/Google Calendar, create a Google Web OAuth client. Set the client ID, secret and exact callback URI from `.env.example`. Generate a Fernet key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and store it as `TOKEN_ENCRYPTION_KEY` through your secret manager. Do not rotate this key without migrating/reconnecting encrypted credentials.

Add authorized test users while Google consent is in testing. The UI starts a PKCE flow with encrypted, expiring, single-use state. Only read-only scopes are requested. The callback stores encrypted tokens; sync imports bounded recent data; disconnect requests revocation and removes credentials. Previously imported data remains private until purged under your retention policy. Sync is an explicit bounded import, not continuous bidirectional synchronization. [Google OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server) documents provider requirements.

## Troubleshooting

| Symptom | Action |
|---|---|
| Setup screen | Configure frontend Supabase URL/key and restart Next.js. |
| 401 | Sign in again; verify asymmetric signing keys, issuer URL and audience. |
| 403/no membership | Seed/provision the verified user's UUID in the selected institution. |
| CORS failure | Match `FRONTEND_ORIGIN` exactly, including localhost versus 127.0.0.1. |
| Queued import never finishes | Start the worker with the same database and object store. |
| Unknown faculty code | Import faculty before timetable; map `faculty_code` correctly. |
| Blank/scanned PDF | Run OCR before upload; password-protected PDFs must be unlocked. |
| Failed job | Review the reported error, fix the file or configuration, then preview/retry. |
| Missing document answer | Search a specific subject, verify scope/expiry and check ingestion status. |
| Wrong vector dimension | Use a 1024-dimensional model such as `bge-m3` and re-index. |
| RLS/permission errors | Review API grants, restricted role membership and transaction-local identity. |
| Windows socketpair/browser startup hangs in a sandbox | Run local tests with loopback networking allowed. This build required that permission. |
| Turbopack process error | Project scripts use the supported Webpack path, which built successfully. |
| Google connection error | Verify consent test-user access, callback URI, scopes and encryption key. |
| MCP authentication error | Refresh the short-lived Supabase access token and select an authorized tenant. |

Read [security](security.md) before handling actual records, and [limitations](limitations.md) before claiming production readiness.
