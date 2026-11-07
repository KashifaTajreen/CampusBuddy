"""Publication boundaries use real memberships and the existing ingestion pipeline."""

import json
from dataclasses import replace
import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from backend.app.auth import resolve_actor
from backend.app.ingestion import Ingestion, work_once
from backend.app.models import Source, Document, DocumentChunk, IngestionJob, Membership
from backend.app.publication import Publication
from backend.app.retrieval import Retrieval
from backend.app.schemas import ImportOptions
from backend.app.services import CampusService
from backend.app.storage import ObjectStore
from backend.tests.conftest import publish


def service(env, user):
    return CampusService(env.db, resolve_actor(env.db, user, env.tenant), clock=env.service.clock)


def stage(env, job):
    ingestion = Ingestion(env.service, env.store)
    ingestion.confirm(job)
    count = ingestion.process(job)
    job.status = "completed"
    job.preview = {**job.preview, "imported_count": count}
    env.db.flush()


def document(env, text=b"Library zirconium policy: return books by Friday.", **options):
    return Ingestion(env.service, env.store).receive(
        "policy.txt", text, ImportOptions(kind="document", title="Zirconium policy", **options)
    )


def menu(env, price, **options):
    rows = [
        dict(
            name="Publication test meal",
            price=price,
            vegetarian=True,
            category="Meal",
            location="Test canteen",
        )
    ]
    return Ingestion(env.service, env.store).receive(
        "menu.json", json.dumps(rows).encode(), ImportOptions(kind="menu", **options)
    )


def test_known_draft_id_denied_before_storage_read(env, monkeypatch):
    job = document(env)
    student = service(env, env.other)
    store = ObjectStore()
    reads = []
    monkeypatch.setattr(store, "get", lambda key: reads.append(key))
    with pytest.raises(HTTPException):
        store.download(student, job.source_id)
    assert not reads
    assert not student.db.scalar(student.scope(Document).where(Document.source_id == job.source_id))


