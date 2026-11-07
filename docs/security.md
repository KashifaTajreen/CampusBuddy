# Threat model and deployment checklist

Phase 6B adds bounded department/semester/section source scope, exactly five roles,
PostgreSQL RLS/lifecycle checks, sanitized cross-uploader menu editing and
authorization-view cache partitions. Structured originals remain separately
restricted; scoped rows never imply access to an entire mixed file. See the
[current role matrix and migration gate](PRODUCT_ACCEPTANCE.md). Current revision 0009 permits explicit same-admin approval within scope; it does not relax validation, membership or publication checks. No staging or production security acceptance is claimed by the local test identity fixture.

Phase 4 model requests are disabled by default and gated by an operator-controlled
data policy. Raw questions are treated as potentially private; external chat
requires `approved_private` plus a policy reference. Private evidence ownership is
server-derived. The model cannot choose SQL or authorization scope. See
[provider privacy and grounding limits](phase4.md).

## Assets and trust boundaries

Shared source publication and unredacted-original permissions are enforced separately
from row filtering. See [publication controls](publication.md) for lifecycle rules,
private ownership, legacy migration behavior and the verification commands.

Protected assets include private email/calendar content, conversations, institutional sources, OAuth credentials and database/storage credentials. The browser, uploaded files, source passages, Google content and user-entered questions are untrusted. Supabase Auth signs identity; database memberships grant institution access. A compromised API/worker host or database owner is outside the protection provided by application RLS and must be addressed operationally.

Signing keys use a bounded five-minute JWKS cache; individual PyJWT keys are not
cached indefinitely. This still allows provider/app cache propagation time and
does not implement online session introspection. Membership revocation is checked
on the next request. See [integration verification](verification.md)
for executed local tests and the unverified staging acceptance procedure.

Selected-document IDs are untrusted selectors. Shared service scope and existing
RLS recheck tenant, owner, department and published parent before chunk retrieval,
including conflict expansion and citations. History rechecks selection metadata
even when an answer had no evidence/citations. Revoked/deleted selections never
silently fall back to a broader corpus. Phase 3 staging tools default to a plan
that reads no credentials or network; explicit project/write safeguards supplement
the required human approval. See [staging preparation](../config/staging/README.md).

| Threat | Implemented control | Remaining work/operational control |
|---|---|---|
| Forged/expired token | JWKS signature, fixed issuer/audience, expiry and subject validation; no unsigned/HS fallback | Enforce account revocation/session policies; short-lived JWTs can remain valid until expiry |
| Cross-institution request | Verified membership + tenant predicates + PostgreSQL RLS | Exercise RLS against production grants; restrict DB credentials |
| Cross-owner private records | Owner predicates, private sources and conversation checks | Protect disk/backups; implement institution-approved retention policy |
| Privilege escalation | Roles stored in memberships; no client role metadata; admin API and restricted DB grants | Admin invitation/revocation lifecycle and operator review |
| Malicious SQL/code request | Fixed tools and parameterized SQLAlchemy; no generated execution | Review future tools under the same boundary |
| Source prompt injection | Sources treated as data, instruction-like excerpts withheld, LLM has no execution tools | Heuristic detection is incomplete; generated summaries still require source review |
| Malicious upload | Size/type bounds, archive limits, bounded local OCR, process memory/time limits, explicit review and atomic publication | Deployment filesystem/network isolation, parser patches and malware scanning |
| OAuth interception/replay | PKCE, encrypted single-use state, 10-minute state TTL, read-only scopes | HTTPS, exact redirects, provider consent configuration and live verification |
| Token theft | Fernet encryption at rest for Google tokens, server-only secrets | Key management/rotation; XSS prevention and endpoint/device security |
| XSS through sources | React text rendering; original files forced to attachment; no HTML execution | Configure CSP for actual auth/API origins and test it with deployment |
| Abuse/resource exhaustion | Shared DB rate buckets, bounded tools, bounded retrieval and network timeouts | Reverse-proxy IP throttling, body limits, parser isolation and load tests |
| Sensitive logs | No query strings, question bodies or source text in application logs | Disable proxy/Uvicorn access logs on OAuth paths; configure monitoring redaction |
| Stale conversation scope | Invalidate context/history on department/section/role change; recheck source access | More granular permission-change invalidation if per-document ACLs are added |

## Operator-approved Google administrator exception

An operator may approve one exact pinned Google administrator identity.
`GOOGLE_ADMIN_EMAIL` and `GOOGLE_ADMIN_USER_ID` are server-only configuration:
both must match the signed Supabase claims. `verify_google_identity` additionally
checks the Auth service's confirmed, provider-owned Google identity;
`active_google_admin` rechecks an active `institution_admin` membership on every
request. Revocation takes effect at the next request. Neither frontend role cards
nor editable user metadata grant access. An unconfigured pin fails closed.

The administrator exception is denied by `onboarding_user`; it cannot grant student enrollment or bypass join codes. A separate default-off, development-only [test-student exception](TEMPORARY_TEST_STUDENT.md) still requires verified identity pins and normal onboarding. Other Google accounts retain the verified
`@rvce.edu.in` restriction. Initial provisioning is an operator-only command for
the exact locally configured account after recent real Google login; it is not a
public API. Its trusted connection reads only that account's Auth identity fields
and creates the first institution/membership only on an empty application baseline.
It never changes Auth accounts or grants PostgreSQL administrator privileges.

## Before a real-institution pilot

- [ ] Use HTTPS and a restricted non-owner, non-BYPASSRLS API database login.
- [ ] Apply migrations with a trusted owner and inspect all table/function grants.
- [ ] Verify RLS under the **actual API login**, including tenant, department and private-owner denial.
- [ ] Confirm the Supabase bucket is private and direct client downloads are denied.
- [ ] Keep service, database, Gemini and Google credentials out of frontend bundles and Git.
- [ ] Use real Supabase Auth invitations, strong password policies and MFA where appropriate.
- [ ] Confirm user membership activation, role assignment and removal procedures.
- [ ] Separate trusted migration/worker credentials from API credentials.
- [ ] Approve external processing before enabling Gemini or Google account imports.
- [ ] Test Google refresh/revocation and provider outages with an authorized test account.
- [ ] Apply request-size limits and resource limits; isolate PDF/XLSX parsing.
- [ ] Disable sensitive access logs and protect conversation/source backups.
- [ ] Define retention, deletion, incident response and backup-restore procedures.
- [ ] Run dependency/security scanning, load tests and an independent security review.
- [ ] Complete the cloud acceptance checks in `docs/verification.md`.

This repository is a tested vertical slice with security boundaries, not an audited or certified enterprise deployment. Default free tiers do not supply enterprise availability or compliance guarantees.
