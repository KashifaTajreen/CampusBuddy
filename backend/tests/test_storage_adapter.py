"""Adapter contract tests with fake keys and mocked HTTP, not cloud acceptance."""

from types import SimpleNamespace

import httpx
import pytest

from backend.app import storage


def configured(**overrides):
    return storage.ObjectStore(SimpleNamespace(storage_backend="supabase",
        supabase_url="https://storage.example.invalid", supabase_storage_bucket="test private",
        supabase_service_role_key="synthetic-key-not-a-credential", **overrides))


def test_private_storage_upload_and_download_contract(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer synthetic-key-not-a-credential"
        assert request.headers["apikey"] == "synthetic-key-not-a-credential"
        assert request.url.raw_path == b"/storage/v1/object/test%20private/tenant/source%20name"
        if request.method == "POST":
            assert request.headers["x-upsert"] == "false"
            assert request.content == b"Synthetic private document"
            return httpx.Response(200, json={"Key": "fixture"})
        return httpx.Response(200, content=b"Synthetic private document")

    client = httpx.Client
    monkeypatch.setattr(storage.httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    store = configured()
    store.put("tenant/source name", b"Synthetic private document")
    source = SimpleNamespace(storage_key="tenant/source name")
    service = SimpleNamespace(original_source=lambda sid: source)
    assert store.download(service, "source")[1] == b"Synthetic private document"
    assert [r.method for r in requests] == ["POST", "GET"]


@pytest.mark.parametrize("status", [401, 403, 404, 503])
def test_storage_provider_denials_and_outages_are_propagated(monkeypatch, status):
    client = httpx.Client
    monkeypatch.setattr(storage.httpx, "Client", lambda **kw: client(
        transport=httpx.MockTransport(lambda _: httpx.Response(status)), **kw))
    with pytest.raises(httpx.HTTPStatusError):
        configured().get("synthetic/object")


def test_missing_storage_credential_refuses_request():
    store = configured()
    store.config.supabase_service_role_key = ""
    with pytest.raises(ValueError, match="not configured"):
        store.headers()


def test_local_storage_round_trip_and_path_traversal_denial(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    store = storage.ObjectStore(SimpleNamespace(storage_backend="local"))
    store.put("synthetic/source", b"fixture bytes")
    assert store.get("synthetic/source") == b"fixture bytes"
    with pytest.raises(ValueError, match="Invalid storage key"):
        store.get("../../../outside.txt")
    store.delete("synthetic/source")
    store.delete("synthetic/source")  # Idempotent recovery after storage success / DB failure.
    assert not store.local_path("synthetic/source").exists()
    with pytest.raises(ValueError):
        store.delete("../../outside")


def test_private_storage_delete_contract_uses_exact_object_key(monkeypatch):
    import json
    requests = []
    client = httpx.Client
    def handler(request):
        requests.append(request)
        assert request.method == "DELETE" and request.url.path == "/storage/v1/object/test private"
        assert json.loads(request.content) == {"prefixes": ["tenant/source name"]}
        return httpx.Response(200, json=[])
    monkeypatch.setattr(storage.httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))
    configured().delete("tenant/source name")
    assert len(requests) == 1
