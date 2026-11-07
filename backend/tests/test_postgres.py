"""Run against a disposable pgvector database; never use an institutional database."""

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import DBAPIError
from backend.app.seed import seed, DEMO_TENANT
from backend.app.auth import resolve_actor
from backend.app.services import CampusService
from backend.app.retrieval import Retrieval
from backend.tests.conftest import MemoryStore, USER, OTHER, TENANT_B
from backend.tests.conftest import REVIEWER
from backend.app.models import Membership, Source, Document
from backend.app.ingestion import Ingestion
from backend.app.publication import Publication
from backend.app.schemas import ImportOptions
from sqlalchemy import select
import json

pytestmark = pytest.mark.postgres


def test_rls_student_document_rows_and_verified_identifier_updates(postgres):
    import io
    import zipfile
    from backend.tests.test_prephase7_documents import workbook
    from backend.app.models import uid
    user,sid=uid(),None
    payload=io.BytesIO(workbook())
    with zipfile.ZipFile(payload,"a") as archive:
        archive.writestr("fixture-reference.txt",uid())
    store=MemoryStore()
    try:
        with sessionmaker(postgres)() as db:
            db.add(Membership(tenant_id=DEMO_TENANT,user_id=user,role="student",department="AIML",semester=3,
                section="A",name="Synthetic row reader",student_number="1RV25CI077"))
            db.commit()
            admin=CampusService(db,resolve_actor(db,USER,DEMO_TENANT))
            pipe=Ingestion(admin,store)
            job=pipe.receive("private-rows-"+uid()+".xlsx",payload.getvalue(),ImportOptions(kind="document"))
            pipe.confirm(job)
            pipe.process(job)
            job.status="completed"
            db.commit()
            sid=job.source_id
            doc=db.scalar(select(Document.id).where(Document.source_id==sid))
        with postgres.begin() as conn:
            scoped(conn)
            conn.execute(text("SELECT set_config('app.user_id',:u,true)"),{"u":user})
            assert conn.scalar(text("SELECT count(*) FROM document_chunks WHERE document_id=:d"),{"d":doc})==0
        with sessionmaker(postgres)() as db:
            reviewer=CampusService(db,resolve_actor(db,REVIEWER,DEMO_TENANT))
            Publication(reviewer).transition(sid,"approve")
            Publication(reviewer).transition(sid,"publish")
            db.commit()
        with postgres.begin() as conn:
            scoped(conn)
            conn.execute(text("SELECT set_config('app.user_id',:u,true)"),{"u":user})
            rows=list(conn.scalars(text("SELECT text FROM document_chunks WHERE document_id=:d"),{"d":doc}))
            assert len(rows)==2 and all("Other Private Mentor" not in row for row in rows)
            with pytest.raises(DBAPIError):
                with conn.begin_nested():
                    conn.execute(text("UPDATE memberships SET student_number='1RV25CI088' WHERE user_id=:u"),{"u":user})
            with pytest.raises(DBAPIError):
                with conn.begin_nested():
                    conn.execute(text("SELECT campus_set_student_number(:t,:u,'1RV25CI088')"),{"t":DEMO_TENANT,"u":user})
    finally:
        with postgres.begin() as conn:
            conn.execute(text("UPDATE memberships SET student_number=NULL WHERE tenant_id=:t AND user_id=:u"),{"t":DEMO_TENANT,"u":user})


def test_postgres_reads_fall_back_safely_when_redis_is_unavailable(postgres, monkeypatch):
    from backend.app import cache
    from sqlalchemy.orm import Session
    class Unavailable:
        def get(self, *_args):
            raise OSError("Synthetic Redis outage")
    monkeypatch.setattr(cache,"client",lambda:Unavailable())
    with postgres.begin() as conn:
        scoped(conn)
        with Session(conn) as db:
            service = CampusService(db,resolve_actor(db,OTHER,DEMO_TENANT))
            assert service.menu(query="Tea")


