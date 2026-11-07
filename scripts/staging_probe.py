"""Operator-run acceptance probes. Default mode reads no credentials and uses no network.

Execution flags are safeguards for an authorized operator, not permission for an
assistant to access staging. This module never creates projects/buckets, rotates
keys, modifies memberships, starts workers or changes database URLs on disk.
"""

import argparse
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote, urlparse
from uuid import uuid4

import httpx
import jwt


class ProbeError(ValueError):
    pass


def approved_config(path, approved_ref, approved_api=None):
    if not path or not approved_ref or not re.fullmatch(r"[a-z0-9]{20}", approved_ref):
        raise ProbeError("An explicit config and exact approved staging project reference are required.")
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    if (cfg.get("environment") != "staging-test-only" or cfg.get("project_ref") != approved_ref
            or cfg.get("supabase_url") != f"https://{approved_ref}.supabase.co"):
        raise ProbeError("Staging identity does not match the explicit approved project.")
    if approved_api is not None:
        parsed = urlparse(approved_api)
        if (cfg.get("api_origin") != approved_api or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or not (parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"))):
            raise ProbeError("An exact approved HTTPS staging API origin (or local loopback API) is required.")
    return cfg


def require_success(response, step):
    if not 200 <= response.status_code < 300:
        raise ProbeError(f"{step} failed with HTTP {response.status_code}; response details withheld.")
    return response


def verify_access(cfg, token):
    # Reuse the actual backend verifier without opening the operator's local DB.
    with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
        from backend.app import auth
    with patch.object(auth, "settings", lambda: SimpleNamespace(supabase_url=cfg["supabase_url"])):
        return auth.verify_token(token)


def login(client, cfg, prefix=""):
    for key in ("email", "password", "expected_user_id", "publishable_key"):
        if not cfg.get(prefix + key if key != "publishable_key" else key):
            raise ProbeError("Dedicated test account credentials and expected user ID are required.")
    response = require_success(client.post(cfg["supabase_url"] + "/auth/v1/token?grant_type=password",
        headers={"apikey": cfg["publishable_key"]},
        json={"email": cfg[prefix + "email"], "password": cfg[prefix + "password"]}), "Test sign-in")
    session = response.json()
    if verify_access(cfg, session["access_token"]) != cfg[prefix + "expected_user_id"]:
        raise ProbeError("Signed-in identity differs from the approved test account.")
    return session


def auth_probe(client, cfg, revoke=False):
    initial = login(client, cfg)
    refreshed = require_success(client.post(cfg["supabase_url"] + "/auth/v1/token?grant_type=refresh_token",
        headers={"apikey": cfg["publishable_key"]}, json={"refresh_token": initial["refresh_token"]}),
        "Token refresh").json()
    if verify_access(cfg, refreshed["access_token"]) != cfg["expected_user_id"]:
        raise ProbeError("Refreshed identity changed unexpectedly.")
    kid = jwt.get_unverified_header(refreshed["access_token"]).get("kid")
    if cfg.get("expected_signing_kid") and kid != cfg["expected_signing_kid"]:
        raise ProbeError("Token does not use the operator-approved rotated signing key.")
    report = dict(login=True, refreshed_signature=True,
                  expected_rotated_key_checked=bool(cfg.get("expected_signing_kid")),
                  session_revocation="not_requested", provider_key_retirement="manual_acceptance_required")
    if revoke:
        require_success(client.post(cfg["supabase_url"] + "/auth/v1/logout?scope=local",
            headers={"apikey": cfg["publishable_key"], "Authorization": "Bearer " + refreshed["access_token"]}),
            "Test-session logout")
        denied = client.post(cfg["supabase_url"] + "/auth/v1/token?grant_type=refresh_token",
            headers={"apikey": cfg["publishable_key"]}, json={"refresh_token": refreshed["refresh_token"]})
        if denied.status_code not in (400, 401, 403):
            raise ProbeError("Revoked refresh token did not receive the expected authentication denial.")
        report["session_revocation"] = "refresh_denied_access_JWT_may_remain_valid_until_expiry"
    return report


def storage_probe(client, cfg):
    from backend.app.storage import ObjectStore

    bucket = cfg.get("bucket", "")
    if not re.fullmatch(r"campus-verify-[a-z0-9-]{1,60}", bucket) or not cfg.get("service_role_key"):
        raise ProbeError("Storage writes require a dedicated campus-verify-* bucket and approved server key.")
    store = ObjectStore(SimpleNamespace(storage_backend="supabase", supabase_url=cfg["supabase_url"],
        supabase_storage_bucket=bucket, supabase_service_role_key=cfg["service_role_key"]))
    metadata = require_success(client.get(cfg["supabase_url"] + "/storage/v1/bucket/" + quote(bucket, safe=""),
                                           headers=store.headers()), "Bucket inspection").json()
    if metadata.get("public") is not False:
        raise ProbeError("Bucket is not explicitly private. No object was uploaded.")
    other = login(client, cfg, "other_")
    if cfg["other_expected_user_id"] == cfg["expected_user_id"]:
        raise ProbeError("Cross-user denial requires distinct approved test accounts.")
    key = "verification/" + uuid4().hex + "/synthetic.txt"
    payload = b"CampusOS isolated storage acceptance fixture."
    try:
        store.put(key, payload)
        if store.get(key) != payload:
            raise ProbeError("Privileged adapter round-trip returned different bytes.")
        urls = [store.url(key), cfg["supabase_url"] + "/storage/v1/object/public/" + quote(bucket, safe="") + "/" + key]
        for url in urls:
            for headers in ({}, {"apikey": cfg["publishable_key"]},
                            {"apikey": cfg["publishable_key"], "Authorization": "Bearer " + other["access_token"]}):
                response = client.get(url, headers=headers)
                if response.status_code not in (400, 401, 403, 404) or response.content == payload:
                    raise ProbeError("Direct object denial was not established; inspect staging bucket policies.")
        return dict(private_bucket=True, adapter_round_trip=True, anonymous_and_other_user_denied=True)
    finally:
        # Exact newly generated key only. Never delete a bucket or another prefix.
        require_success(client.request("DELETE", cfg["supabase_url"] + "/storage/v1/object/" + quote(bucket, safe=""),
            headers=store.headers(), json={"prefixes": [key]}), "Test object cleanup")


def api_probe(client, cfg, worker_snapshot=False):
    owner = login(client, cfg)
    headers = {"Authorization": "Bearer " + owner["access_token"], "X-Tenant-ID": cfg["tenant_id"]}
    origin = cfg["api_origin"].rstrip("/")
    if worker_snapshot:
        jobs = require_success(client.get(origin + "/v1/ingestion", headers=headers), "Worker snapshot").json()
        job = next((j for j in jobs if j["id"] == cfg.get("worker_job_id")), None)
        if not job:
            raise ProbeError("The approved test job is not visible in this uploader's recent jobs.")
        return dict(job_id=job["id"], status=job["status"], attempts=job["attempts"], source_status=job["source_status"],
                    restart_execution="manual_operator_action_required")
    source_id = cfg.get("private_source_id")
    if not source_id or not cfg.get("private_document_id"):
        raise ProbeError("An approved synthetic private document/source pair is required.")
    owner_profile = require_success(client.get(origin + "/v1/me", headers=headers), "Owner membership").json()
    if owner_profile.get("role") != "student":
        raise ProbeError("The approved private-document owner must have a verified student membership.")
    require_success(client.get(origin + "/v1/sources/" + source_id, headers=headers), "Owner API download")
    selected_owner = require_success(client.post(origin + "/v1/chat", headers=headers,
        json={"question": "Synthetic privacy verification", "document_id": cfg["private_document_id"]}),
        "Owner selected-document access").json()
    if selected_owner.get("selected_document", {}).get("source_id") != source_id:
        raise ProbeError("Approved private document and source do not match.")
    if client.get(origin + "/v1/sources/" + source_id).status_code != 401:
        raise ProbeError("Anonymous API download was not denied.")
    checked = []
    for prefix in ("other_", "admin_"):
        account = login(client, cfg, prefix)
        if cfg[prefix + "expected_user_id"] == cfg["expected_user_id"]:
            raise ProbeError("Private denial checks require distinct test identities.")
        scoped = {**headers, "Authorization": "Bearer " + account["access_token"]}
        profile = require_success(client.get(origin + "/v1/me", headers=scoped), "Denial account membership").json()
        allowed_roles = ("student",) if prefix == "other_" else ("institution_admin", "platform_admin")
        if profile.get("role") not in allowed_roles:
            raise ProbeError("A denial account does not have its expected verified staging membership role.")
        if client.get(origin + "/v1/sources/" + source_id, headers=scoped).status_code not in (403, 404):
            raise ProbeError("Cross-owner API download was not denied.")
        offset = 0
        for _ in range(101):
            page = require_success(client.get(origin + f"/v1/documents?limit=100&offset={offset}",
                                             headers=scoped), "Cross-owner document listing").json()
            encoded = json.dumps(page)
            if cfg["private_document_id"] in encoded or source_id in encoded:
                raise ProbeError("Private document metadata appeared in another account's listing.")
            next_offset = page.get("next_offset")
            if next_offset is None:
                break
            if not isinstance(next_offset, int) or next_offset <= offset:
                raise ProbeError("Unexpected document pagination; denial could not be verified.")
            offset = next_offset
        else:
            raise ProbeError("Document list exceeded probe bounds; metadata denial is inconclusive.")
        selected = client.post(origin + "/v1/chat", headers=scoped,
            json={"question": "Synthetic privacy verification", "document_id": cfg["private_document_id"]})
        if selected.status_code != 404:
            raise ProbeError("Cross-owner document selection was not denied.")
        checked.append(prefix.rstrip("_"))
    return dict(owner_download=True, anonymous_denied=True, cross_owner_denied=checked, metadata_denied=True,
                membership_revocation="manual_operator_action_and_same_token_recheck_required")


def membership_probe(client, cfg):
    session = login(client, cfg)
    headers = {"Authorization": "Bearer " + session["access_token"], "X-Tenant-ID": cfg["tenant_id"]}
    endpoint = cfg["api_origin"].rstrip("/") + "/v1/me"
    require_success(client.get(endpoint, headers=headers), "Active membership")
    input("In a separate authorized staging terminal, deactivate only this test membership. Press Enter after completion: ")
    if client.get(endpoint, headers=headers).status_code != 403:
        raise ProbeError("Membership revocation did not deny the same still-issued access token.")
    return dict(next_request_denied=True, same_access_token=True, restore_test_membership="manual_operator_action")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", choices=["auth", "storage", "api", "membership", "worker-snapshot"], default="auth")
    parser.add_argument("--execute", action="store_true", help="Use only after explicit staging approval; creates test Auth sessions.")
    parser.add_argument("--config")
    parser.add_argument("--approved-project-ref")
    parser.add_argument("--approved-api-origin")
    parser.add_argument("--allow-test-writes", action="store_true")
    parser.add_argument("--revoke-test-session", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        print(json.dumps(dict(mode="plan_only", network_access=False, credentials_read=False,
            check=args.check, required_approval="Named non-production project, dedicated test accounts and scoped writes.",
            manual_checks=["Membership revocation", "Operator-approved signing-key rotation/retirement",
                           "Hosted worker restart and shared-storage operation"]), indent=2))
        return 0
    try:
        if (args.check in ("storage", "api") or args.revoke_test_session) and not args.allow_test_writes:
            raise ProbeError("Explicit --allow-test-writes is required before object/API/session changes.")
        if args.check in ("api", "membership", "worker-snapshot") and not args.approved_api_origin:
            raise ProbeError("An explicit approved API origin is required.")
        cfg = approved_config(args.config, args.approved_project_ref, args.approved_api_origin)
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            if args.check == "auth":
                result = auth_probe(client, cfg, args.revoke_test_session)
            elif args.check == "storage":
                result = storage_probe(client, cfg)
            elif args.check == "membership":
                result = membership_probe(client, cfg)
            else:
                result = api_probe(client, cfg, args.check == "worker-snapshot")
        print(json.dumps(dict(check=args.check, result=result), indent=2))
        return 0
    except Exception as exc:
        # No token/password/key, provider body or exception URL is printed.
        print(json.dumps(dict(check=args.check, failed=True,
            reason=str(exc) if isinstance(exc, ProbeError) else type(exc).__name__ + ": details withheld")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
