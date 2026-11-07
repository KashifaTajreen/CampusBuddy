import pytest
from fastapi import HTTPException
from sqlalchemy import select, func
from backend.app.files import Files
from backend.app.ingestion import Ingestion, work_once
from backend.app.models import Source, DocumentChunk, IngestionJob, Document
from backend.app.schemas import ImportOptions
from backend.app.retrieval import Retrieval
from backend.tests.conftest import publish
from backend.tests.test_selected_documents import pdf_document, actor_service
from backend.tests.test_file_parsing import MENU, pdf_table
from backend.tests.test_auth_api import client as client, key as key, token


def test_delete_restore_requires_processing_and_independent_approval(env):
    document, job = pdf_document(env)
    source = env.db.get(Source, job.source_id)
    Files(env.service, env.store).change(source.id, "delete")
    assert source.file_state == "deleted" and not list(env.db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document.id)))
    assert job.status == "cancelled"
    with pytest.raises(HTTPException):
        env.service.original_source(source.id)
    assert document.id not in str(Retrieval(env.service).search("Zirconium scholarship"))
    Files(env.service, env.store).change(source.id, "restore")
    assert source.publication_status == "draft" and source.approved_by is None
    new_job = env.db.scalar(select(IngestionJob).where(IngestionJob.source_id == source.id, IngestionJob.status == "preview"))
    assert new_job
    publish(env, new_job)
    assert source.publication_status == "published" and env.service.selected_document(document.id)
    assert env.db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == document.id)) > 0


def test_purge_confirmation_storage_failure_retry_and_content_scrub(env, monkeypatch):
    document, job = pdf_document(env)
    source = env.db.get(Source, job.source_id)
    lifecycle = Files(env.service, env.store)
    lifecycle.change(source.id, "delete")
    with pytest.raises(HTTPException):
        lifecycle.change(source.id, "purge", "yes")
    original_delete = env.store.delete
    monkeypatch.setattr(env.store, "delete", lambda *_: (_ for _ in ()).throw(OSError("fixture storage failure")))
    with pytest.raises(HTTPException) as exc:
        lifecycle.change(source.id, "purge", "DELETE "+source.id)
    assert exc.value.status_code == 503 and source.file_state == "purge_pending"
    monkeypatch.setattr(env.store, "delete", original_delete)
    key = source.storage_key
    lifecycle.change(source.id, "purge", "DELETE "+source.id)
    assert source.file_state == "purged" and key not in env.store.items and not source.storage_key
    assert env.db.get(Document, document.id) is None
    assert job.options == job.preview == job.staged_data == {}
    with pytest.raises(HTTPException):
        lifecycle.change(source.id, "restore")


@pytest.mark.parametrize("scope", ["student", "cross_tenant", "other_private", "admin_other_private"])
def test_lifecycle_authorization(env, scope):
    document, job = pdf_document(env, private=scope in ("other_private", "admin_other_private"))
    service = actor_service(env, user=env.reviewer if scope == "admin_other_private" else env.other,
                            tenant=env.other_tenant if scope == "cross_tenant" else env.tenant)
    with pytest.raises(HTTPException):
        Files(service, env.store).change(job.source_id, "delete")
    assert env.db.get(Source, job.source_id).file_state == "active"


def test_menu_pdf_correction_duplicate_validation_and_publication(env):
    ingestion = Ingestion(env.service, env.store)
    job = ingestion.receive("menu.pdf", pdf_table(), ImportOptions(kind="menu"))
    assert job.preview["format"] == "pdf" and job.preview["valid_count"] == 1
    ingestion.preview(job, rows=[{**MENU, "price": "bad"}])
    assert job.preview["error_count"]
    with pytest.raises(ValueError):
        ingestion.confirm(job)
    ingestion.preview(job, rows=[MENU, MENU])
    assert job.preview["error_count"]
    ingestion.preview(job, rows=[{**MENU, "price": 30}])
    publish(env, job)
    assert any(r["name"] == MENU["name"] and r["price"] == 30 for r in env.service.menu())