def test_real_pgvector_multilingual_selected_document_uses_only_compatible_model(postgres, monkeypatch):
    from pathlib import Path
    from backend.app import retrieval
    from backend.app.semantic import LocalE5
    from backend.app.config import Settings
    from backend.app.models import DocumentChunk, uid
    from sqlalchemy.orm import Session
    directory = Path(__file__).resolve().parents[2]/".scratch/semantic-e5"
    if not (directory/"manifest.json").exists():
        pytest.skip("Approved local E5 assets are not installed; run scripts.setup_semantic after approval.")
    model = LocalE5(directory)
    monkeypatch.setattr(retrieval, "semantic_model", lambda:model)
    monkeypatch.setattr(retrieval, "settings", lambda:Settings(_env_file=None, semantic_min_score=.78))
    with Session(postgres) as db:
        admin = CampusService(db, resolve_actor(db, USER, DEMO_TENANT))
        pipeline = Ingestion(admin, MemoryStore())
        job = pipeline.receive("semantic-"+uid()+".txt",
            ("The campus medical room provides first aid near the main administrative building. Reference "+uid()).encode(),
            ImportOptions(kind="document", title="Semantic first aid"))
        pipeline.confirm(job)
        pipeline.process(job)
        job.status = "completed"
        db.flush()
        reviewer = CampusService(db, resolve_actor(db, REVIEWER, DEMO_TENANT))
        Publication(reviewer).transition(job.source_id, "approve")
        Publication(admin).transition(job.source_id, "publish")
        document_id = db.scalar(select(Document.id).where(Document.source_id == job.source_id))
        db.commit()
    with postgres.begin() as conn:
        scoped(conn)
        with Session(conn) as db:
            student = CampusService(db, resolve_actor(db, OTHER, DEMO_TENANT))
            result = Retrieval(student).search("प्राथमिक उपचार कहाँ मिलेगा?", document_id=document_id)
            assert result["hits"] and {h["document_id"] for h in result["hits"]} == {document_id}
            assert result["mode"] == model.identity
    with Session(postgres) as db:
        for chunk in db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document_id)):
            chunk.embedding_model = "incompatible-model"
        db.commit()
    with postgres.begin() as conn:
        scoped(conn)
        with Session(conn) as db:
            student = CampusService(db, resolve_actor(db, OTHER, DEMO_TENANT))
            result = Retrieval(student).search("प्राथमिक उपचार कहाँ मिलेगा?", document_id=document_id)
            assert result["missing"] and not result["hits"]


def test_rls_section_source_insertion_and_cross_section_denial(postgres):
    from uuid import uuid4
    from sqlalchemy.orm import Session
    from backend.app.models import IngestionJob
    from backend.app.ingestion import work_once
    user, student_a, student_b, source_id = [str(uuid4()) for _ in range(4)]
    store = MemoryStore()
    with Session(postgres) as db:
        for who, role, section in ((user,"section_admin","A"),(student_a,"student","A"),(student_b,"student","B")):
            db.add(Membership(tenant_id=DEMO_TENANT, user_id=who, role=role, department="AIML", semester=3, section=section))
        db.commit()
    with postgres.begin() as conn:
        scoped(conn)
        with Session(conn) as db:
            service = CampusService(db, resolve_actor(db, user, DEMO_TENANT))
            pipeline = Ingestion(service, store)
            job = pipeline.receive("section-policy.txt", b"A scoped section policy starts at 13:30 on Friday. "+user.encode(),
                ImportOptions(kind="document", department="AIML", semester=3, section="A"))
            source_id, job_id = job.source_id, job.id
            pipeline.confirm(job)
            db.flush()
    with postgres.begin() as conn:
        scoped(conn)
        conn.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u":user})
        with pytest.raises(DBAPIError):
            with conn.begin_nested():
                conn.execute(text("""INSERT INTO sources(id,tenant_id,filename,kind,checksum,storage_key,uploaded_by,
                  department,semester,section,created_at,demo) VALUES(:id,:t,'forged.txt','document',:id,'fixture',:u,'AIML',3,'B',now(),false)"""),
                  {"id":str(uuid4()),"t":DEMO_TENANT,"u":user})
    assert work_once(sessionmaker(postgres, expire_on_commit=False), store)
    with Session(postgres) as db:
        assert db.get(IngestionJob, job_id).status == "completed"
        reviewer = CampusService(db, resolve_actor(db, REVIEWER, DEMO_TENANT))
        Publication(reviewer).transition(source_id, "approve")
        admin = CampusService(db, resolve_actor(db, USER, DEMO_TENANT))
        Publication(admin).transition(source_id, "publish")
        db.commit()
    with postgres.begin() as conn:
        scoped(conn)
        for who, expected in ((student_a,1),(student_b,0)):
            conn.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u":who})
            assert conn.scalar(text("SELECT count(*) FROM sources WHERE id=:s"), {"s":source_id}) == expected
            assert conn.scalar(text("SELECT count(*) FROM documents WHERE source_id=:s"), {"s":source_id}) == expected


