# Dedicated Supabase staging setup and acceptance

**Preparation only.** No project, bucket, account, hosted process or live-provider
acceptance was created/run by Phase 3. Obtain explicit approval for the named
non-production project and each write/restart/rotation scope before using any
credentials. Script flags are operator safeguards; they do not grant an assistant
permission. A project reference alone cannot prove that a project is non-production.

## Required resources and identities

Use a new dedicated Supabase test project with no institutional data. Its Auth,
database and Storage must belong to the same approved project. Record the project
reference and confirm it is not the production reference through your operator's
inventory. Check available quotas/billing before creating resources; no pricing or
free-capacity assumption is made here.

Required resources:

- Supabase Auth with asymmetric RS256 or ES256 signing keys, confirmed test users,
  approved frontend/callback origins and normal expiry/refresh settings.
- PostgreSQL with pgvector, trusted migration/function ownership, a separate
  trusted worker connection and a restricted API login inheriting `campus_api`.
- A new **private** `campus-verify-<unique-suffix>` bucket for acceptance objects.
  Use no permissive anonymous or authenticated-user object policies: intended
  campus downloads go through FastAPI. Do not alter an existing bucket to test it.
- HTTPS frontend/API hosting and a separately supervised worker using the same
  staging database and bucket. Keep the database and server key off the browser.
- Dedicated owner student, other student, uploader administrator and independent
  reviewer administrator accounts; optionally a second tenant/department for
  isolation checks. Map their actual Auth UUIDs to test-only memberships.

Credential/write boundaries:

| Configuration | Purpose | Possible effects after approval |
| --- | --- | --- |
| Project URL/public key + test-user passwords | Real sign-in/refresh | Creates test Auth sessions |
| Staging server Storage key | Inspect private bucket and exercise adapter | Upload/delete one generated synthetic object when explicitly enabled |
| Trusted migration/operator DB connection | Apply migrations and provision test memberships | Changes only the dedicated staging schema/test rows |
| Restricted API DB connection | CampusOS runtime | RLS-protected app reads/writes, never table ownership/BYPASSRLS |
| Trusted worker DB connection + same private bucket | Persistent ingestion | Claims and processes staging test jobs |
| Hosted service/operator access | Restart/rotation acceptance | Separate explicit approval required; probes do not automate deployment or key rotation |

## Configuration and database setup — after approval only

Copy `backend.env.example`, `worker.env.example`, `frontend.env.example` and
`probe.json.example` to separately managed staging settings. Local `.env` and
`frontend/.env.local` stay intact. `*.env` and `*.local.json` in this directory
are Git-ignored. Prefer a secret manager rather than committing or logging files.

The env templates are for the deployment process manager's env-file/secret
injection feature; CampusOS does not automatically load these template filenames.
Use a clean staging deployment, not a shell inheriting production settings.
Supply every required staging field explicitly and leave optional Gemini/Google
configuration disabled. Frontend `NEXT_PUBLIC_*` values are built into its bundle;
build a separate staging frontend with the approved staging URL/public key.

Before migrations, back up the dedicated staging database and inspect the target
connection/project through the operator console. With the approved migration
connection injected into `DATABASE_URL` on the **staging host**, run:

```powershell
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic upgrade 0004
.\.venv\Scripts\python.exe -m alembic current
```

Expected head is `0004`. Do not run migration/test seeding against production.
There is no Phase 3 schema migration: selection uses existing conversation JSON.

Using the approved staging SQL operator, create the API login without embedding
a password in SQL history, then set its password through a secure prompt/manager:

```sql
CREATE ROLE campus_staging_api LOGIN NOSUPERUSER NOBYPASSRLS;
GRANT campus_api TO campus_staging_api;
```

For psql, `\password campus_staging_api` prompts without placing the password in
the SQL text. Assign only this role's connection to the API. Keep the worker and
migration credentials separate. Verify actual role flags, table ownership and
function ownership. Security-definer helpers with FORCE RLS require a suitably
trusted function owner; do not assume a provider's default role has the right grants.
Also inspect `anon`/`authenticated` grants on application tables: browser campus
data access should use FastAPI, not an unintended direct Data API grant.

Provision a test institution and memberships for the four actual Auth UUIDs using
the trusted operator workflow. Do not manufacture JWT user IDs or accept browser
role metadata. Label all fixture content as synthetic. Exercise shared imports
through upload → preview → confirm → worker → independent approval → publication.
Create a private owner-student document through the authorized personal import API
(`visibility: "private"`) and retain its document/source IDs for the probes.

Run the existing entry points as separate supervised staging services, with the
different API/worker env templates injected by the deployment manager:

```sh
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --no-access-log
python -m backend.app.worker
```

Use TLS/reverse-proxy limits and disable sensitive access logs. Production startup
guards remain enabled in staging via `APP_ENV=production`.

## Probe commands and expected results

The default command is safe to run locally: it reads no config/credentials and
does not construct a network client:

```powershell
.\.venv\Scripts\python.exe -m scripts.staging_probe --check auth
```

After explicit staging approval, fill ignored `probe.local.json` with dedicated
test values. Never paste tokens/passwords into chat. Execute only the relevant
approved operation. Replace `APPROVED_PROJECT_REF`/`APPROVED_API_ORIGIN` with the
exact approved public identifiers, not secrets:

