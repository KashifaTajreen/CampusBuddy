"""TEST ONLY: isolated API + RSA-signed identity fixture for browser verification.

This is not imported by the production app. It never accepts real institutional credentials.
It exercises real JWT verification, memberships, ingestion and storage using an isolated database.
"""

import json
import os
import threading
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

USER = "00000000-0000-4000-8000-000000000002"
OTHER = "00000000-0000-4000-8000-000000000003"
ADMIN = "00000000-0000-4000-8000-000000000005"
REVIEWER = "00000000-0000-4000-8000-000000000006"
SECTION_ADMIN = "00000000-0000-4000-8000-000000000007"
DEPARTMENT_ADMIN = "00000000-0000-4000-8000-000000000008"
CANTEEN_MANAGER = "00000000-0000-4000-8000-000000000009"
SECTION_B = "00000000-0000-4000-8000-000000000011"
PROJECT_STUDENTS = {name:f"00000000-0000-4000-8000-{n:012}" for name,n in (("desktop",70),("mobile",71),("tablet",72))}
BASE = "http://127.0.0.1:" + os.environ.get("CAMPUS_TEST_AUTH_PORT", "8766")
FRONTEND = "http://127.0.0.1:" + os.environ.get("CAMPUS_TEST_FRONTEND_PORT", "3000")
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
JWK.update(kid="browser-test", alg="RS256", use="sig")