def test_rls_canteen_manager_cannot_forge_official_documents(postgres):
    from uuid import uuid4
    from sqlalchemy.orm import Session
    user = str(uuid4())
    with Session(postgres) as db:
        db.add(Membership(tenant_id=DEMO_TENANT, user_id=user, role="canteen_manager", department="AIML"))
        db.commit()
    with postgres.begin() as conn:
        scoped(conn)
        conn.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u":user})
        with pytest.raises(DBAPIError):
            with conn.begin_nested():
                conn.execute(text("""INSERT INTO sources(id,tenant_id,filename,kind,checksum,storage_key,uploaded_by,
                    department,created_at,demo) VALUES(:id,:t,'forged.txt','document',:id,'fixture',:u,'All',now(),false)"""),
                    {"id":str(uuid4()),"t":DEMO_TENANT,"u":user})
        with pytest.raises(DBAPIError):
            with conn.begin_nested():
                conn.execute(text("SELECT campus_upsert_membership(:t,:u,'institution_admin','AIML',3,'A','Forged')"),
                    {"t":DEMO_TENANT,"u":str(uuid4())})


@pytest.fixture(scope="module")
def postgres():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip(
            "TEST_POSTGRES_URL is not configured; PostgreSQL/RLS needs a disposable pgvector database."
        )
    engine = create_engine(url)
    with sessionmaker(engine)() as db:
        store = MemoryStore()
        seed(db, USER, anchor=date(2026, 10, 28), store=store)
        seed(db, OTHER, TENANT_B, anchor=date(2026, 10, 28), store=store)
        for user, role in ((OTHER, "student"), (REVIEWER, "institution_admin")):
            if not db.scalar(select(Membership).where(Membership.user_id == user, Membership.tenant_id == DEMO_TENANT)):
                db.add(Membership(user_id=user, tenant_id=DEMO_TENANT, role=role, department="AIML",
                                  semester=3, section="A", name="Publication test participant"))
        db.commit()
    yield engine
    engine.dispose()


def scoped(connection):
    connection.execute(text("SET LOCAL ROLE campus_api"))
    connection.execute(
        text("SELECT set_config('app.user_id',:u,true),set_config('app.tenant_id',:t,true)"),
        dict(u=USER, t=DEMO_TENANT),
    )


def test_rls_denies_cross_tenant(postgres):
    with postgres.begin() as conn:
        scoped(conn)
        assert (
            conn.scalar(text("SELECT count(*) FROM campus_records WHERE tenant_id=:t"), dict(t=DEMO_TENANT))
            > 0
        )
        assert (
            conn.scalar(text("SELECT count(*) FROM campus_records WHERE tenant_id=:t"), dict(t=TENANT_B)) == 0
        )


