"""Synthetic Events dual-storage and RLS rehearsal; loopback disposable DB only."""

import os

import pytest
from sqlalchemy import create_engine, event, select, text, func
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from backend.app import retrieval
from backend.app.auth import resolve_actor
from backend.app.event_data import EventReviewRequest
from backend.app.event_pipeline import EventPipeline
from backend.app.ingestion import Ingestion, work_once
from backend.app.models import (
    Institution,
    Membership,
    Source,
    IngestionJob,
    Document,
    DocumentChunk,
    CampusRecord,
)
from backend.app.schemas import ImportOptions
from backend.app.services import CampusService
from backend.tests.conftest import MemoryStore
from backend.tests.test_events_phase2 import e5 as e5, TEXT


def test_events_real_e5_pgvector_rls_publish_retry_and_replacement(e5, monkeypatch):
    url = os.environ.get("TEST_EVENTS_POSTGRES_URL")
    if not url:
        pytest.skip("Requires the new disposable loopback Events PostgreSQL rehearsal database.")
    parsed = make_url(url)
    assert parsed.host == "127.0.0.1" and parsed.database == "campus_events_rehearsal"
    monkeypatch.setattr(retrieval, "semantic_model", lambda: e5)
    owner = create_engine(url)
    api = create_engine(url)

    @event.listens_for(api, "connect")
    def restricted(connection, _):
        connection.cursor().execute("SET ROLE campus_api")

    factory = sessionmaker(owner, expire_on_commit=False)
    api_factory = sessionmaker(api, expire_on_commit=False)
    tenant = "00000000-0000-4000-8000-000000003101"
    admin = "00000000-0000-4000-8000-000000003102"
    student = "00000000-0000-4000-8000-000000003103"
    outsider = "00000000-0000-4000-8000-000000003104"

    @event.listens_for(api_factory, "after_begin")
    def identity(session, transaction, connection):
        connection.execute(
            text("SELECT set_config('app.user_id',:user,true),set_config('app.tenant_id',:tenant,true)"),
            dict(user=session.info.get("user", admin), tenant=tenant),
        )

    with factory() as db:
        db.add(Institution(id=tenant, name="Disposable events test"))
        db.flush()
        db.add_all(
            [
                Membership(tenant_id=tenant, user_id=who, role=role, department=dept, semester=3, section="A")
                for who, role, dept in [
                    (admin, "institution_admin", "AIML"),
                    (student, "student", "AIML"),
                    (outsider, "student", "CSE"),
                ]
            ]
        )
        db.commit()
    store = MemoryStore()
    from backend.app import ingestion

    monkeypatch.setattr(ingestion, "ObjectStore", lambda: store)

    def queue(name, content, replacement=None):
        with api_factory() as db:
            c = CampusService(db, resolve_actor(db, admin, tenant))
            ingest = Ingestion(c, store)
            job = ingest.receive(
                name,
                content,
                ImportOptions(
                    kind="events",
                    event_workflow=True,
                    department="AIML",
                    demo=True,
                    replaces_source_id=replacement,
                ),
            )
            EventPipeline(ingest).save_review(
                job, EventReviewRequest(events=job.preview["event_rows"], pages=job.preview["event_pages"])
            )
            assert not job.preview["error_count"]
            ingest.confirm(job)
            db.commit()
            return job.id, job.source_id

    # Existing OCR caches are worker-owned. Re-extraction through the restricted
    # API must queue new work without trying to UPDATE staged_data itself.
    with factory() as db:
        c=CampusService(db,resolve_actor(db,admin,tenant))
        legacy=Ingestion(c,store).receive('legacy-ocr.txt',TEXT.encode(),ImportOptions(kind='events',department='AIML'))
        legacy.staged_data={'ocr_result':{'pages':[[1,TEXT]],'rows':[],'format':'txt','needs_review':False}}
        legacy_id,legacy_sid=legacy.id,legacy.source_id
        db.commit()
    with api_factory() as db:
        c=CampusService(db,resolve_actor(db,admin,tenant))
        ingest=Ingestion(c,store)
        legacy=ingest.job(legacy_id)
        legacy.options=ImportOptions.model_validate({**legacy.options,'event_workflow':True}).model_dump(mode='json')
        ingest.preview(legacy,reextract=True)
        db.commit()
    assert work_once(factory,store)
    with api_factory() as db:
        c=CampusService(db,resolve_actor(db,admin,tenant))
        ingest=Ingestion(c,store)
        legacy=ingest.job(legacy_id)
        body=EventReviewRequest(events=legacy.preview['event_rows'],pages=legacy.preview['event_pages'])
        EventPipeline(ingest).save_review(legacy,body,reviewed=False)
        assert not legacy.preview['event_reviewed']
        db.commit()
        EventPipeline(ingest).save_review(legacy,body)
        ingest.confirm(legacy)
        legacy.options={**legacy.options,'event_index_preview':True,'event_publish_requested':False}
        db.commit()
    assert work_once(factory,store)
    with factory() as db:
        assert db.get(Source,legacy_sid).publication_status=='draft'
        assert db.get(IngestionJob,legacy_id).preview['event_index_ready']
        assert db.scalar(select(func.count()).select_from(CampusRecord).where(CampusRecord.source_id==legacy_sid))==0
    jid, sid = queue("local-event.txt", TEXT.encode())
    assert work_once(factory, store)
    with factory() as db:
        job = db.get(IngestionJob, jid)
        assert job.status == "completed", job.error
        assert db.get(Source, sid).publication_status == "pending_review"
        assert db.scalar(select(func.count()).select_from(Document).where(Document.source_id == sid)) == 1
        assert db.scalar(select(func.count()).select_from(DocumentChunk)) > 0
    with api_factory(info={"user": student}) as db:
        # Raw SQL here exercises RLS without application filters.
        assert not db.scalars(select(Document)).all()
        assert not db.scalars(select(DocumentChunk)).all()
        assert not db.scalars(select(Source)).all()
    with api_factory() as db:
        from backend.app.publication import Publication

        c = CampusService(db, resolve_actor(db, admin, tenant))
        publication = Publication(c)
        publication.transition(sid, "approve")
        publication.transition(sid, "publish")
        db.commit()
    with api_factory(info={"user": student}) as db:
        c = CampusService(db, resolve_actor(db, student, tenant))
        listed = c.event_listing(query="Lantern")
        assert len(listed) == 1 and listed[0]["source_id"] == sid
        result = retrieval.Retrieval(c).search("Lantern Arts Fair rules", event_id=listed[0]["id"])
        assert result["hits"] and all(h["source_id"] == sid for h in result["hits"])
        assert db.scalar(select(DocumentChunk.embedding_model)).startswith("onnx-e5-")
        assert db.scalar(select(func.count()).select_from(DocumentChunk)) > 0
        assert "vector" in str(
            db.execute(
                text(
                    "SELECT format_type(atttypid,atttypmod) FROM pg_attribute WHERE attrelid='document_chunks'::regclass AND attname='embedding'"
                )
            ).scalar()
        )
    with api_factory(info={"user": outsider}) as db:
        assert not db.scalars(select(Document)).all()
        assert not db.scalars(select(DocumentChunk)).all()
        assert not db.scalars(select(CampusRecord)).all()
    # Replacement is staged without removing the old visible event/evidence.
    next_jid, next_sid = queue(
        "replacement.txt", TEXT.replace("Test Auditorium", "Reviewed Test Hall").encode(), sid
    )
    with api_factory(info={"user": student}) as db:
        assert db.scalar(select(Source.id)) == sid
    assert work_once(factory, store)
    with api_factory() as db:
        c = CampusService(db, resolve_actor(db, admin, tenant))
        publication = Publication(c)
        publication.transition(next_sid, "approve")
        publication.transition(next_sid, "publish")
        db.commit()
    with api_factory(info={"user": student}) as db:
        c = CampusService(db, resolve_actor(db, student, tenant))
        assert [r["source_id"] for r in c.event_listing()] == [next_sid]
        assert len(db.scalars(select(Document)).all()) == 1
        assert all(d.source_id == next_sid for d in db.scalars(select(Document)).all())
    # Revocation after confirmation prevents a later worker from publishing.
    bad_jid, bad_sid = queue(
        "revoked-admin.txt", TEXT.replace("Lantern Arts Fair", "Lantern Winter Fair").encode()
    )
    with factory() as db:
        db.scalar(select(Membership).where(Membership.user_id == admin)).active = False
        db.commit()
    assert work_once(factory, store)
    with factory() as db:
        assert db.get(IngestionJob, bad_jid).status == "failed"
        assert db.get(Source, bad_sid).publication_status == "draft"
        assert db.get(Source, next_sid).publication_status == "published"
    owner.dispose()
    api.dispose()
