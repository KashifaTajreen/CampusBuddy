"""Runs only against the Redis instance owned/marked by scripts.verify_redis."""
import json
import os
from dataclasses import replace
import pytest
from sqlalchemy import select
from backend.app import cache
from backend.app.config import Settings
from backend.app.models import CampusRecord, Source
from backend.app.services import CampusService
from backend.app.publication import Publication


@pytest.fixture
def store(monkeypatch):
    import redis
    url = os.getenv("TEST_REDIS_URL")
    token = os.getenv("CAMPUS_REDIS_TEST_TOKEN")
    if not url or not token:
        pytest.skip("Disposable Redis URL/ownership marker not configured; use scripts.verify_redis.")
    result = redis.Redis.from_url(url, socket_timeout=1)
    assert result.get("campus-verification-owner") == token.encode(), "Refusing an unowned Redis database"
    monkeypatch.setattr(cache, "settings", lambda: Settings(_env_file=None, redis_enabled=True, redis_url=url))
    class SafeClient:
        def __getattr__(self, name):
            return getattr(result, name)
        def __repr__(self):
            return "DisposableRedisClient(credentials redacted)"
    return SafeClient()


def keys(store):
    return {k for k in store.scan_iter("campus:v1:read:*") if not k.endswith(b":fill")}


def test_real_cache_populates_and_reauthorizes_poisoned_payload(env, store):
    before = keys(store)
    first = env.service.menu(100, True)
    new = keys(store)-before
    assert first and len(new) == 1
    key = next(iter(new))
    foreign = env.db.scalar(select(CampusRecord).where(CampusRecord.tenant_id == env.other_tenant, CampusRecord.kind == "menu"))
    store.set(key, json.dumps([{**foreign.payload, "id": foreign.id, "source_id": foreign.source_id}]))
    second = env.service.menu(100, True)
    assert second == first and foreign.id not in str(second)
    assert 0 < store.ttl(key) <= 30


def test_tenant_user_role_department_keys_are_separate(env, store):
    first = env.service.menu()
    before = keys(store)
    other = CampusService(env.db, replace(env.service.actor, user_id=env.other, role="student", department="CSE"), clock=env.service.clock)
    other_rows = other.menu()
    assert [(r["id"], r["price"]) for r in other_rows] == [(r["id"], r["price"]) for r in first]
    assert all(not r["original_download_allowed"] for r in other_rows)
    assert keys(store)-before
    foreign = CampusService(env.db, replace(env.service.actor, tenant_id=env.other_tenant), clock=env.service.clock)
    assert {r["source_id"] for r in foreign.menu()}.isdisjoint({r["source_id"] for r in first})


def test_revocation_invalidates_cached_source_and_notices(env, store):
    rows = env.service.menu()
    notices = env.service.notices()
    assert rows and notices
    source = env.db.get(Source, rows[0]["source_id"])
    Publication(env.service).transition(source.id, "revoke", "Cache revocation test")
    env.db.commit()
    assert source.id not in {r["source_id"] for r in env.service.menu()}
    document_source = notices[0]["source_id"]
    from backend.app.files import Files
    Files(env.service, env.store).change(document_source, "delete")
    env.db.commit()
    assert document_source not in {r["source_id"] for r in env.service.notices()}


def test_redis_outage_falls_back_without_private_cache(env, monkeypatch):
    class Broken:
        def get(self, *_):
            raise OSError("test outage")
    monkeypatch.setattr(cache, "client", lambda: Broken())
    assert env.service.menu()
    assert cache.SharedCache(env.service, "emails", env.service.scope(CampusRecord, "emails")).key is None


def test_rollback_does_not_publish_cache_epoch(env, store):
    key = "campus:v1:revision:"+env.tenant
    before = store.get(key)
    cache.touch(env.db, env.tenant)
    env.db.rollback()
    assert store.get(key) == before


def test_shared_read_cache_reused_for_equal_authorization_views(env, store):
    env.service.menu()
    before = keys(store)
    same_scope = CampusService(env.db, replace(env.service.actor, user_id=env.reviewer), clock=env.service.clock)
    assert same_scope.menu() == env.service.menu()
    assert keys(store) == before


def test_section_cache_and_role_change_invalidation(env, store):
    from backend.tests.test_product_workflows import member, finish
    from backend.app.ingestion import Ingestion
    from backend.app.schemas import ImportOptions
    from backend.app.models import Membership
    a = member(env, "student", section="A")
    b = member(env, "student", section="B")
    job = Ingestion(env.service, env.store).receive("cache-section.txt", b"Section A confidential briefing is on Friday.",
        ImportOptions(kind="document", department="AIML", semester=3, section="A", title="Cache scope notice",
                      published=env.service.local_now().date()))
    finish(env, env.service, job)
    assert job.source_id in {r["source_id"] for r in a.notices()}
    assert job.source_id not in {r["source_id"] for r in b.notices()}
    revision = store.get("campus:v1:revision:"+env.tenant)
    membership = env.db.scalar(select(Membership).where(Membership.user_id == a.actor.user_id))
    membership.section = "B"
    env.db.commit()
    assert store.get("campus:v1:revision:"+env.tenant) != revision
    changed = CampusService(env.db, replace(a.actor, section="B"), clock=env.service.clock)
    assert job.source_id not in {r["source_id"] for r in changed.notices()}


def test_shared_menu_cache_recomputes_uploader_download_permission(env, store):
    from backend.tests.test_product_workflows import member, finish
    from backend.app.ingestion import Ingestion
    from backend.app.schemas import ImportOptions
    first, second = member(env,"canteen_manager"), member(env,"canteen_manager")
    job = Ingestion(first,env.store).receive("manager-menu.json", json.dumps([
        dict(name="Uploader meal",price=45,vegetarian=True,category="Lunch",location="Campus Canteen")
    ]).encode(), ImportOptions(kind="menu"))
    finish(env,first,job)
    row = next(r for r in first.menu() if r["source_id"] == job.source_id)
    assert row["original_download_allowed"]
    before = keys(store)
    other_row = next(r for r in second.menu() if r["source_id"] == job.source_id)
    assert not other_row["original_download_allowed"]
    assert keys(store) == before  # Same public view, identity-specific capability.


def test_published_price_replacement_invalidates_cached_student_answer(env, store):
    from backend.tests.test_product_workflows import member, finish
    from backend.app.ingestion import Ingestion
    from backend.app.schemas import ImportOptions
    from backend.app.router import Router
    manager, student = member(env,"canteen_manager"), member(env,"student")
    row = dict(name="Cached product meal", price=40, vegetarian=True, category="Lunch", location="Campus Canteen")
    pipeline = Ingestion(manager,env.store)
    first = Ingestion(env.service,env.store).receive("price-v1.json",json.dumps([row]).encode(),ImportOptions(kind="menu"))
    finish(env,env.service,first)
    answer = Router(student).ask("What can I eat for under 100 rupees?")
    assert next(r for r in answer["records"] if r["name"]==row["name"])["price"] == 40
    env.db.commit()
    revision = store.get("campus:v1:revision:"+env.tenant)
    second = pipeline.receive("price-v2.json",json.dumps([{**row,"price":75}]).encode(),
        ImportOptions(kind="menu",replaces_source_id=first.source_id))
    finish(env,manager,second)
    assert store.get("campus:v1:revision:"+env.tenant) != revision
    answer = Router(student).ask("What can I eat for under 100 rupees?")
    assert next(r for r in answer["records"] if r["name"]==row["name"])["price"] == 75
