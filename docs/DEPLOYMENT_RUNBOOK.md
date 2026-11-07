# Deployment runbook

**Phase 7 deployment remains paused.** This is a generic review checklist, not approval to provision or modify a live service. No project IDs, credentials or operator-specific execution records are part of this guide.

1. Agree on the exact non-production/production targets, owners, region, costs and rollback authority. Confirm no unrelated project will be touched.
2. Inventory target schema/revision, grants, pgvector availability, private Storage bucket and Google OAuth redirect/signing configuration. Use read-only inspection first.
3. Stop target writers; make a consistent backup, verify its hash and restoration on a disposable database. Rehearse the reviewed migration chain through 0009 and scope/RLS denials.
4. Obtain explicit owner approval for exact target, SQL/hash and backup. Only then migrate with the trusted owner connection. Stop on extension, permission, preservation or verification failure; do not seed real resources automatically.
5. Configure restricted API login and separate trusted worker credentials. Deploy reviewed code with preinstalled compatible model assets and durable object storage. Never bake credentials/model weights/private uploads into the repository or image context.
6. Build the frontend with that environment's public API/Supabase settings. Run API and worker separately. Confirm health/readiness, queue progress and logs without credentials/source contents.
7. Complete real Google login, refresh/logout, student onboarding/join-code denials, scoped admin upload/review/publication, student schedule/menu/RAG and private-original denials. Exercise worker restart/retry/concurrency and bucket anonymous/cross-user denials.
8. Record actual results and unresolved failures. Enable public traffic only after owner review. Rollback must preserve new data and use the reviewed recovery plan; do not blindly downgrade or reset.

Templates: `render.yaml`, `deploy/Dockerfile`, `frontend/vercel.json`, `config/staging/`. Automatic Render deployment is off. Follow [deployment configuration](deployment.md), [security](security.md) and [limitations](limitations.md). A Git push alone is not a deployment approval.