def test_rls_file_delete_restore_and_student_denial(postgres):
    sid, _, doc_id = prepared_source(postgres)
    with postgres.begin() as conn:
        scoped(conn)
        with sessionmaker(bind=conn)() as db:
            approve_publish(db, sid)
        conn.execute(text("SELECT campus_file_action(:s,'delete','','')"), {"s": sid})
        assert conn.scalar(text("SELECT count(*) FROM document_chunks WHERE document_id=:d"), {"d": doc_id}) == 0
        conn.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u": OTHER})
        assert conn.scalar(text("SELECT count(*) FROM sources WHERE id=:s"), {"s": sid}) == 0
        with pytest.raises(DBAPIError):
            with conn.begin_nested():
                conn.execute(text("SELECT campus_file_action(:s,'restore','','')"), {"s": sid})
        conn.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u": USER})
        conn.execute(text("SELECT campus_file_action(:s,'restore','','')"), {"s": sid})
        assert conn.execute(text("SELECT file_state,publication_status,approved_by FROM sources WHERE id=:s"), {"s": sid}).one() == ("active", "draft", None)


def test_rls_private_file_deletion_requires_owner(postgres):
    sid, _, _ = prepared_source(postgres, private=True)
    with postgres.begin() as conn:
        scoped(conn)
        with pytest.raises(DBAPIError):
            with conn.begin_nested():
                conn.execute(text("SELECT campus_file_action(:s,'delete','','')"), {"s": sid})
        conn.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u": OTHER})
        conn.execute(text("SELECT campus_file_action(:s,'delete','','')"), {"s": sid})
        with pytest.raises(DBAPIError):
            with conn.begin_nested():
                conn.execute(text("SELECT campus_file_action(:s,'begin_purge','yes','')"), {"s": sid})
        conn.execute(text("SELECT campus_file_action(:s,'begin_purge',:confirm,'')"), {"s": sid, "confirm": "DELETE "+sid})
        assert conn.scalar(text("SELECT file_state FROM sources WHERE id=:s"), {"s": sid}) == "purge_pending"
        conn.execute(text("SELECT campus_file_action(:s,'finish_purge',:confirm,'')"), {"s": sid, "confirm": "DELETE "+sid})
        assert conn.scalar(text("SELECT file_state FROM sources WHERE id=:s"), {"s": sid}) == "purged"
        assert conn.scalar(text("SELECT count(*) FROM documents WHERE source_id=:s"), {"s": sid}) == 0


def test_rls_rejects_tenant_forgery(postgres):
    with postgres.connect() as conn:
        transaction = conn.begin()
        scoped(conn)
        with pytest.raises(DBAPIError):
            conn.execute(
                text(
                    "INSERT INTO sources(id,tenant_id,filename,kind,checksum,storage_key,uploaded_by,department,demo,created_at) VALUES(gen_random_uuid()::text,:t,'evil.txt','document','x','x',:u,'All',false,now())"
                ),
                dict(t=TENANT_B, u=USER),
            )
        transaction.rollback()


def test_cannot_self_promote_database_role(postgres):
    with postgres.connect() as conn:
        transaction = conn.begin()
        scoped(conn)
        with pytest.raises(DBAPIError):
            conn.execute(text("UPDATE memberships SET role='platform_admin' WHERE user_id=:u"), dict(u=USER))
        transaction.rollback()


def test_postgres_vector_keyword_retrieval(postgres):
    with postgres.connect() as conn:
        with conn.begin():
            scoped(conn)
            with sessionmaker(bind=conn)() as db:
                service = CampusService(
                    db,
                    resolve_actor(db, USER, DEMO_TENANT),
                    clock=lambda: datetime(2026, 10, 28, tzinfo=ZoneInfo("Asia/Kolkata")),
                )
                result = Retrieval(service).search("internal assessment circular")
                assert result["hits"]
                assert result["conflicts"]
                assert service.menu(100, True)


