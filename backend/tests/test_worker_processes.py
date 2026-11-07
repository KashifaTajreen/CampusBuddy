"""Real API/worker subprocesses with synthetic identities and disposable data."""

from contextlib import contextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import pytest
from sqlalchemy import create_engine, select, update, func
from sqlalchemy.orm import Session

from backend.app.auth import resolve_actor
from backend.app.ingestion import Ingestion
from backend.app.models import Base, Document, DocumentChunk, IngestionJob, Institution, Membership, Source, utcnow
from backend.app.schemas import ImportOptions
from backend.app.services import CampusService
from backend.app import storage
from backend.tests.conftest import USER
from backend.tests.process_support import ROOT

WORKER_TENANT = "00000000-0000-4000-8000-000000000010"


def wait_for(check, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("Disposable process verification timed out.")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Processes:
    def __init__(self, database_url=None, directory=None):
        (ROOT / ".scratch").mkdir(exist_ok=True)
        self.directory = directory or Path(tempfile.mkdtemp(prefix="process-test-", dir=ROOT / ".scratch"))
        self.database_url = database_url or "sqlite:///" + (self.directory / "campus-test.db").as_posix()
        self.children = []
        self.logs = []
        self.env = dict(os.environ, DATABASE_URL=self.database_url, APP_ENV="development",
                        CAMPUS_TEST_DIRECTORY=str(self.directory), CAMPUS_TEST_BACKGROUND_WORKER="0",
                        STORAGE_BACKEND="local", SUPABASE_SERVICE_ROLE_KEY="", GEMINI_API_KEY="",
                        GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="", EMBEDDING_MODEL_PATH="")

    def start(self, module, args=(), **env):
        log = (self.directory / f"process-{len(self.logs)}.log").open("w", encoding="utf-8")
        self.logs.append(log)
        process = subprocess.Popen([sys.executable, "-m", module, *args], cwd=ROOT,
                                   env=dict(self.env, **env), stdout=log, stderr=subprocess.STDOUT)
        self.children.append(process)
        return process

    def worker(self, once=True, block=False):
        return self.start("backend.tests.process_support", ["--once"] if once else [],
                          CAMPUS_TEST_BLOCK_READ="1" if block else "0")

    def finish(self, process):
        assert process.wait(timeout=45) == 0, "Worker exited unsuccessfully; inspect its isolated process log."

    def stop(self, process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)

    def close(self):
        for child in reversed(self.children):
            self.stop(child)
        for log in self.logs:
            log.close()


@pytest.fixture
def api_process():
    group = Processes()
    api_port, auth_port = free_port(), free_port()
    group.env.update(CAMPUS_TEST_API_PORT=str(api_port), CAMPUS_TEST_AUTH_PORT=str(auth_port))
    group.api_url = f"http://127.0.0.1:{api_port}"
    group.auth_url = f"http://127.0.0.1:{auth_port}"
    group.client = httpx.Client(timeout=10)

    def start_api():
        process = group.start("backend.tests.e2e_server")

        def ready():
            assert process.poll() is None, "API fixture exited; inspect its isolated process log."
            try:
                return group.client.get(group.api_url + "/health").status_code == 200
            except httpx.TransportError:
                return False

        # This API fixture migrates, seeds and parses two real PDFs before binding
        # its socket. Match the demo's bounded startup allowance; keep the worker
        # processing/restart assertions and their independent deadlines unchanged.
        wait_for(ready, timeout=90)
        return process

    group.start_api = start_api
    try:
        group.api = start_api()
        yield group
    finally:
        group.client.close()
        group.close()


def login(group, email="admin@example.invalid"):
    response = group.client.post(group.auth_url + "/auth/v1/token?grant_type=password",
                                 json={"email": email, "password": "CampusTest123!"})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["access_token"],
            "X-Tenant-ID": "00000000-0000-4000-8000-000000000001"}


def queue_via_api(group):
    headers = login(group)
    response = group.client.post(group.api_url + "/v1/ingestion/preview", headers=headers,
        data={"options": json.dumps({"kind": "document", "title": "Process verification"})},
        files={"file": ("process.txt", b"Synthetic process verification document.", "text/plain")})
    assert response.status_code == 200
    job = response.json()
    response = group.client.post(group.api_url + f"/v1/ingestion/{job['id']}/confirm", headers=headers)
    assert response.status_code == 200 and response.json()["status"] == "queued"
    return job


