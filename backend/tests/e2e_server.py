"""TEST ONLY: isolated API + RSA-signed identity fixture for browser verification.

This is not imported by the production app. It never accepts real institutional credentials.
It exercises real JWT verification, memberships, ingestion and storage using an isolated database.
"""

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

USER = "00000000-0000-4000-8000-000000000002"
OTHER = "00000000-0000-4000-8000-000000000003"
ADMIN = "00000000-0000-4000-8000-000000000005"
BASE = "http://127.0.0.1:8766"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
JWK.update(kid="browser-test", alg="RS256", use="sig")


def identity(user):
    return dict(
        id=user,
        aud="authenticated",
        role="authenticated",
        email="admin@example.invalid" if user == ADMIN else "student@example.invalid",
        email_confirmed_at="2026-10-28T00:00:00Z",
        created_at="2026-10-28T00:00:00Z",
        app_metadata={"provider": "email", "providers": ["email"]},
        user_metadata={},
        identities=[],
    )


def session(user):
    current = datetime.now(timezone.utc)
    token = jwt.encode(
        dict(
            sub=user,
            iss=BASE + "/auth/v1",
            aud="authenticated",
            iat=current,
            exp=current + timedelta(hours=1),
        ),
        KEY,
        algorithm="RS256",
        headers={"kid": "browser-test"},
    )
    return dict(
        access_token=token,
        refresh_token="test-only-refresh",
        token_type="bearer",
        expires_in=3600,
        expires_at=int(current.timestamp()) + 3600,
        user=identity(user),
    )


class TestIdentity(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, body, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:3000")
        self.send_header(
            "Access-Control-Allow-Headers",
            "authorization,apikey,content-type,x-client-info,x-supabase-api-version",
        )
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_OPTIONS(self):
        self.respond({})

    def do_GET(self):
        if self.path.endswith("jwks.json"):
            self.respond({"keys": [JWK]})
        elif self.path.startswith("/auth/v1/user"):
            try:
                claims = jwt.decode(
                    self.headers.get("Authorization", "")[7:],
                    KEY.public_key(),
                    algorithms=["RS256"],
                    audience="authenticated",
                    issuer=BASE + "/auth/v1",
                )
                self.respond(identity(claims["sub"]))
            except jwt.PyJWTError:
                self.respond({"message": "Invalid test token"}, 401)
        else:
            self.respond({"test_identity_provider": True})

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        if self.path.startswith("/auth/v1/token"):
            if data.get("password") != "CampusTest123!":
                return self.respond(
                    {"error": "invalid_credentials", "error_description": "Invalid test credentials"}, 400
                )
            self.respond(session(ADMIN if data.get("email") == "admin@example.invalid" else USER))
        else:
            self.respond({})


def run():
    os.environ["DATABASE_URL"] = "sqlite:///./data/e2e.db"
    os.environ["SUPABASE_URL"] = BASE
    os.environ["FRONTEND_ORIGIN"] = "http://127.0.0.1:3000"
    os.environ["APP_ENV"] = "development"
    # The whole suite signs in as the same few users within one minute.
    os.environ["RATE_LIMIT_PER_MINUTE"] = "5000"
    from backend.app.db import SessionLocal, initialize_local, engine
    from backend.app.models import Membership, Base
    from backend.app.seed import seed, DEMO_TENANT
    from sqlalchemy import select

    assert engine.url.database == "./data/e2e.db", "Only the isolated browser-test database may be reset."
    Base.metadata.drop_all(engine)
    initialize_local()
    with SessionLocal() as db:
        seed(db, ADMIN, anchor=datetime.now().date())
        db.add(
            Membership(
                tenant_id=DEMO_TENANT,
                user_id=USER,
                role="student",
                department="AIML",
                semester=3,
                section="A",
                name="Aarav",
                diet="vegetarian",
            )
        )
        if not db.scalar(
            select(Membership).where(Membership.user_id == ADMIN, Membership.tenant_id == DEMO_TENANT)
        ):
            db.add(
                Membership(
                    tenant_id=DEMO_TENANT,
                    user_id=ADMIN,
                    role="institution_admin",
                    department="AIML",
                    semester=3,
                    section="A",
                    name="Campus Admin",
                )
            )
        db.commit()
    server = ThreadingHTTPServer(("127.0.0.1", 8766), TestIdentity)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    from backend.app.ingestion import work_once

    def worker():
        import time

        while True:
            if not work_once(SessionLocal):
                time.sleep(0.25)

    threading.Thread(target=worker, daemon=True).start()
    import uvicorn

    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    run()
