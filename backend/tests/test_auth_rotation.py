"""Real signatures/JWKS HTTP with synthetic keys, never a Supabase project."""

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi import HTTPException

from backend.app import auth
from backend.tests.conftest import USER


@pytest.fixture
def issuer(monkeypatch):
    keys = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            assert self.path == "/auth/v1/.well-known/jwks.json"
            body = json.dumps({"keys": keys}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(auth, "settings", lambda: SimpleNamespace(supabase_url=base))
    auth.jwks_client.cache_clear()
    try:
        yield base, keys
    finally:
        auth.jwks_client.cache_clear()
        server.shutdown()
        server.server_close()
        thread.join(5)


def signed(base, private, kid, algorithm="RS256"):
    now = datetime.now(timezone.utc)
    return jwt.encode(dict(sub=USER, iss=base + "/auth/v1", aud="authenticated", iat=now,
                           exp=now + timedelta(minutes=10)), private, algorithm=algorithm, headers={"kid": kid})


def jwk(private, kid, algorithm="RS256"):
    cls = jwt.algorithms.RSAAlgorithm if algorithm == "RS256" else jwt.algorithms.ECAlgorithm
    return {**json.loads(cls.to_jwk(private.public_key())), "kid": kid, "alg": algorithm, "use": "sig"}


def test_retired_signing_key_is_rejected_after_jwks_cache_expires(issuer, monkeypatch):
    base, keys = issuer
    old = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    new = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    keys[:] = [jwk(old, "old"), jwk(new, "standby")]
    old_token = signed(base, old, "old")
    assert auth.verify_token(old_token) == USER
    assert auth.verify_token(signed(base, new, "standby")) == USER
    keys[:] = [jwk(new, "standby")]
    client = auth.jwks_client(base + "/auth/v1/.well-known/jwks.json")
    # Simulate passage of the configured TTL, without sleeping five minutes.
    monkeypatch.setattr(client.jwk_set_cache.jwk_set_with_timestamp, "get_timestamp",
                        lambda: time.monotonic() - 301)
    assert client.jwk_set_cache.is_expired()
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(old_token)
    assert exc.value.status_code == 401
    assert auth.verify_token(signed(base, new, "standby")) == USER


def test_es256_accepts_valid_tokens_and_rejects_forged_signature(issuer):
    base, keys = issuer
    private = ec.generate_private_key(ec.SECP256R1())
    keys[:] = [jwk(private, "elliptic", "ES256")]
    first = signed(base, private, "elliptic", "ES256")
    assert auth.verify_token(first) == USER
    forged = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(HTTPException) as exc:
        auth.verify_token(signed(base, forged, "elliptic", "ES256"))
    assert exc.value.status_code == 401