def test_api_restart_preserves_queued_job_and_separate_worker_publication(api_process):
    group = api_process
    job = queue_via_api(group)
    group.stop(group.api)
    group.api = group.start_api()
    headers = login(group)
    jobs = group.client.get(group.api_url + "/v1/ingestion", headers=headers).json()
    assert next(j for j in jobs if j["id"] == job["id"])["status"] == "queued"
    worker = group.worker()
    assert worker.pid != group.api.pid
    group.finish(worker)
    sid = job["source_id"]
    review = group.client.get(group.api_url + f"/v1/admin/sources/{sid}", headers=headers).json()
    assert review["job_status"] == "completed" and review["status"] == "pending_review"
    student_headers = login(group, "student@example.invalid")
    assert group.client.get(group.api_url + f"/v1/sources/{sid}", headers=student_headers).status_code in (403, 404)
    reviewer = login(group, "reviewer@example.invalid")
    assert group.client.post(group.api_url + f"/v1/admin/sources/{sid}/approve", headers=reviewer,
                              json={}).status_code == 200
    assert group.client.post(group.api_url + f"/v1/admin/sources/{sid}/publish", headers=headers,
                              json={}).status_code == 200
    downloaded = group.client.get(group.api_url + f"/v1/sources/{sid}", headers=student_headers)
    assert downloaded.status_code == 200 and downloaded.content == b"Synthetic process verification document."
    assert group.client.get(group.api_url + f"/v1/sources/{sid}").status_code == 401


@contextmanager
def queued_documents(engine, user, tenant, monkeypatch, count=1):
    directory = Path(engine.url.database).parent if engine.dialect.name == "sqlite" else None
    group = Processes(engine.url.render_as_string(hide_password=False), directory=directory)
    monkeypatch.setattr(storage, "ROOT", group.directory)
    store = storage.ObjectStore(type("LocalConfig", (), {"storage_backend": "local"})())
    ids = []
    with Session(engine) as db:
        service = CampusService(db, resolve_actor(db, user, tenant))
        ingestion = Ingestion(service, store)
        for i in range(count):
            job = ingestion.receive("worker.txt", f"Synthetic worker document {group.directory.name} {i}".encode(),
                                    ImportOptions(kind="document"))
            ingestion.confirm(job)
            ids.append(job.id)
        db.commit()
    try:
        yield group, ids
    finally:
        group.close()


def exercise_concurrent_workers(engine, user, tenant, monkeypatch):
    with queued_documents(engine, user, tenant, monkeypatch, count=4) as (group, ids):
        first, second = group.worker(once=False), group.worker(once=False)
        assert first.pid != second.pid

        def completed():
            with Session(engine) as db:
                jobs = db.scalars(select(IngestionJob).where(IngestionJob.id.in_(ids))).all()
                assert not any(j.status == "failed" for j in jobs)
                return all(j.status == "completed" for j in jobs)

        wait_for(completed)
        group.stop(first)
        group.stop(second)
        with Session(engine) as db:
            jobs = db.scalars(select(IngestionJob).where(IngestionJob.id.in_(ids))).all()
            assert all(j.attempts == 1 for j in jobs)
            sources = [j.source_id for j in jobs]
            docs = db.scalars(select(Document).where(Document.source_id.in_(sources))).all()
            assert len(docs) == 4 and len({d.source_id for d in docs}) == 4
            assert db.scalar(select(func.count()).select_from(DocumentChunk).where(
                DocumentChunk.document_id.in_([d.id for d in docs]))) == 4
            assert all(db.get(Source, sid).publication_status == "pending_review" for sid in sources)


def exercise_crashed_worker_recovery(engine, user, tenant, monkeypatch):
    with queued_documents(engine, user, tenant, monkeypatch) as (group, ids):
        job_id = ids[0]
        blocked = group.worker(block=True)
        wait_for(lambda: (group.directory / "worker-reading.marker").exists())
        group.stop(blocked)  # Kill only this owned test process during its uncommitted read.
        with Session(engine) as db:
            job = db.get(IngestionJob, job_id)
            assert job.status == "processing" and job.attempts == 1
            assert not db.scalar(select(Document.id).where(Document.source_id == job.source_id))
        group.finish(group.worker())  # A fresh claim must not be stolen before lease expiry.
        with Session(engine) as db:
            assert db.get(IngestionJob, job_id).attempts == 1
            db.execute(update(IngestionJob).where(IngestionJob.id == job_id).values(
                claimed_at=utcnow() - timedelta(minutes=11)))
            db.commit()
        group.finish(group.worker())
        with Session(engine) as db:
            job = db.get(IngestionJob, job_id)
            assert job.status == "completed" and job.attempts == 2
            assert len(db.scalars(select(Document).where(Document.source_id == job.source_id)).all()) == 1
            assert db.get(Source, job.source_id).publication_status == "pending_review"


@pytest.fixture
def sqlite_worker_engine():
    group = Processes()
    engine = create_engine(group.database_url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Institution(id=WORKER_TENANT, name="Synthetic worker fixture", demo=True))
        db.flush()
        db.add(Membership(tenant_id=WORKER_TENANT, user_id=USER, role="institution_admin"))
        db.commit()
    try:
        yield engine
    finally:
        engine.dispose()
        group.close()


def test_concurrent_sqlite_worker_processes_do_not_duplicate_jobs(sqlite_worker_engine, monkeypatch):
    exercise_concurrent_workers(sqlite_worker_engine, USER, WORKER_TENANT, monkeypatch)


def test_killed_sqlite_worker_recovers_after_claim_lease(sqlite_worker_engine, monkeypatch):
    exercise_crashed_worker_recovery(sqlite_worker_engine, USER, WORKER_TENANT, monkeypatch)
