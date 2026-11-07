# Current CampusOS main handoff

The correct Git root is the inner CampusBuddy application repository, not its containing workspace directory. Remote: https://github.com/KashifaTajreen/CampusBuddy (public). Main contains the current Next.js/FastAPI/worker application; current migration head is 0009.

## History-preserving integration

The fetched remote main and local main had unrelated histories (13 remote commits and 3 local commits). The remote contained the older `app/`, `frontend-next/`, root `migrations/`/`tests/` application and old frontend implementation. The cleanup commits the reviewed latest local tree, then uses an **ours-strategy merge with unrelated histories allowed** to retain remote ancestry without importing obsolete code. This is a normal fast-forwardable push from the remote's perspective; no force push or history rewrite is needed.

No backup branch, tag or repository is created, and no remote branch is deleted. Inspect the final commit and remote verification with `git log -2 --oneline`, `git rev-parse HEAD` and `git ls-remote origin refs/heads/main`. Branch protection/permission rejection is a stop condition, not a reason to bypass protection.

## Removed/excluded material

- Obsolete remote `app/`, `frontend-next/`, root legacy migration/test tree, obsolete source image/build-guide conversion files and old root launchers are absent from the current application tree. Their commits remain reachable through preserved history.
- Sixteen one-off browser/live-draft debugging scripts and 23 local execution reports were removed from publishable source after dependency review. They remain in ignored `.scratch/github-cleanup/retired-local-tools/`. Three operator-specific guides/checkpoints were rewritten generically, preserving their original copies there too.
- Generated `artifacts/mcp-verification.json`, `artifacts/verification.json` and `frontend/next-env.d.ts` were untracked while their actual local files were retained. Generated outputs, models, env files and databases remain ignored. `artifacts/README.md` explains the boundary.
- The actual benchmark identity/authorization profile `config/evaluation/shared_llama_scope.json` is preserved locally and ignored. Only a disabled placeholder example is public. Benchmark unit tests now use independent synthetic identity/scope fixtures.
- All current application modules, migration/policy files, UI, essential backend/browser/benchmark-tool tests, reusable fixtures, manifests/lockfile and guarded operator utilities are preserved.

See [verification](verification.md) for executed counts, resolved test-harness failures, remaining skips and OCR limits. Original env files/uploads/databases/model weights are not changed or uploaded. No Supabase Auth/member/published-record/storage changes, live migration or deployment are part of this handoff. Phase 7 deployment and Gmail/Calendar feature work remain paused.

## Teammate setup

Follow [README](../README.md). Clone main, create your own local environment from examples, configure your approved Supabase/Auth project and use a new local/disposable database. Do not reuse another person's secrets or run project-specific production maintenance helpers as setup.

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
# Enter your own approved values in ignored env files; never overwrite existing ones.
# Only for your NEW EMPTY local database:
.\.venv\Scripts\python.exe -m alembic upgrade head
# Separate terminals:
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001 --no-access-log
.\.venv\Scripts\python.exe -m backend.app.worker
cd frontend
npm run dev -- --port 3001
```

Start a feature branch from updated main, push that branch and open a Pull Request:

```powershell
git switch main
git pull --ff-only origin main
git switch -c feature/your-topic
# Edit and run relevant checks.
git add <reviewed-files>
git commit -m "feat: describe the change"
git push -u origin feature/your-topic
```

An existing checkout with unrelated old history should use a new clone or a separately reviewed history integration, not a destructive reset. Keep existing private data/env files outside the new clone and migrate/import them only under explicit owner approval.