def identity(user):
    return dict(
        id=user,
        aud="authenticated",
        role="authenticated",
        email="admin@example.invalid" if user == ADMIN else "reviewer@example.invalid" if user == REVIEWER else "student@example.invalid",
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
        self.send_header("Access-Control-Allow-Origin", FRONTEND)
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
            user = {"admin@example.invalid": ADMIN, "reviewer@example.invalid": REVIEWER,
                "section@example.invalid": SECTION_ADMIN, "department@example.invalid": DEPARTMENT_ADMIN,
                "canteen@example.invalid": CANTEEN_MANAGER, "section-b@example.invalid": SECTION_B,
                **{f"student-{name}@example.invalid":who for name,who in PROJECT_STUDENTS.items()}}.get(data.get("email"), USER)
            self.respond(session(user))
        else:
            self.respond({})


def run():
    from backend.tests.process_support import fixture_directory, ROOT

    if not os.environ.get("CAMPUS_TEST_DIRECTORY"):
        (ROOT / ".scratch").mkdir(exist_ok=True)
        os.environ["CAMPUS_TEST_DIRECTORY"] = tempfile.mkdtemp(prefix="browser-", dir=ROOT / ".scratch")
    directory = fixture_directory()
    database = directory / "campus-test.db"
    fresh = not database.exists()
    marker = directory / "isolated-fixture.marker"
    if not fresh and (not marker.exists() or marker.read_text() != "CampusOS test fixture"):
        raise RuntimeError("Refusing an existing database without this fixture's ownership marker.")
    if fresh:
        marker.write_text("CampusOS test fixture", encoding="utf-8")
    os.environ["DATABASE_URL"] = "sqlite:///" + database.as_posix()
    os.environ["SUPABASE_URL"] = BASE
    os.environ["FRONTEND_ORIGIN"] = FRONTEND
    os.environ["APP_ENV"] = "development"
    os.environ["STORAGE_BACKEND"] = "local"
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["LLM_ENABLED"] = "false"
    os.environ["LLM_PROVIDER"] = "none"
    os.environ["EMBEDDING_MODEL_PATH"] = os.environ.get("CAMPUS_TEST_SEMANTIC_PATH", "")
    os.environ["GOOGLE_CLIENT_ID"] = ""
    os.environ["GOOGLE_CLIENT_SECRET"] = ""
    os.environ["SUPABASE_SERVICE_ROLE_KEY"] = ""
    # Import fixture builders only AFTER the disposable environment is pinned;
    # they transitively import database modules. Never bind the operator's URL.
    from backend.tests.test_prephase7_documents import workbook
    (directory / "synthetic-project.xlsx").write_bytes(workbook())
    from backend.app import storage

    storage.ROOT = directory
    from backend.app.db import SessionLocal, initialize_local, engine
    from backend.app.models import Membership, Source, IngestionJob, uid
    from backend.app.seed import seed, DEMO_TENANT
    from sqlalchemy import select

    assert Path(engine.url.database).resolve() == database
    if fresh:
        from alembic import command
        from alembic.config import Config
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    initialize_local()
    with SessionLocal() as db:
        if fresh:
            seed(db, ADMIN, anchor=datetime.now().date())
            for who in PROJECT_STUDENTS.values():
                db.add(Membership(tenant_id=DEMO_TENANT, user_id=who, role="student", department="AIML",
                    semester=3, section="A", name="Aarav", diet="vegetarian"))
            for user, role, section in ((SECTION_ADMIN,"section_admin","A"),
                (DEPARTMENT_ADMIN,"department_admin","A"),(CANTEEN_MANAGER,"canteen_manager","A"),(SECTION_B,"student","B")):
                db.add(Membership(tenant_id=DEMO_TENANT, user_id=user, role=role,
                    department="AIML", semester=3, section=section, name="Product fixture "+role))
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
                    student_number="1RV25CI077",
                )
            )
            db.add(Membership(tenant_id=DEMO_TENANT, user_id=REVIEWER, role="institution_admin",
                              department="AIML", semester=3, section="A", name="Independent reviewer"))
            # Real draft objects/jobs make a long file list for viewport/focus tests.
            import hashlib
            from backend.app.storage import ObjectStore
            from backend.app.schemas import ImportOptions
            objects=ObjectStore()
            for i in range(25):
                sid=uid()
                payload=f"Demo draft {i}; unpublished synthetic content.".encode()
                checksum=hashlib.sha256(payload).hexdigest()
                key=f"{DEMO_TENANT}/{sid}/{checksum}"
                objects.put(key,payload)
                db.add(Source(id=sid,tenant_id=DEMO_TENANT,kind="document",filename=f"Demo draft audit {i}.txt",
                    checksum=checksum,storage_key=key,uploaded_by=ADMIN,department="All",demo=True))
                db.flush()
                db.add(IngestionJob(tenant_id=DEMO_TENANT,source_id=sid,requested_by=ADMIN,department="All",
                    status="preview",options=ImportOptions(kind="document",demo=True).model_dump(mode="json"),
                    preview=dict(message="Synthetic draft awaiting confirmation.",format="txt",errors=[])))
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
    if fresh:
        # Two real PDFs use the same production processing and independent review
        # services. These synthetic fixtures never bypass publication controls.
        import fitz
        from backend.app.auth import resolve_actor
        from backend.app.services import CampusService
        from backend.app.ingestion import Ingestion
        from backend.app.publication import Publication
        from backend.app.schemas import ImportOptions

        with SessionLocal() as db:
            admin = CampusService(db, resolve_actor(db, ADMIN, DEMO_TENANT))
            for name, day in (("Alpha", "Friday"), ("Beta", "Tuesday")):
                pdf = fitz.open()
                pdf.new_page().insert_text((50, 50), f"Zirconium scholarship {name} eligibility handbook.")
                pdf.new_page().insert_text((50, 50), f"Zirconium scholarship deadline is {day}. Reference {name}.")
                ingestion = Ingestion(admin)
                job = ingestion.receive(name + ".pdf", pdf.tobytes(), ImportOptions(
                    kind="document", title="Selection Handbook " + name, demo=True,
                    topic="selection-demo", claim=day))
                pdf.close()
                ingestion.confirm(job)
                ingestion.process(job)
                job.status = "completed"
                db.flush()
                reviewer = CampusService(db, resolve_actor(db, REVIEWER, DEMO_TENANT))
                Publication(reviewer).transition(job.source_id, "approve")
                Publication(admin).transition(job.source_id, "publish")
            db.commit()
    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("CAMPUS_TEST_AUTH_PORT", "8766"))), TestIdentity)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    from backend.app.ingestion import work_once

    def worker():
        import time

        while True:
            if not work_once(SessionLocal):
                time.sleep(0.25)

    if os.environ.get("CAMPUS_TEST_BACKGROUND_WORKER", "1") == "1":
        threading.Thread(target=worker, daemon=True).start()
    import uvicorn

    uvicorn.run("backend.app.main:app", host="127.0.0.1",
                port=int(os.environ.get("CAMPUS_TEST_API_PORT", "8765")), log_level="warning", access_log=False)


if __name__ == "__main__":
    run()
