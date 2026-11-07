"""Optional Redis acceleration. Database scope remains authoritative on every hit."""
from functools import lru_cache
import hashlib
import json
import uuid
from sqlalchemy import event
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.models import Source, CampusRecord, Document

KINDS = {"menu", "faculty", "events", "calendar", "timetable", "exceptions", "notices"}


@lru_cache(maxsize=4)
def connection(url):
    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry
    return redis.Redis.from_url(url, socket_connect_timeout=0.15, socket_timeout=0.15,
        max_connections=12, retry=Retry(NoBackoff(), 0), health_check_interval=30)


def client():
    config = settings()
    return connection(config.redis_url) if config.redis_enabled and config.redis_url else None


def touch(session, tenant):
    session.info.setdefault("cache_tenants", set()).add(tenant)


@event.listens_for(Session, "before_flush")
def record_changes(session, *_):
    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if getattr(obj, "__tablename__", "") in {"sources", "campus_records", "documents", "document_chunks", "memberships", "ingestion_jobs"}:
            if getattr(obj, "tenant_id", None):
                touch(session, obj.tenant_id)


@event.listens_for(Session, "after_commit")
def invalidate_committed(session):
    if session.in_nested_transaction():
        return
    tenants = session.info.pop("cache_tenants", set())
    if tenants:
        try:
            store = client()
            if store:
                for tenant in tenants:
                    store.incr("campus:v1:revision:" + tenant)
        except Exception:
            pass  # A fresh database fingerprint still prevents stale-source access.


@event.listens_for(Session, "after_rollback")
def discard_pending(session):
    if session.in_nested_transaction():
        return
    session.info.pop("cache_tenants", None)


class SharedCache:
    def __init__(self, service, kind, statement):
        self.c, self.kind, self.key, self.token, self.store = service, kind, None, None, None
        self.statement = statement
        if kind not in KINDS or service.db.new or service.db.dirty or service.db.info.get("cache_tenants"):
            return
        try:
            self.store = client()
            if self.store is None:
                return
            model = Document if kind == "notices" else CampusRecord
            if service.db.scalar(statement.where(model.owner_id.is_not(None)).with_only_columns(model.id).limit(1)):
                return  # Private metadata must not populate or truncate a shared result.
            sources = list(service.db.execute(service.scope(Source).where(Source.owner_id.is_(None),
                Source.kind == ("document" if kind == "notices" else kind)).with_only_columns(
                Source.id, Source.owner_id, Source.department, Source.semester, Source.section,
                Source.checksum, Source.published_at, Source.student_records).order_by(Source.id).limit(1001)))
            if len(sources) > 1000:
                return
            a = service.actor
            compiled = statement.compile()
            personalized = kind in {"timetable", "exceptions"}
            parameters = {k: ("shared-actor" if not personalized and v == a.user_id else v) for k, v in compiled.params.items()}
            revision = self.store.get("campus:v1:revision:" + a.tenant_id) or b"0"
            material = dict(tenant=a.tenant_id, user=a.user_id if personalized else None, role=a.role, department=a.department,
                semester=a.semester, section=a.section, date=str(service.local_now().date()),
                sources=[tuple(row) for row in sources], sql=str(compiled), parameters=parameters,
                revision=revision.decode())
            self.key = "campus:v1:read:" + hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()
        except Exception:
            self.key = None

    def read(self):
        if not self.key:
            return None
        try:
            value = self.store.get(self.key)
            if value and len(value) < 1_000_000:
                rows = json.loads(value)
                if not isinstance(rows, list) or len(rows) > 1000:
                    return None
                # Recheck row-level scope, parent publication and ownership before reuse.
                ids = [row["id"] for row in rows]
                model = Document if self.kind == "notices" else CampusRecord
                allowed = {r.id: r.source_id for r in self.c.db.execute(self.statement
                    .where(model.id.in_(ids), model.owner_id.is_(None))
                    .with_only_columns(model.id, model.source_id))}
                if all(allowed.get(r["id"]) == r["source_id"] for r in rows):
                    return rows
            self.token = uuid.uuid4().hex
            if not self.store.set(self.key + ":fill", self.token, nx=True, ex=5):
                self.token = None  # No waiting: other callers fall back to the database.
        except Exception:
            pass
        return None

    def write(self, rows, private=False):
        if not self.key or not self.token or private:
            return
        try:
            payload = json.dumps(rows)
            if len(payload.encode()) < 1_000_000:
                self.store.set(self.key, payload, ex=settings().cache_ttl_seconds)
            self.store.eval("if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end",
                            1, self.key + ":fill", self.token)
        except Exception:
            pass
