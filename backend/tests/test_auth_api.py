from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select
from backend.app.main import app
from backend.app.auth import get_session
from backend.app import auth
from backend.app.models import Membership


@pytest.fixture
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key, user, **overrides):
    current = datetime.now(timezone.utc)
    claims = dict(
        sub=user,
        iss="https://auth.example.test/auth/v1",
        aud="authenticated",
        iat=current,
        exp=current + timedelta(hours=1),
    )
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


@pytest.fixture
def client(env, key, monkeypatch, running_api):
    from backend.app.mcp_server import mcp

    # TestClient's host is isolated from an operator's deployment host allowlist.
    monkeypatch.setattr(mcp.settings.transport_security, "allowed_hosts", ["testserver"])
    monkeypatch.setattr(auth, "settings", lambda: SimpleNamespace(supabase_url="https://auth.example.test"))
    monkeypatch.setattr(
        auth,
        "jwks_client",
        lambda _: SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=key.public_key())),
    )

    def db():
        with env.factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app.dependency_overrides[get_session] = db
    test = running_api
    test.headers.update({"Authorization": "Bearer " + token(key, env.user), "X-Tenant-ID": env.tenant})
    yield test
    app.dependency_overrides.clear()


def test_reject_unauthenticated(client):
    client.headers.pop("Authorization")
    assert client.get("/v1/overview").status_code == 401


def test_reject_forged_signature(client, env):
    fake = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    client.headers["Authorization"] = "Bearer " + token(fake, env.user)
    assert client.get("/v1/overview").status_code == 401


def test_reject_expired(client, key, env):
    client.headers["Authorization"] = "Bearer " + token(
        key, env.user, exp=datetime.now(timezone.utc) - timedelta(seconds=10)
    )
    assert client.get("/v1/overview").status_code == 401


def test_reject_bad_issuer(client, key, env):
    client.headers["Authorization"] = "Bearer " + token(key, env.user, iss="https://evil.test")
    assert client.get("/v1/overview").status_code == 401


def test_reject_bad_audience(client, key, env):
    client.headers["Authorization"] = "Bearer " + token(key, env.user, aud="evil")
    assert client.get("/v1/overview").status_code == 401


def test_tenant_forgery(client, env):
    client.headers["X-Tenant-ID"] = env.other_tenant
    assert client.get("/v1/menu").status_code == 403


def test_membership_revocation_denies_same_still_valid_jwt(client, env):
    assert client.get("/v1/me").status_code == 200
    token_before = client.headers["Authorization"]
    with env.factory() as db:
        membership = db.scalar(select(Membership).where(
            Membership.tenant_id == env.tenant, Membership.user_id == env.user))
        membership.active = False
        db.commit()
    assert client.headers["Authorization"] == token_before
    assert client.get("/v1/me").status_code == 403
    assert client.get("/v1/menu").status_code == 403


def test_actual_overview(client):
    response = client.get("/v1/overview")
    assert response.status_code == 200
    assert response.json()["notices"]


def test_department_profiles(client, env):
    profile = client.get("/v1/me").json()
    assert profile["department"] == "AIML"
    assert profile["role"] == "institution_admin"


def test_no_role_mass_assignment(client):
    response = client.patch(
        "/v1/me", json={"name": "Hello", "interests": "AI", "diet": "vegetarian", "role": "platform_admin"}
    )
    assert response.status_code == 422


def test_student_admin_api_denied(client, env, key):
    client.headers["Authorization"] = "Bearer " + token(key, env.other)
    assert client.get("/v1/admin/system").status_code == 403
    assert (
        client.post(
            "/v1/ingestion/preview",
            data={"options": '{"kind":"document"}'},
            files={"file": ("x.txt", b"Example notice", "text/plain")},
        ).status_code
        == 403
    )


def test_conversation_owner(client, env, key):
    answer = client.post("/v1/chat", json={"question": "Food under ₹100"})
    assert answer.status_code == 200
    cid = answer.json()["conversation_id"]
    client.headers["Authorization"] = "Bearer " + token(key, env.other)
    assert client.get("/v1/conversations/" + cid).status_code == 404


def test_conversation_followup(client):
    first = client.post("/v1/chat", json={"question": "Who teaches Machine Learning?"}).json()
    second = client.post(
        "/v1/chat",
        json={"question": "Where is that professor’s office?", "conversation_id": first["conversation_id"]},
    ).json()
    assert second["records"][0]["office"] == "Block A, Room 210"
    assert second["sources"]


def test_streaming_response(client):
    response = client.post("/v1/chat/stream", json={"question": "Food under ₹100"})
    assert response.status_code == 200
    assert "event: delta" in response.text
    assert "event: result" in response.text


