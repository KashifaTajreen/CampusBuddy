import shutil
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.orm import sessionmaker
from backend.app.db import make_engine
from backend.app.models import Base, Membership
from backend.app.seed import seed, DEMO_TENANT
from backend.app.auth import resolve_actor
from backend.app.services import CampusService

USER = "00000000-0000-4000-8000-000000000002"
OTHER = "00000000-0000-4000-8000-000000000003"
TENANT_B = "00000000-0000-4000-8000-000000000004"
REVIEWER = "00000000-0000-4000-8000-000000000006"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


class MemoryStore:
    def __init__(self):
        self.items = {}

    def put(self, key, value):
        self.items[key] = value

    def get(self, key):
        return self.items[key]

    def delete(self, key):
        self.items.pop(key, None)


@pytest.fixture(autouse=True)
def offline_providers(monkeypatch):
    """Local credentials must never turn unit tests into live provider calls."""
    from backend.app import ai, retrieval

    from backend.app.config import Settings
    monkeypatch.setattr(ai, "settings", lambda: Settings(llm_enabled=False, llm_provider="none"))
    monkeypatch.setattr(retrieval, "semantic_model", lambda: None)


@pytest.fixture(scope="session")
def running_api(tmp_path_factory):
    # All API test modules share one ASGI/MCP session-manager lifetime.
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app import db
    # Bare pytest must never initialize or migrate the operator's .env database.
    previous = db.engine
    disposable = make_engine("sqlite:///"+str(tmp_path_factory.mktemp("asgi")/"api.db"))
    db.engine = disposable
    db.SessionLocal.configure(bind=disposable)
    try:
        with TestClient(app) as test:
            yield test
    finally:
        db.SessionLocal.configure(bind=previous)
        db.engine = previous
        disposable.dispose()


@pytest.fixture(scope="session")
def seeded(tmp_path_factory):
    path = tmp_path_factory.mktemp("baseline") / "base.db"
    engine = make_engine("sqlite:///" + str(path))
    Base.metadata.create_all(engine)
    store = MemoryStore()
    with sessionmaker(engine)() as db:
        seed(db, USER, anchor=NOW.date(), store=store)
        seed(db, OTHER, TENANT_B, anchor=NOW.date(), store=store)
        db.add(
            Membership(
                tenant_id=DEMO_TENANT,
                user_id=OTHER,
                role="student",
                department="CSE",
                semester=5,
                section="B",
                name="Diya",
            )
        )
        db.add(Membership(tenant_id=DEMO_TENANT, user_id=REVIEWER, role="institution_admin",
                          department="AIML", semester=3, section="A", name="Independent reviewer"))
        db.commit()
    engine.dispose()
    return path, store


@pytest.fixture
def env(seeded, tmp_path):
    path, store = seeded
    target = tmp_path / "test.db"
    shutil.copyfile(path, target)
    engine = make_engine("sqlite:///" + str(target))
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        a = resolve_actor(db, USER, DEMO_TENANT)
        c = CampusService(db, a, clock=lambda: NOW)
        yield SimpleNamespace(
            db=db,
            service=c,
            store=store,
            factory=factory,
            engine=engine,
            user=USER,
            tenant=DEMO_TENANT,
            other=OTHER,
            other_tenant=TENANT_B,
            reviewer=REVIEWER,
        )
    engine.dispose()


@pytest.fixture
def student(env):
    from dataclasses import replace

    return CampusService(env.db, replace(env.service.actor, role="student"), clock=lambda: NOW)


def publish(env, job):
    """Exercise real processing, independent approval and publication in tests."""
    from backend.app.ingestion import Ingestion
    from backend.app.publication import Publication

    if job.status != "completed":
        ingestion = Ingestion(env.service, env.store)
        ingestion.confirm(job)
        count = ingestion.process(job)
        job.status = "completed"
        job.preview = {**job.preview, "imported_count": count}
        env.db.flush()
    reviewer = CampusService(env.db, resolve_actor(env.db, REVIEWER, env.tenant), clock=lambda: NOW)
    Publication(reviewer).transition(job.source_id, "approve")
    Publication(env.service).transition(job.source_id, "publish")