def test_processing_and_approval_do_not_publish_documents_or_chunks(env):
    job = document(env)
    stage(env, job)
    student = service(env, env.other)
    source = env.db.get(Source, job.source_id)
    assert source.publication_status == "pending_review"
    doc = env.db.scalar(select(Document).where(Document.source_id == source.id))
    assert env.db.scalar(select(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    assert not env.db.scalar(student.scope(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    assert not any(h["source_id"] == source.id for h in Retrieval(student).search("zirconium policy")["hits"])
    Publication(service(env, env.reviewer)).transition(source.id, "approve")
    assert not env.db.scalar(student.scope(Document).where(Document.id == doc.id))
    with pytest.raises(HTTPException):
        student.original_source(source.id)
    Publication(env.service).transition(source.id, "publish")
    assert env.db.scalar(student.scope(Document).where(Document.id == doc.id))
    assert any(h["source_id"] == source.id for h in Retrieval(student).search("zirconium policy")["hits"])


def test_authorized_uploader_can_approve_own_source_under_0009_policy(env):
    job = document(env)
    stage(env, job)
    Publication(env.service).transition(job.source_id, "approve")
    source = env.db.get(Source, job.source_id)
    assert source.publication_status == 'approved'
    assert source.approved_by == env.user


@pytest.mark.parametrize("action", ["approve", "publish", "reject", "revoke"])
def test_student_cannot_transition_shared_source(env, action):
    job = document(env)
    stage(env, job)
    with pytest.raises(HTTPException) as exc:
        Publication(service(env, env.other)).transition(job.source_id, action, "Untrusted request")
    assert exc.value.status_code == 403


def test_mixed_department_original_restricted_but_records_remain_readable(env):
    rows = [
        dict(
            name="Scoped meal " + dept,
            department=dept,
            price=20,
            vegetarian=True,
            category="Meal",
            location="Scope test",
        )
        for dept in ("AIML", "CSE")
    ]
    job = Ingestion(env.service, env.store).receive(
        "mixed.json", json.dumps(rows).encode(), ImportOptions(kind="menu", department="All")
    )
    publish(env, job)
    student = service(env, env.other)
    records = student.menu(query="Scoped meal")
    assert [r["department"] for r in records] == ["CSE"]
    assert records[0]["original_download_allowed"] is False
    assert student.source(job.source_id)  # Metadata is safe; unredacted bytes are not.
    with pytest.raises(HTTPException) as exc:
        student.original_source(job.source_id)
    assert exc.value.status_code == 403
    assert env.service.original_source(job.source_id)
    department_admin = CampusService(env.db, replace(student.actor, role="department_admin"))
    with pytest.raises(HTTPException):
        department_admin.original_source(job.source_id)


def test_structured_replacement_does_not_touch_live_records_until_publication(env):
    old = menu(env, 25)
    publish(env, old)
    new = menu(env, 40, replaces_source_id=old.source_id)
    stage(env, new)
    assert env.service.menu(query="Publication test meal")[0]["price"] == 25
    assert env.db.get(Source, old.source_id).publication_status == "published"
    publish(env, new)
    record = env.service.menu(query="Publication test meal")[0]
    assert record["price"] == 40 and record["source_id"] == new.source_id
    assert env.db.get(Source, old.source_id).publication_status == "replaced"
    with pytest.raises(HTTPException):
        env.service.original_source(old.source_id)


def test_document_replacement_is_atomic_and_excludes_old_chunks(env):
    old = document(env)
    publish(env, old)
    new = document(
        env, b"Library zirconium policy: return books by Monday.", replaces_source_id=old.source_id
    )
    stage(env, new)
    student = service(env, env.other)
    assert {h["source_id"] for h in Retrieval(student).search("zirconium")["hits"]} == {old.source_id}
    publish(env, new)
    assert {h["source_id"] for h in Retrieval(student).search("zirconium")["hits"]} == {new.source_id}
    doc = env.db.scalar(select(Document).where(Document.source_id == new.source_id))
    assert doc.version == 2 and doc.supersedes_id


def test_rejected_replacement_keeps_previous_source_available(env):
    old = menu(env, 25)
    publish(env, old)
    new = menu(env, 40, replaces_source_id=old.source_id)
    stage(env, new)
    Publication(service(env, env.reviewer)).transition(new.source_id, "reject", "Wrong prices")
    assert env.service.menu(query="Publication test meal")[0]["price"] == 25
    assert env.db.get(Source, old.source_id).publication_status == "published"
    with pytest.raises(HTTPException):
        Publication(env.service).transition(new.source_id, "publish")


def test_failed_publication_rolls_back_new_status_and_replacement(env, monkeypatch):
    old = menu(env, 25)
    publish(env, old)
    new = menu(env, 40, replaces_source_id=old.source_id)
    stage(env, new)
    Publication(service(env, env.reviewer)).transition(new.source_id, "approve")
    old_id, new_id = old.source_id, new.source_id
    env.db.commit()

    def fail(*args):
        raise ValueError("Simulated activation failure")

    monkeypatch.setattr(Ingestion, "activate_records", fail)
    with pytest.raises(ValueError):
        Publication(env.service).transition(new_id, "publish")
    assert env.db.get(Source, old_id).publication_status == "published"
    assert env.db.get(Source, new_id).publication_status == "approved"
    env.db.rollback()
    assert env.db.get(Source, old_id).publication_status == "published"
    assert env.db.get(Source, new_id).publication_status == "approved"
    assert env.service.menu(query="Publication test meal")[0]["price"] == 25


def test_worker_rechecks_revoked_uploader(env):
    job = document(env)
    Ingestion(env.service, env.store).confirm(job)
    member = env.db.scalar(select(Membership).where(Membership.user_id == env.user))
    member.role = "student"
    job_id, source_id = job.id, job.source_id
    env.db.commit()
    assert work_once(env.factory, env.store)
    env.db.expire_all()
    assert env.db.get(IngestionJob, job_id).status == "failed"
    assert env.db.get(Source, source_id).publication_status == "draft"
    assert not env.db.scalar(select(Document).where(Document.source_id == source_id))


@pytest.mark.parametrize("person", ["uploader", "reviewer"])
def test_publication_rechecks_participants_after_approval(env, person):
    job = document(env)
    stage(env, job)
    Publication(service(env, env.reviewer)).transition(job.source_id, "approve")
    user = env.user if person == "uploader" else env.reviewer
    member = env.db.scalar(
        select(Membership).where(Membership.user_id == user, Membership.tenant_id == env.tenant)
    )
    member.active = False
    env.db.flush()
    publisher = service(env, env.reviewer if person == "uploader" else env.user)
    with pytest.raises(HTTPException) as exc:
        Publication(publisher).transition(job.source_id, "publish")
    assert exc.value.status_code == 403
    assert env.db.get(Source, job.source_id).publication_status == "approved"


def test_revoke_cancels_queued_work_and_is_terminal(env):
    job = document(env)
    ingestion = Ingestion(env.service, env.store)
    ingestion.confirm(job)
    Publication(service(env, env.reviewer)).transition(job.source_id, "revoke", "Withdrawn before processing")
    env.db.commit()
    assert not work_once(env.factory, env.store)
    env.db.refresh(job)
    assert job.status == "cancelled"
    with pytest.raises(ValueError):
        ingestion.confirm(job)
    with pytest.raises(ValueError):
        ingestion.preview(job)


def test_revoked_published_source_disappears_from_records_and_retrieval(env):
    job = document(env)
    publish(env, job)
    Publication(env.service).transition(job.source_id, "revoke", "Policy withdrawn")
    student = service(env, env.other)
    assert not any(h["source_id"] == job.source_id for h in Retrieval(student).search("zirconium")["hits"])
    with pytest.raises(HTTPException):
        student.original_source(job.source_id)


def test_private_document_remains_owner_only_even_for_administrators(env):
    owner = service(env, env.other)
    ingestion = Ingestion(owner, env.store)
    job = ingestion.receive(
        "personal.txt",
        b"Private zirconium research notes.",
        ImportOptions(kind="document", visibility="private"),
    )
    ingestion.confirm(job)
    job_id, source_id = job.id, job.source_id
    env.db.commit()
    assert work_once(env.factory, env.store)
    env.db.expire_all()
    assert env.db.get(IngestionJob, job_id).status == "completed"
    assert owner.source(source_id).owner_id == env.other
    assert any(h["source_id"] == source_id for h in Retrieval(owner).search("zirconium research")["hits"])
    assert owner.original_source(source_id)
    for user in (env.user, env.reviewer):
        other = service(env, user)
        with pytest.raises(HTTPException):
            other.original_source(source_id)
        with pytest.raises(HTTPException):
            Publication(other).review(source_id)
        assert not env.db.scalar(other.scope(Document).where(Document.source_id == source_id))
    assert all(s.id != source_id for s in Publication(env.service).sources())


def test_even_private_owner_admin_cannot_make_private_source_shared(env):
    job = document(env, visibility="private")
    stage(env, job)
    assert env.db.get(Source, job.source_id).publication_status == "published"
    with pytest.raises(HTTPException):
        Publication(env.service).transition(job.source_id, "publish")


def test_worker_failure_retry_stays_unpublished(env, monkeypatch):
    from backend.app import ingestion as module

    job = document(env)
    Ingestion(env.service, env.store).confirm(job)
    job_id, source_id = job.id, job.source_id
    env.db.commit()
    embed = module.embeddings

    def fail(*args):
        raise ValueError("Transient indexing failure")

    before = env.db.scalar(select(func.count()).select_from(DocumentChunk))
    monkeypatch.setattr(module, "embeddings", fail)
    assert work_once(env.factory, env.store)
    env.db.expire_all()
    job = env.db.get(IngestionJob, job_id)
    assert job.status == "failed"
    assert env.db.get(Source, source_id).publication_status == "draft"
    assert env.db.scalar(select(func.count()).select_from(DocumentChunk)) == before
    with pytest.raises(HTTPException):
        Publication(service(env, env.reviewer)).transition(source_id, "approve")
    monkeypatch.setattr(module, "embeddings", embed)
    job.status = "received"
    ingestion = Ingestion(env.service, env.store)
    ingestion.preview(job)
    ingestion.confirm(job)
    env.db.commit()
    assert work_once(env.factory, env.store)
    env.db.expire_all()
    job = env.db.get(IngestionJob, job_id)
    assert job.status == "completed" and job.attempts == 2
    assert env.db.get(Source, source_id).publication_status == "pending_review"
    publish(env, job)
    assert service(env, env.other).source(source_id)


def test_replacement_cannot_cross_tenant_kind_department_or_private_scope(env):
    old = menu(env, 25)
    publish(env, old)
    with pytest.raises(ValueError):
        document(env, replaces_source_id=old.source_id)
    with pytest.raises(ValueError):
        menu(env, 40, replaces_source_id=old.source_id, department="CSE")
    foreign = env.db.scalar(select(Source).where(Source.tenant_id == env.other_tenant))
    with pytest.raises(HTTPException):
        menu(env, 40, replaces_source_id=foreign.id)


def test_department_reviewer_cannot_review_other_departments(env):
    job = document(env, department="AIML")
    stage(env, job)
    member = env.db.scalar(select(Membership).where(Membership.user_id == env.reviewer))
    member.role, member.department = "department_admin", "CSE"
    env.db.flush()
    with pytest.raises(HTTPException):
        Publication(service(env, env.reviewer)).transition(job.source_id, "approve")


def test_department_reviewer_cannot_read_another_uploaders_raw_structured_preview(env):
    job = menu(env, 25, department="CSE")
    member = env.db.scalar(select(Membership).where(Membership.user_id == env.reviewer))
    member.role, member.department = "department_admin", "CSE"
    env.db.flush()
    reviewer = Publication(service(env, env.reviewer))
    assert all(s.id != job.source_id for s in reviewer.sources())
    with pytest.raises(HTTPException):
        reviewer.review(job.source_id)


def test_specific_department_import_cannot_hide_other_department_rows(env):
    rows = [
        dict(
            name="Wrong scope",
            department="AIML",
            price=20,
            vegetarian=True,
            category="Meal",
            location="Scope test",
        )
    ]
    job = Ingestion(env.service, env.store).receive(
        "wrong-scope.json", json.dumps(rows).encode(), ImportOptions(kind="menu", department="CSE")
    )
    assert job.preview["error_count"] == 1
    with pytest.raises(ValueError):
        Ingestion(env.service, env.store).confirm(job)


def test_structured_reviewer_must_retain_institution_wide_permission(env):
    job = menu(env, 25, department="CSE")
    stage(env, job)
    Publication(service(env, env.reviewer)).transition(job.source_id, "approve")
    member = env.db.scalar(select(Membership).where(Membership.user_id == env.reviewer))
    member.role, member.department = "department_admin", "CSE"
    env.db.flush()
    with pytest.raises(HTTPException):
        Publication(env.service).transition(job.source_id, "publish")
    assert env.db.get(Source, job.source_id).publication_status == "approved"


def test_demo_flag_on_http_style_import_does_not_bypass_approval(env):
    job = document(env, demo=True)
    stage(env, job)
    assert env.db.get(Source, job.source_id).publication_status == "pending_review"
    with pytest.raises(HTTPException):
        Publication(env.service).transition(job.source_id, "publish")


def test_retained_service_does_not_use_cached_role_after_external_revocation(env):
    job = document(env)
    stage(env, job)
    source_id = job.source_id
    env.db.commit()
    cached = env.db.scalar(
        select(Membership).where(Membership.user_id == env.user, Membership.tenant_id == env.tenant)
    )
    assert cached.role == "institution_admin"
    with env.factory() as db:
        member = db.scalar(
            select(Membership).where(Membership.user_id == env.user, Membership.tenant_id == env.tenant)
        )
        member.role = "student"
        db.commit()
    with pytest.raises(HTTPException) as exc:
        Publication(env.service).transition(source_id, "revoke", "Stale administrator attempt")
    assert exc.value.status_code == 403
    assert env.db.get(Source, source_id).publication_status == "pending_review"


def test_competing_replacement_cannot_replace_an_already_replaced_source(env):
    old = menu(env, 25)
    publish(env, old)
    first = menu(env, 40, replaces_source_id=old.source_id)
    second = menu(env, 50, replaces_source_id=old.source_id)
    stage(env, first)
    stage(env, second)
    Publication(service(env, env.reviewer)).transition(second.source_id, "approve")
    publish(env, first)
    with pytest.raises(HTTPException) as exc:
        Publication(env.service).transition(second.source_id, "publish")
    assert exc.value.status_code == 409
    assert env.db.scalar(select(Source.publication_status).where(Source.id == second.source_id)) == "approved"
    assert env.db.get(Source, second.source_id).publication_status == "approved"
    assert env.service.menu(query="Publication test meal")[0]["price"] == 40


@pytest.mark.parametrize("operation", ["revoke", "delete"])
def test_sqlite_revocation_during_processing_cannot_resurrect_source(env, monkeypatch, operation):
    import threading
    from backend.app import ingestion as module

    job = document(env)
    Ingestion(env.service, env.store).confirm(job)
    source_id = job.source_id
    env.db.commit()
    parsing, release, revoking, revoked = (threading.Event() for _ in range(4))
    errors = []
    extract = module.extract_pages

    def blocked(*args):
        parsing.set()
        if not release.wait(10):
            raise ValueError("Test parser release timed out")
        return extract(*args)

    def worker():
        try:
            work_once(env.factory, env.store)
        except Exception as exc:
            errors.append(exc)

    def revoke():
        try:
            with env.factory() as db:
                actor = resolve_actor(db, env.reviewer, env.tenant)
                revoking.set()
                if operation == "delete":
                    from backend.app.files import Files
                    Files(CampusService(db, actor), env.store).change(source_id, "delete")
                else:
                    Publication(CampusService(db, actor)).transition(source_id, "revoke", "Withdrawn during processing")
                db.commit()
                revoked.set()
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(module, "extract_pages", blocked)
    worker_thread = threading.Thread(target=worker)
    revoke_thread = threading.Thread(target=revoke)
    worker_thread.start()
    try:
        assert parsing.wait(5)
        revoke_thread.start()
        assert revoking.wait(5)
        assert not revoked.wait(0.2), "Revocation must serialize with the worker's source transaction"
    finally:
        release.set()
        worker_thread.join(10)
        if revoke_thread.ident:
            revoke_thread.join(10)
    assert not worker_thread.is_alive() and not revoke_thread.is_alive()
    assert not errors and revoked.is_set()
    env.db.expire_all()
    assert env.db.get(Source, source_id).publication_status == "revoked"