def test_source_cross_tenant(client, env):
    from backend.app.models import Source

    source = env.db.scalar(select(Source).where(Source.tenant_id == env.other_tenant))
    assert client.get("/v1/sources/" + source.id).status_code == 404


def test_unknown_tool_denied(client):
    assert client.post("/v1/tools/run_sql", json={"sql": "SELECT * FROM memberships"}).status_code == 400


def test_no_identity_tool_parameter(client, env):
    response = client.post("/v1/tools/search_authorized_emails", json={"user_id": env.other})
    assert response.status_code == 400


def test_scope_change_invalidates_context(client, env):
    first = client.post("/v1/chat", json={"question": "Who teaches Machine Learning?"}).json()
    with env.factory() as db:
        member = db.scalar(
            select(Membership).where(Membership.user_id == env.user, Membership.tenant_id == env.tenant)
        )
        member.department = "ECE"
        db.commit()
    assert client.get("/v1/conversations/" + first["conversation_id"]).json() == []


def test_mcp_http_requires_auth(client):
    client.headers.pop("Authorization")
    assert client.post("/mcp/", json={}).status_code == 401


def test_google_unconfigured_is_explicit(client):
    response = client.post("/v1/integrations/google_gmail/connect")
    assert response.status_code == 503


def test_authenticated_mcp_http_tool_roundtrip(client, env, monkeypatch):
    from backend.app import mcp_server

    monkeypatch.setattr(mcp_server, "SessionLocal", env.factory)
    headers = {"Accept": "application/json, text/event-stream"}
    initialized = client.post(
        "/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "CampusOS HTTP verification", "version": "1"},
            },
        },
    )
    assert initialized.status_code == 200
    headers["MCP-Protocol-Version"] = initialized.json()["result"]["protocolVersion"]
    listed = client.post(
        "/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    )
    assert listed.status_code == 200
    assert len(listed.json()["result"]["tools"]) == 15
    result = client.post(
        "/mcp/",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "search_canteen_items", "arguments": {"budget": 100, "vegetarian": True}},
        },
    )
    assert result.status_code == 200
    payload = result.json()["result"]
    assert not payload.get("isError", False)
    import json

    data = json.loads(payload["content"][0]["text"])
    assert data["tenant_id"] == env.tenant
    assert data["data"]


def test_publication_http_workflow_and_direct_student_denials(client, env, key, monkeypatch):
    from backend.app.storage import ObjectStore
    from backend.app.ingestion import work_once
    from backend.app.models import Source, DocumentChunk, Document

    reads = []
    monkeypatch.setattr(ObjectStore, "put", lambda self, k, value: env.store.put(k, value))

    def get(self, k):
        reads.append(k)
        return env.store.get(k)

    monkeypatch.setattr(ObjectStore, "get", get)
    uploaded = client.post(
        "/v1/ingestion/preview", data={"options": '{"kind":"document","title":"Zirconium policy"}'},
        files={"file": ("policy.txt", b"Library zirconium policy says Friday.", "text/plain")},
    )
    assert uploaded.status_code == 200
    job = uploaded.json()
    sid = job["source_id"]
    assert job["source_status"] == "draft"
    client.headers["Authorization"] = "Bearer " + token(key, env.other)
    reads.clear()
    assert client.get("/v1/sources/" + sid).status_code in (403, 404)
    assert not reads
    for action in ("approve", "publish", "reject", "revoke"):
        assert client.post(f"/v1/admin/sources/{sid}/{action}", json={"reason": "Attempt"}).status_code == 403
    assert client.get(f"/v1/admin/sources/{sid}").status_code == 403
    assert client.post(f"/v1/ingestion/{job['id']}/confirm").status_code == 404
    client.headers["Authorization"] = "Bearer " + token(key, env.user)
    assert client.post(f"/v1/ingestion/{job['id']}/confirm").status_code == 200
    assert work_once(env.factory, env.store)
    client.headers["Authorization"] = "Bearer " + token(key, env.reviewer)
    review = client.get(f"/v1/admin/sources/{sid}")
    assert review.status_code == 200
    assert review.json()["status"] == "pending_review"
    assert review.json()["validation"]["passages"]
    # Revision 0009 permits explicit review by the uploading scoped admin.
    client.headers["Authorization"] = "Bearer " + token(key, env.user)
    assert client.post(f"/v1/admin/sources/{sid}/approve", json={}).status_code == 200
    client.headers["Authorization"] = "Bearer " + token(key, env.other)
    result = client.post("/v1/tools/search_documents", json={"query": "zirconium"}).json()["data"]
    assert sid not in {h["source_id"] for h in result["hits"]}
    assert client.get("/v1/sources/" + sid).status_code in (403, 404)
    client.headers["Authorization"] = "Bearer " + token(key, env.user)
    assert client.post(f"/v1/admin/sources/{sid}/publish", json={}).status_code == 200
    client.headers["Authorization"] = "Bearer " + token(key, env.other)
    assert client.get("/v1/sources/" + sid).content == b"Library zirconium policy says Friday."
    answer = client.post("/v1/chat", json={"question": "Library zirconium policy"}).json()
    assert sid in {s["id"] for s in answer["sources"]}
    client.headers["Authorization"] = "Bearer " + token(key, env.reviewer)
    assert client.post(f"/v1/admin/sources/{sid}/revoke", json={"reason": "Withdrawn"}).status_code == 200
    client.headers["Authorization"] = "Bearer " + token(key, env.other)
    assert client.get("/v1/sources/" + sid).status_code in (403, 404)
    history = client.get("/v1/conversations/" + answer["conversation_id"]).json()
    assert not any(m["role"] == "assistant" for m in history)
    env.db.expire_all()
    assert env.db.get(Source, sid).publication_status == "revoked"
    # Revocation hides retained chunks rather than deleting the audit trail.
    doc = env.db.scalar(select(Document).where(Document.source_id == sid))
    assert env.db.scalar(select(DocumentChunk).where(DocumentChunk.document_id == doc.id))