```powershell
# Real login, refresh and backend signature verification; expected_signing_kid is optional for rotation checks.
.\.venv\Scripts\python.exe -m scripts.staging_probe --execute --check auth --config config/staging/probe.local.json --approved-project-ref APPROVED_PROJECT_REF

# Also revoke only the newly created test session and confirm its refresh is denied.
.\.venv\Scripts\python.exe -m scripts.staging_probe --execute --check auth --config config/staging/probe.local.json --approved-project-ref APPROVED_PROJECT_REF --allow-test-writes --revoke-test-session

# Inspect bucket privacy, use the real ObjectStore adapter, check direct denials, and delete exactly the generated object.
.\.venv\Scripts\python.exe -m scripts.staging_probe --execute --check storage --config config/staging/probe.local.json --approved-project-ref APPROVED_PROJECT_REF --allow-test-writes

# Owner download/selection, anonymous denial, other-student/admin denial and private metadata isolation.
.\.venv\Scripts\python.exe -m scripts.staging_probe --execute --check api --config config/staging/probe.local.json --approved-project-ref APPROVED_PROJECT_REF --approved-api-origin APPROVED_API_ORIGIN --allow-test-writes
```

Storage probes require a `campus-verify-*` bucket, check `public: false` before
uploading, use unique `verification/<uuid>/synthetic.txt` keys, disable overwrite
and redirects, and clean up only that exact object. They never create/delete a
bucket or scan/delete another prefix. Object writes/API conversations/session
logout require the extra write flag. Credentials and provider response bodies
are omitted from output/errors. Treat an inconclusive HTTP result as failure,
not privacy success.

The API probe needs a valid private document/source pair and three distinct
accounts (owner, other student and administrator). It confirms the owner can
select that exact source, other accounts cannot list its metadata or select it,
and anonymous/other accounts cannot download it. The test question may be saved
in the owner's staging conversation, covered by the explicit write approval.
The probe verifies their actual membership roles through `/v1/me`; account labels
alone cannot count as student/admin denial evidence.

### Membership revocation

Use the same-token interactive probe after approving a change to only the
dedicated test membership:

```powershell
.\.venv\Scripts\python.exe -m scripts.staging_probe --execute --check membership --config config/staging/probe.local.json --approved-project-ref APPROVED_PROJECT_REF --approved-api-origin APPROVED_API_ORIGIN
```

It signs in, verifies `/v1/me` succeeds, and pauses. In the separately authorized
staging operator console, record the membership's initial state and set only that
test user/tenant row inactive. Require exactly one affected row. Press Enter:
the probe uses the **same** access token and expects 403. Restore only that test
row afterward. The probe itself never changes a membership or uses DB credentials.

JWT logout and membership revocation are different: this backend verifies JWTs
offline and does not introspect every session. A logged-out access JWT can remain
valid until expiry; next-request inactive-membership denial is the immediate
application control. See [Supabase sessions](https://supabase.com/docs/guides/auth/sessions).

### Signing-key rotation and retirement

Obtain separate project-wide rotation approval. Publish a standby asymmetric key
and follow provider propagation guidance before activation. Record the public
expected `kid` in the probe config and run the auth probe after rotation; it checks
the actual refreshed token uses that key and verifies its signature through the
existing FastAPI verifier. Repeat through the running staging API/browser to test
the already-running verifier's cache, not only a fresh probe process.

Retain an old, still-unexpired **test** JWT securely in an operator harness, then
retire its key only after further approval. After provider and app cache expiry,
expect rejection through that running API. If the JWT expired first, the result
does not prove key retirement; report it as inconclusive. Test invalid-signature,
issuer/audience and algorithm rejection as well. The scripts do not rotate or
revoke signing keys. See [Supabase signing keys](https://supabase.com/docs/guides/auth/signing-keys).

### Hosted worker restart and shared storage

With an approved synthetic shared upload queued while the hosted worker is
stopped, record its ID as `worker_job_id`, then capture:

Use a separate ignored probe config whose primary `email`/`password`/
`expected_user_id` identify that authorized uploader administrator; the student
owner configuration used by private-document checks does not own a shared job.

```powershell
.\.venv\Scripts\python.exe -m scripts.staging_probe --execute --check worker-snapshot --config config/staging/probe.local.json --approved-project-ref APPROVED_PROJECT_REF --approved-api-origin APPROVED_API_ORIGIN
```

Start/restart only the authorized staging worker through its actual supervisor,
then rerun the snapshot. The same job must survive, complete into `pending_review`
and remain unavailable to students. Approve and publish with different authorized
accounts; then verify citations and original bytes through the API using a
student account. This proves the worker read the same private object store used
by the API, not a machine-local substitute.

For restart during processing, stop only the test worker, verify no partial
publication, and allow its ten-minute stale claim lease to recover. Do not mutate
another job's timestamps or reset the database. Test two staging workers and
provider outages after separate approval. Capture supervisor restart evidence,
job attempts, absence of duplicate documents and final publication state. The
snapshot probe records state; it does **not** claim to have restarted a hosted
process. Local crash/concurrency tests provide separate, already executed evidence.

## Outstanding acceptance

Real browser login/refresh/recovery, provider key propagation/retirement, actual
restricted login/pooler behavior, cloud bucket policy denials and hosted worker
operation remain unverified until these approved checks are executed. Mocked
probe tests are not cloud evidence. Keep credentials out of logs and delete only
newly created acceptance objects/sessions under the approved cleanup scope.