def test_deleted_queued_job_cannot_be_reclaimed(env):
    ingestion = Ingestion(env.service, env.store)
    job = ingestion.receive("queue.txt", b"Persistent campus notice pending worker.", ImportOptions(kind="document"))
    ingestion.confirm(job)
    Files(env.service, env.store).change(job.source_id, "delete")
    env.db.commit()
    work_once(env.factory, env.store)
    env.db.expire_all()
    assert env.db.get(IngestionJob, job.id).status == "cancelled"
    assert env.db.get(Source, job.source_id).file_state == "deleted"


def test_document_rollback_keeps_current_available_until_reviewed(env):
    original, first = pdf_document(env)
    ingestion = Ingestion(env.service, env.store)
    newer = ingestion.receive("new.txt", b"Zirconium scholarship revised deadline Wednesday.", ImportOptions(kind="document", title="New handbook", replaces_source_id=first.source_id))
    publish(env, newer)
    old_source = env.db.get(Source, first.source_id)
    assert old_source.publication_status == "replaced"
    Files(env.service, env.store).change(old_source.id, "rollback", replaces_source_id=newer.source_id)
    assert env.service.source(newer.source_id).publication_status == "published"
    restored_job = env.db.scalar(select(IngestionJob).where(IngestionJob.source_id == old_source.id, IngestionJob.status == "preview"))
    publish(env, restored_job)
    assert old_source.publication_status == "published" and env.db.get(Source, newer.source_id).publication_status == "replaced"
    assert env.service.selected_document(original.id)


def test_section_lab_batch_pdf_rows_are_validated(env):
    row = dict(department=env.service.actor.department, semester=env.service.actor.semester, section=env.service.actor.section,
               subject="Operating Systems Lab", faculty_code=env.service.faculty()[0]["code"], room="Lab 1", day=2,
               start_time="14:00", end_time="16:00", effective_from="2026-10-01", effective_to="2026-12-31", lab_batch="B1")
    data = pdf_table(list(row), list(row.values()))
    job = Ingestion(env.service, env.store).receive("lab.pdf", data, ImportOptions(kind="timetable"))
    assert job.preview["valid_count"] == 1, job.preview["errors"]
    assert job.preview["records"][0]["lab_batch"] == "B1"
    identity = {key: key for key in row}
    Ingestion(env.service, env.store).preview(job, mapping=identity, rows=[row, {**row, "lab_batch": "", "subject": "Whole section lecture"}])
    assert not job.preview['error_count'] and job.preview['overlap_warnings']
    Ingestion(env.service, env.store).preview(job, mapping=identity, rows=[row, {**row, "lab_batch": "B2"}])
    assert not job.preview["error_count"]


def test_direct_api_deletion_denials_and_saved_answer_redaction(client, key, env):
    document, job = pdf_document(env)
    env.db.commit()
    client.headers["Authorization"] = "Bearer " + token(key, env.other)
    assert client.get("/v1/admin/files").status_code == 403
    response = client.post(f"/v1/admin/sources/{job.source_id}/file", json={"action": "delete"})
    assert response.status_code in (403, 404)
    answer = client.post("/v1/chat", json={"question": "Zirconium scholarship deadline", "document_id": document.id})
    assert answer.status_code == 200 and answer.json()["records"]
    conversation = answer.json()["conversation_id"]
    client.headers["Authorization"] = "Bearer " + token(key, env.user)
    first_page = client.get("/v1/admin/files?limit=1&offset=0").json()
    second_page = client.get("/v1/admin/files?limit=1&offset=1").json()
    assert len(first_page) == len(second_page) == 1 and first_page[0]["id"] != second_page[0]["id"]
    response = client.post(f"/v1/admin/sources/{job.source_id}/file", json={"action": "delete"})
    assert response.status_code == 200 and response.json()["file_state"] == "deleted"
    client.headers["Authorization"] = "Bearer " + token(key, env.other)
    assert client.get(f"/v1/sources/{job.source_id}").status_code in (403, 404)
    history = client.get(f"/v1/conversations/{conversation}").json()
    assert all(not m["content"].get("records") for m in history if m["role"] == "assistant")