def test_publication_requires_authentication(client):
    client.headers.pop("Authorization")
    assert client.post("/v1/admin/sources/unknown/publish", json={}).status_code == 401


def test_mixed_original_download_denied_over_http(client, env, key, monkeypatch):
    from backend.app.models import Source
    from backend.app.storage import ObjectStore

    source = env.db.scalar(select(Source).where(Source.kind == "faculty", Source.tenant_id == env.tenant))
    reads = []
    monkeypatch.setattr(ObjectStore, "get", lambda self, k: reads.append(k) or env.store.get(k))
    client.headers["Authorization"] = "Bearer " + token(key, env.other)
    directory = client.get("/v1/faculty").json()
    assert directory and all(r["department"] == "CSE" for r in directory)
    assert client.get("/v1/sources/" + source.id).status_code == 403
    assert not reads
    client.headers["Authorization"] = "Bearer " + token(key, env.user)
    assert client.get("/v1/sources/" + source.id).status_code == 200


def test_private_document_http_cannot_be_read_or_published_by_admin(client, env, key, monkeypatch):
    from backend.app.storage import ObjectStore
    from backend.app.ingestion import work_once

    monkeypatch.setattr(ObjectStore, "put", lambda self, k, value: env.store.put(k, value))
    monkeypatch.setattr(ObjectStore, "get", lambda self, k: env.store.get(k))
    client.headers["Authorization"] = "Bearer " + token(key, env.other)
    job = client.post(
        "/v1/ingestion/preview", data={"options": '{"kind":"document","visibility":"private"}'},
        files={"file": ("notes.txt", b"Private zirconium notes", "text/plain")},
    ).json()
    assert client.post(f"/v1/ingestion/{job['id']}/confirm").status_code == 200
    assert work_once(env.factory, env.store)
    assert client.get("/v1/sources/" + job["source_id"]).status_code == 200
    client.headers["Authorization"] = "Bearer " + token(key, env.user)
    assert client.get("/v1/sources/" + job["source_id"]).status_code == 404
    assert client.get("/v1/admin/sources/" + job["source_id"]).status_code == 404
    assert client.post("/v1/admin/sources/" + job["source_id"] + "/publish", json={}).status_code == 404
    assert job["source_id"] not in {s["id"] for s in client.get("/v1/admin/system").json()["sources"]}


def test_demoted_uploader_cannot_read_raw_job_preview_or_confirm(client, env, key, monkeypatch):
    from backend.app.storage import ObjectStore

    monkeypatch.setattr(ObjectStore, "put", lambda self, k, value: env.store.put(k, value))
    monkeypatch.setattr(ObjectStore, "get", lambda self, k: env.store.get(k))
    job = client.post(
        "/v1/ingestion/preview", data={"options": '{"kind":"document"}'},
        files={"file": ("new.txt", b"Unpublished administrative document", "text/plain")},
    ).json()
    member = env.db.scalar(select(Membership).where(Membership.tenant_id == env.tenant, Membership.user_id == env.user))
    member.role = "student"
    env.db.commit()
    assert job["id"] not in {j["id"] for j in client.get("/v1/ingestion").json()}
    assert client.post(f"/v1/ingestion/{job['id']}/confirm").status_code == 403
