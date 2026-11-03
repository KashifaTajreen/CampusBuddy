import os
import shutil
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.orm import sessionmaker
os.environ["MCP_ALLOWED_HOSTS"] = '["127.0.0.1:*","localhost:*","testserver"]'  # keep a developer .env from changing test hosts
from backend.app.db import make_engine
from backend.app.models import Base, Membership
from backend.app.seed import seed, DEMO_TENANT
from backend.app.auth import resolve_actor
from backend.app.services import CampusService

USER = "00000000-0000-4000-8000-000000000002"
OTHER = "00000000-0000-4000-8000-000000000003"
TENANT_B = "00000000-0000-4000-8000-000000000004"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


class MemoryStore:
    def __init__(self):
        self.items = {}

    def put(self, key, value):
        self.items[key] = value

    def get(self, key):
        return self.items[key]


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
        )
    engine.dispose()


@pytest.fixture
def student(env):
    from dataclasses import replace

    return CampusService(env.db, replace(env.service.actor, role="student"), clock=lambda: NOW)
