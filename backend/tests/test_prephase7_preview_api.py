import pytest
from sqlalchemy import select
from backend.app.models import Membership
from backend.app.ingestion import Ingestion
from backend.app.schemas import ImportOptions
from backend.app import storage
from backend.tests.test_auth_api import client as client, key as key, token
from backend.tests.test_prephase7_documents import workbook


def test_draft_content_preview_and_current_role_checks(client,env,monkeypatch):
    monkeypatch.setattr(storage,"ObjectStore",lambda:env.store)
    job=Ingestion(env.service,env.store).receive("project.xlsx",workbook(),ImportOptions(kind="document"))
    env.db.commit()
    path=f"/v1/admin/sources/{job.source_id}/content-preview"
    response=client.get(path)
    assert response.status_code==200 and response.headers["cache-control"]=="no-store"
    assert response.json()["tables"][1]["rows"][0]["Faculty Mentor"]=="Dr. Synthetic Mentor"
    membership=env.db.scalar(select(Membership).where(Membership.user_id==env.user,Membership.tenant_id==env.tenant))
    membership.role="student"
    env.db.commit()
    assert client.get(path).status_code==403


def test_deleted_preview_and_anonymous_access_denied(client,env,monkeypatch):
    from backend.app.files import Files
    monkeypatch.setattr(storage,"ObjectStore",lambda:env.store)
    job=Ingestion(env.service,env.store).receive("project.xlsx",workbook(),ImportOptions(kind="document"))
    env.db.commit()
    path=f"/v1/admin/sources/{job.source_id}/content-preview"
    Files(env.service,env.store).change(job.source_id,"delete")
    env.db.commit()
    assert client.get(path).status_code==404
    client.headers.pop("Authorization")
    assert client.get(path).status_code==401


def test_student_number_is_admin_verified_not_a_preference(client,env):
    membership=env.db.scalar(select(Membership).where(Membership.user_id==env.user,Membership.tenant_id==env.tenant))
    membership.role="student"
    env.db.commit()
    assert client.patch("/v1/me",json=dict(name="Student",interests="",student_number="1RV25CI077")).status_code==422
    assert client.post("/v1/admin/memberships",json=dict(user_id=env.other,name="Student",role="student",department="CSE",semester=5,section="B",student_number="1RV25CI077")).status_code==403


def test_verified_identity_change_invalidates_saved_student_row_answers(client,env,key):
    from backend.tests.test_prephase7_documents import student
    from backend.tests.test_product_workflows import finish
    own=student(env)
    job=Ingestion(env.service,env.store).receive("history.xlsx",workbook(),ImportOptions(kind="document"))
    finish(env,env.service,job)
    client.headers["Authorization"]="Bearer "+token(key,own.actor.user_id)
    answer=client.post("/v1/chat",json={"question":"Who is my skill lab mentor?"})
    assert answer.status_code==200
    conversation=answer.json()["conversation_id"]
    path=f"/v1/conversations/{conversation}"
    assert client.get(path).json()
    membership=env.db.scalar(select(Membership).where(Membership.user_id==own.actor.user_id,Membership.tenant_id==env.tenant))
    membership.student_number="1RV25CI999"
    env.db.commit()
    assert client.get(path).json()==[]


@pytest.mark.parametrize("restriction",["removed_row_audience","legacy_missing_chunk_ids"])
def test_saved_student_answers_recheck_current_row_permissions(client,env,key,restriction):
    from backend.app.models import Document, DocumentChunk, Message
    from backend.tests.test_prephase7_documents import student
    from backend.tests.test_product_workflows import finish
    own=student(env)
    job=Ingestion(env.service,env.store).receive("history-access.xlsx",workbook(),ImportOptions(kind="document"))
    finish(env,env.service,job)
    client.headers["Authorization"]="Bearer "+token(key,own.actor.user_id)
    response=client.post("/v1/chat",json={"question":"Who is my skill lab mentor?"})
    assert response.status_code==200
    conversation=response.json()["conversation_id"]
    path=f"/v1/conversations/{conversation}"
    assert any(m["role"]=="assistant" for m in client.get(path).json())
    if restriction=="removed_row_audience":
        # Legacy/quarantined or re-indexed evidence can lose its row grant while
        # the parent source remains published and the identity stays unchanged.
        document=env.db.scalar(select(Document).where(Document.source_id==job.source_id))
        for chunk in env.db.scalars(select(DocumentChunk).where(DocumentChunk.document_id==document.id)):
            chunk.student_numbers=[]
    else:
        message=env.db.scalar(select(Message).where(Message.conversation_id==conversation,Message.role=="assistant"))
        message.content={**message.content,"sources":[{"id":job.source_id}]}
    env.db.commit()
    assert not any(m["role"]=="assistant" for m in client.get(path).json())


@pytest.mark.parametrize("suffix",[".docx",".pptx"])
def test_office_text_and_table_preview(suffix):
    from backend.app.parsing import parse_file
    from backend.tests.test_file_parsing import office
    result=parse_file("table"+suffix,office(suffix),preview_only=True)
    assert result["tables"] and result["tables"][0]["rows"][0]["name"]=="Test idli"


def test_scanned_pdf_and_image_preview_does_not_need_ocr():
    import pymupdf
    from backend.app.parsing import parse_inline
    image=pymupdf.Pixmap(pymupdf.csRGB,(0,0,100,100),False)
    image.clear_with(255)
    png=image.tobytes("png")
    document=pymupdf.open()
    for _ in range(4):
        page=document.new_page()
        page.insert_image(page.rect,stream=png)
    result=parse_inline("scan.pdf",document.tobytes(),ocr=False,preview_only=True)
    assert len(result["images"])==3 and result["truncated"]
    assert parse_inline("image.png",png,ocr=False,preview_only=True)["images"][0]["data"].startswith("data:image/png;base64,")


def test_disabled_ocr_does_not_prevent_original_preview_or_enable_processing(client,env,monkeypatch):
    import pymupdf
    from backend.app import ingestion
    from backend.app.config import Settings
    monkeypatch.setattr(ingestion,"settings",lambda:Settings(_env_file=None,ocr_enabled=False))
    monkeypatch.setattr(storage,"ObjectStore",lambda:env.store)
    image=pymupdf.Pixmap(pymupdf.csRGB,(0,0,100,100),False)
    image.clear_with(255)
    png=image.tobytes("png")
    pipeline=Ingestion(env.service,env.store)
    job=pipeline.receive("unindexed.png",png,ImportOptions(kind="document"))
    env.db.commit()
    assert job.status=="preview" and job.preview["needs_ocr"] and job.preview["error_count"]
    assert client.get(f"/v1/admin/sources/{job.source_id}/content-preview").json()["images"]
    with pytest.raises(ValueError):
        pipeline.confirm(job)
    with pytest.raises(ValueError,match="disabled"):
        pipeline.preview(job)