def prepared_source(postgres, *, private=False, kind="document", replacement=None, price=25):
    """Trusted worker stages data; subsequent test operations use campus_api."""
    store = MemoryStore()
    with sessionmaker(postgres)() as db:
        user = OTHER if private else USER
        service = CampusService(db, resolve_actor(db, user, DEMO_TENANT))
        payload = (b"Publication RLS zirconium policy." if kind == "document" else json.dumps([
            dict(name="PostgreSQL publication meal", price=price, vegetarian=True,
                 category="Meal", location="RLS test")
        ]).encode())
        # Distinct suffixes keep fixtures independent when tests share a database.
        from backend.app.models import uid

        if kind == "document":
            payload += uid().encode()
        else:
            rows = json.loads(payload)
            rows[0]["test_import_id"] = uid()  # Unmapped metadata keeps repeated disposable runs independent.
            payload = json.dumps(rows).encode()
        ingestion = Ingestion(service, store)
        job = ingestion.receive("policy.txt" if kind == "document" else "menu.json", payload,
                                ImportOptions(kind=kind, visibility="private" if private else "shared",
                                              replaces_source_id=replacement))
        ingestion.confirm(job)
        count = ingestion.process(job)
        job.status = "completed"
        job.preview = {**job.preview, "imported_count": count}
        db.flush()
        doc_id = db.scalar(select(Document.id).where(Document.source_id == job.source_id))
        result = job.source_id, job.id, doc_id
        db.commit()
        return result


def approve_publish(db, source_id):
    reviewer = CampusService(db, resolve_actor(db, REVIEWER, DEMO_TENANT))
    Publication(reviewer).transition(source_id, "approve")
    uploader = CampusService(db, resolve_actor(db, USER, DEMO_TENANT))
    Publication(uploader).transition(source_id, "publish")


def test_rls_hides_unpublished_documents_and_chunks_from_student(postgres):
    sid, _, doc_id = prepared_source(postgres)
    with postgres.begin() as conn:
        scoped(conn)
        conn.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u": OTHER})
        assert conn.scalar(text("SELECT count(*) FROM sources WHERE id=:id"), {"id": sid}) == 0
        assert conn.scalar(text("SELECT count(*) FROM ingestion_jobs WHERE source_id=:id"), {"id": sid}) == 0
        assert conn.scalar(text("SELECT count(*) FROM documents WHERE id=:id"), {"id": doc_id}) == 0
        assert conn.scalar(text("SELECT count(*) FROM document_chunks WHERE document_id=:id"), {"id": doc_id}) == 0
        with pytest.raises(DBAPIError):
            with conn.begin_nested():
                conn.execute(text("SELECT campus_source_transition(:id,'pending_review','approved','')"), {"id": sid})
    with postgres.begin() as conn:
        scoped(conn)
        with sessionmaker(bind=conn)() as db:
            approve_publish(db, sid)
        conn.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u": OTHER})
        assert conn.scalar(text("SELECT count(*) FROM documents WHERE id=:id"), {"id": doc_id}) == 1
        assert conn.scalar(text("SELECT count(*) FROM document_chunks WHERE document_id=:id"), {"id": doc_id}) > 0


def test_rls_cannot_bypass_publication_or_change_worker_staging(postgres):
    sid, job_id, _ = prepared_source(postgres)
    with postgres.begin() as conn:
        scoped(conn)
        for statement, params in [
            ("UPDATE sources SET publication_status='published' WHERE id=:id", {"id": sid}),
            ("UPDATE ingestion_jobs SET staged_data='{}' WHERE id=:id", {"id": job_id}),
        ]:
            with pytest.raises(DBAPIError):
                with conn.begin_nested():
                    conn.execute(text(statement), params)
        # 0009 allows the uploader's explicit approval; it still does not publish
        # through a raw UPDATE or give a student a transition capability.
        conn.execute(text("SELECT campus_source_transition(:id,'pending_review','approved','')"), {"id": sid})
        assert conn.scalar(text("SELECT publication_status FROM sources WHERE id=:id"), {"id": sid}) == "approved"
        conn.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u": OTHER})
        with pytest.raises(DBAPIError):
            with conn.begin_nested():
                conn.execute(text("SELECT campus_source_transition(:id,'approved','published','')"), {"id": sid})


