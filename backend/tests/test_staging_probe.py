"""Staging tools are exercised only with fake configuration and mocked HTTP."""

import json
from pathlib import Path

import httpx
import jwt
import pytest

from scripts import staging_probe as probe


def config():
    return dict(environment="staging-test-only", project_ref="a" * 20,
        supabase_url="https://" + "a" * 20 + ".supabase.co", publishable_key="synthetic-public",
        service_role_key="synthetic-secret", email="owner@example.invalid", password="synthetic-password",
        expected_user_id="owner-test", other_email="other@example.invalid", other_password="synthetic-password",
        other_expected_user_id="other-test", bucket="campus-verify-unit", api_origin="https://api.example.invalid")


def test_default_plan_does_not_read_credentials_or_open_network(monkeypatch, capsys):
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kw: pytest.fail("Credential read"))
    monkeypatch.setattr(probe.httpx, "Client", lambda **_: pytest.fail("Network construction"))
    assert probe.main(["--config", "does-not-exist.json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["network_access"] is False and body["credentials_read"] is False


@pytest.mark.parametrize("args", [[], ["--check", "storage"], ["--check", "api"], ["--check", "auth", "--revoke-test-session"]])
def test_execute_requires_explicit_target_and_write_approvals(monkeypatch, args):
    monkeypatch.setattr(probe.httpx, "Client", lambda **_: pytest.fail("Network construction"))
    assert probe.main(["--execute", *args]) == 1


@pytest.mark.parametrize("field,value", [("environment", "production"), ("project_ref", "b" * 20),
                                        ("supabase_url", "https://production.example.invalid")])
def test_mismatched_project_config_is_refused(tmp_path, field, value):
    cfg = config()
    cfg[field] = value
    path = tmp_path / "probe.local.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(probe.ProbeError):
        probe.approved_config(path, "a" * 20)


def test_auth_refresh_and_session_revocation_probe_contract(monkeypatch):
    cfg = config()
    token = jwt.encode({}, "synthetic-unit-key-not-a-real-credential", algorithm="HS256", headers={"kid": "rotated"})
    requests = []
    refreshes = 0

    def respond(request):
        nonlocal refreshes
        requests.append(request)
        if "/logout" in str(request.url):
            return httpx.Response(204)
        if "refresh_token" in str(request.url):
            refreshes += 1
            if refreshes == 2:
                return httpx.Response(400)
        return httpx.Response(200, json={"access_token": token, "refresh_token": "synthetic-refresh"})

    monkeypatch.setattr(probe, "verify_access", lambda *_: "owner-test")
    cfg["expected_signing_kid"] = "rotated"
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = probe.auth_probe(client, cfg, revoke=True)
    assert result["login"] and result["refreshed_signature"] and result["expected_rotated_key_checked"]
    assert "refresh_denied" in result["session_revocation"] and len(requests) == 4


def test_public_bucket_stops_before_any_object_write(monkeypatch):
    def respond(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"public": True})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(probe.ProbeError, match="not explicitly private"):
            probe.storage_probe(client, config())


def test_storage_probe_roundtrip_denials_and_exact_key_cleanup(monkeypatch):
    cfg = config()
    payload = b"CampusOS isolated storage acceptance fixture."
    deleted = []
    uploaded = []

    def respond(request):
        if "/bucket/" in str(request.url):
            return httpx.Response(200, json={"public": False})
        if request.method == "DELETE":
            deleted.extend(json.loads(request.content)["prefixes"])
            return httpx.Response(200, json=[])
        if request.method == "POST":
            assert request.headers["x-upsert"] == "false"
            uploaded.append(request.url.path.split("campus-verify-unit/", 1)[1])
            return httpx.Response(200, json={})
        if request.headers.get("Authorization") == "Bearer synthetic-secret":
            return httpx.Response(200, content=payload)
        return httpx.Response(403)

    monkeypatch.setattr(probe, "login", lambda *_: {"access_token": "synthetic-other-token"})
    original = httpx.Client
    monkeypatch.setattr(probe.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    with probe.httpx.Client() as client:
        result = probe.storage_probe(client, cfg)
    assert result["private_bucket"] and result["anonymous_and_other_user_denied"]
    assert len(deleted) == 1 and deleted == uploaded
    assert deleted[0].startswith("verification/") and deleted[0].endswith("/synthetic.txt")


def test_membership_probe_reuses_same_token_after_operator_action(monkeypatch):
    cfg = {**config(), "tenant_id": "synthetic-tenant"}
    headers = []
    monkeypatch.setattr(probe, "login", lambda *_: {"access_token": "synthetic-issued-token"})
    monkeypatch.setattr("builtins.input", lambda _: "")

    def respond(request):
        headers.append(request.headers["Authorization"])
        return httpx.Response(200 if len(headers) == 1 else 403)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = probe.membership_probe(client, cfg)
    assert result["next_request_denied"] and headers == ["Bearer synthetic-issued-token"] * 2
