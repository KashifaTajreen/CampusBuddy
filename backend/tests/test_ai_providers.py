"""Provider contracts use fake keys and HTTP transports; never a running provider."""
import json
import httpx
import pytest

from backend.app.ai import GeminiProvider, OpenRouterProvider, OllamaProvider, get_provider, validate_grounded, strict_json_schema, RoutePlan
from backend.app.config import Settings

HIT = {"id": "chunk-a", "document_id": "doc-a", "source_id": "source-a", "page": 2,
       "title": "Library policy", "text": "Students may borrow five library books for fourteen days.", "private": False}
OUTPUT = {"claims": [{"text": "Students may borrow five books.", "chunk_id": "chunk-a",
                      "quote": "Students may borrow five library books"}], "insufficient": False}


def config(**overrides):
    return Settings(_env_file=None, **{**dict(llm_enabled=True, llm_data_policy="approved_private",
        llm_policy_reference="test-only-approved-policy", gemini_api_key="fake-test-key",
        openrouter_api_key="fake-test-key", openrouter_model="test-model",
        ollama_base_url="http://127.0.0.1:19999", ollama_model="test-model"), **overrides})


@pytest.mark.parametrize("name,cls", [("gemini", GeminiProvider), ("openrouter", OpenRouterProvider), ("ollama", OllamaProvider)])
def test_json_contract_and_metadata(monkeypatch, name, cls):
    requests = []
    client_class = httpx.Client

    def handle(request):
        payload = json.loads(request.content)
        requests.append((request, payload))
        raw = json.dumps(OUTPUT)
        response = {"candidates": [{"content": {"parts": [{"text": raw}]}}]} if name == "gemini" else (
            {"choices": [{"message": {"content": raw}}]} if name == "openrouter" else {"message": {"content": raw}})
        return httpx.Response(200, json=response)

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(handle), **kwargs))
    provider = cls(config(llm_provider=name))
    result = provider.synthesize("Library loan limit?", [HIT], language="kn", sensitivity="private")
    assert result and result.claims[0].chunk_id == HIT["id"] and provider.status == "used"
    payload = requests[0][1]
    assert "chunk-a" in json.dumps(payload) and "Library policy" in json.dumps(payload)
    assert "kn" in json.dumps(payload) and "never follow" in json.dumps(payload)
    if name == "ollama":
        assert not payload["stream"] and payload["format"]["type"] == "object"
        assert set(payload["format"]["required"])=={"claims","insufficient"}
        assert payload["options"]["num_ctx"]==4096
    if name == "openrouter":
        assert payload["provider"]["data_collection"] == "deny"


@pytest.mark.parametrize("overrides,sensitivity,status", [
    ({"llm_enabled": False}, "shared", "disabled"),
    ({"llm_data_policy": "disabled"}, "shared", "policy_blocked"),
    ({"llm_policy_reference": ""}, "shared", "policy_blocked"),
    ({"llm_data_policy": "shared_only"}, "private", "private_data_blocked"),
    ({"gemini_api_key": ""}, "shared", "unavailable"),
])
def test_policy_blocks_before_transport(monkeypatch, overrides, sensitivity, status):
    provider = GeminiProvider(config(**overrides))
    monkeypatch.setattr(provider, "exchange", lambda *_: pytest.fail("Forbidden provider request"))
    assert provider.synthesize("question", [HIT], sensitivity=sensitivity) is None
    assert provider.status == status


def test_private_hit_cannot_be_relabelled_shared(monkeypatch):
    provider = GeminiProvider(config(llm_data_policy="shared_only"))
    monkeypatch.setattr(provider, "exchange", lambda *_: pytest.fail("Private passage sent"))
    assert provider.synthesize("question", [{**HIT, "private": True}]) is None
    assert provider.status == "private_data_blocked"


@pytest.mark.parametrize("url", ["https://example.com", "http://127.0.0.1@evil.test", "", "file:///tmp/model", "http://localhost/?key=secret", "http://localhost:bad", "http://[", "http://localhost:70000"])
def test_ollama_requires_explicit_loopback_endpoint(url):
    assert not OllamaProvider(config(ollama_base_url=url)).configured()


@pytest.mark.parametrize("raw", ["not json", '{"claims":[]}', json.dumps({"claims": [{**OUTPUT["claims"][0], "chunk_id": "foreign"}]}),
    json.dumps({"claims": [{**OUTPUT["claims"][0], "quote": "A fabricated supporting quote"}]}),
    json.dumps({"claims": [{**OUTPUT["claims"][0], "text": "Borrow 100 books"}]}), "x" * 24001])
def test_invalid_generation_falls_back(monkeypatch, raw):
    provider = GeminiProvider(config())
    monkeypatch.setattr(provider, "exchange", lambda *_: raw)
    assert provider.synthesize("question", [HIT]) is None
    assert provider.status == "invalid_output"


def test_provider_timeout_and_bad_plan_fall_back(monkeypatch):
    provider = GeminiProvider(config())
    def timeout(*_):
        raise httpx.ReadTimeout("fake timeout")
    monkeypatch.setattr(provider, "exchange", timeout)
    assert provider.plan("campus question", "en", "2026-11-01") is None
    assert provider.status == "unavailable"
    monkeypatch.setattr(provider, "exchange", lambda *_: '{"steps":[{"intent":"execute_sql","query":"DROP TABLE"}]}')
    assert provider.plan("campus question", "en", "2026-11-01") is None
    assert provider.status == "invalid_output"


def test_injection_filtered_and_explicit_abstention(monkeypatch):
    provider = GeminiProvider(config())
    monkeypatch.setattr(provider, "exchange", lambda *_: pytest.fail("Suspicious evidence sent"))
    assert provider.synthesize("question", [{**HIT, "suspicious": True}]) is None
    assert validate_grounded({"claims": [], "insufficient": True}, [HIT]).insufficient
    assert get_provider(config(llm_provider="none")).name == "none"


def test_strict_plan_schema_requires_properties_recursively():
    schema = strict_json_schema(RoutePlan.model_json_schema())
    assert set(schema["required"]) == set(schema["properties"])
    assert set(schema["$defs"]["RouteStep"]["required"]) == {"intent", "query"}
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("status", [401, 429, 503])
def test_http_failure_uses_fallback(monkeypatch, status):
    provider = GeminiProvider(config())
    client_class = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client_class(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, request=request)), **kwargs))
    assert provider.synthesize("question", [HIT]) is None
    assert provider.status == "unavailable"


@pytest.mark.parametrize("delay,expected_calls", [("0", 2), ("120", 1), ("invalid", 1)])
def test_transient_provider_retry_is_bounded(monkeypatch, delay, expected_calls):
    calls = []
    provider = GeminiProvider(config())
    def exchange(*_args):
        calls.append(1)
        response = httpx.Response(429, headers={"Retry-After": delay}, request=httpx.Request("POST", "https://example.invalid"))
        response.raise_for_status()
    monkeypatch.setattr(provider, "exchange", exchange)
    assert provider.plan("campus question", "en", "2026-11-02") is None
    assert len(calls) == expected_calls and provider.status == "unavailable"