def test_rls_private_document_denies_other_owners_including_admins(postgres):
    sid, _, doc_id = prepared_source(postgres, private=True)
    with postgres.begin() as conn:
        scoped(conn)
        assert conn.scalar(text("SELECT count(*) FROM sources WHERE id=:id"), {"id": sid}) == 0
        assert conn.scalar(text("SELECT count(*) FROM documents WHERE id=:id"), {"id": doc_id}) == 0
        assert conn.scalar(text("SELECT count(*) FROM document_chunks WHERE document_id=:id"), {"id": doc_id}) == 0
        conn.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u": OTHER})
        assert conn.scalar(text("SELECT count(*) FROM documents WHERE id=:id"), {"id": doc_id}) == 1
        assert conn.scalar(text("SELECT count(*) FROM document_chunks WHERE document_id=:id"), {"id": doc_id}) > 0


def test_rls_structured_publication_and_replacement_under_api_role(postgres):
    old, _, _ = prepared_source(postgres, kind="menu")
    with postgres.begin() as conn:
        scoped(conn)
        with sessionmaker(bind=conn)() as db:
            approve_publish(db, old)
    new, _, _ = prepared_source(postgres, kind="menu", replacement=old, price=40)
    with postgres.begin() as conn:
        scoped(conn)
        with sessionmaker(bind=conn)() as db:
            service = CampusService(db, resolve_actor(db, USER, DEMO_TENANT))
            assert service.menu(query="PostgreSQL publication meal")[0]["price"] == 25
            approve_publish(db, new)
            assert service.menu(query="PostgreSQL publication meal")[0]["price"] == 40
            assert db.get(Source, old).publication_status == "replaced"
            Publication(service).transition(new, "revoke", "RLS test revocation")
            assert not service.menu(query="PostgreSQL publication meal")
        # An administrator can lock a retained revoked row, but cannot edit it
        # while leaving it attached to the revoked source.
        with pytest.raises(DBAPIError):
            with conn.begin_nested():
                conn.execute(text("UPDATE campus_records SET payload=payload WHERE source_id=:s"), {"s": new})
    corrected, _, _ = prepared_source(postgres, kind="menu", price=45)
    with postgres.begin() as conn:
        scoped(conn)
        with sessionmaker(bind=conn)() as db:
            service = CampusService(db, resolve_actor(db, USER, DEMO_TENANT))
            approve_publish(db, corrected)
            assert service.menu(query="PostgreSQL publication meal")[0]["price"] == 45
            assert db.get(Source, new).publication_status == "revoked"
            Publication(service).transition(corrected, "revoke", "Keep repeat runs isolated")


def test_rls_student_cannot_attach_shared_rows_to_a_private_source(postgres):
    sid, _, _ = prepared_source(postgres, private=True)
    with postgres.begin() as conn:
        scoped(conn)
        conn.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u": OTHER})
        with pytest.raises(DBAPIError):
            with conn.begin_nested():
                conn.execute(text("""
                    INSERT INTO campus_records(id,tenant_id,kind,natural_key,department,owner_id,source_id,payload)
                    VALUES(gen_random_uuid()::text,:t,'menu',gen_random_uuid()::text,'All',NULL,:s,'{}')
                """), {"t": DEMO_TENANT, "s": sid})


def test_rls_api_upload_and_postgres_worker_failure_retry(postgres):
    """Use API grants for uploads; use the separate owner connection for work."""
    from backend.app.ingestion import work_once
    from backend.app.models import IngestionJob, uid

    class TransientStore(MemoryStore):
        unavailable = False

        def get(self, key):
            if self.unavailable:
                raise RuntimeError("Synthetic temporary storage outage")
            return super().get(key)

    store = TransientStore()
    with postgres.begin() as conn:
        scoped(conn)
        with sessionmaker(bind=conn)() as db:
            service = CampusService(db, resolve_actor(db, USER, DEMO_TENANT))
            ingestion = Ingestion(service, store)
            job = ingestion.receive("retry.txt", ("Retry zirconium policy " + uid()).encode(),
                                    ImportOptions(kind="document"))
            ingestion.confirm(job)
            db.flush()
            job_id, source_id = job.id, job.source_id
            assert job.status == "queued"
            assert db.get(Source, source_id).publication_status == "draft"

    factory = sessionmaker(postgres, expire_on_commit=False)
    store.unavailable = True
    assert work_once(factory, store=store)
    with factory() as db:
        job = db.get(IngestionJob, job_id)
        assert job.status == "failed" and job.attempts == 1
        assert db.get(Source, source_id).publication_status == "draft"
        assert not db.scalar(select(Document.id).where(Document.source_id == source_id))

    store.unavailable = False
    with postgres.begin() as conn:
        scoped(conn)
        with sessionmaker(bind=conn)() as db:
            service = CampusService(db, resolve_actor(db, USER, DEMO_TENANT))
            ingestion = Ingestion(service, store)
            job = ingestion.job(job_id)
            ingestion.preview(job)
            ingestion.confirm(job)
            db.flush()
    assert work_once(factory, store=store)
    with factory() as db:
        job = db.get(IngestionJob, job_id)
        assert job.status == "completed" and job.attempts == 2
        assert db.get(Source, source_id).publication_status == "pending_review"
    with postgres.begin() as conn:
        scoped(conn)
        with sessionmaker(bind=conn)() as db:
            approve_publish(db, source_id)
        conn.execute(text("SELECT set_config('app.user_id',:u,true)"), {"u": OTHER})
        assert conn.scalar(text("SELECT count(*) FROM documents WHERE source_id=:s"), {"s": source_id}) == 1


def test_concurrent_postgres_worker_processes_do_not_duplicate_jobs(postgres, monkeypatch):
    from backend.tests.test_worker_processes import exercise_concurrent_workers

    exercise_concurrent_workers(postgres, USER, DEMO_TENANT, monkeypatch)


def test_killed_postgres_worker_recovers_after_claim_lease(postgres, monkeypatch):
    from backend.tests.test_worker_processes import exercise_crashed_worker_recovery

    exercise_crashed_worker_recovery(postgres, USER, DEMO_TENANT, monkeypatch)


def test_rls_selected_document_limits_vector_keyword_and_conflict_queries(postgres):
    from fastapi import HTTPException

    first, _, first_doc = prepared_source(postgres)
    second, _, second_doc = prepared_source(postgres)
    with sessionmaker(postgres)() as db:
        for doc_id, claim in ((first_doc, "Friday"), (second_doc, "Tuesday")):
            doc = db.get(Document, doc_id)
            doc.topic, doc.claim = "selected-pg-scope", claim
        db.commit()
    with postgres.begin() as conn:
        scoped(conn)
        with sessionmaker(bind=conn)() as db:
            approve_publish(db, first)
            approve_publish(db, second)
            student = CampusService(db, resolve_actor(db, OTHER, DEMO_TENANT))
            result = Retrieval(student).search("zirconium policy", document_id=first_doc)
            assert result["hits"] and not result["conflicts"]
            assert {h["document_id"] for h in result["hits"]} == {first_doc}
            assert second_doc not in str(result)
            admin = CampusService(db, resolve_actor(db, USER, DEMO_TENANT))
            Publication(admin).transition(first, "revoke", "Selected source withdrawn")
            student = CampusService(db, resolve_actor(db, OTHER, DEMO_TENANT))
            assert db.scalar(select(Document.id).where(Document.id == first_doc)) is None
            with pytest.raises(HTTPException) as exc:
                Retrieval(student).search("zirconium policy", document_id=first_doc)
            assert exc.value.status_code == 404
