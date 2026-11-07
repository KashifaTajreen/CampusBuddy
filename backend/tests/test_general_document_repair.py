from pathlib import Path
import os

import pytest
from sqlalchemy import select

from backend.app.ingestion import Ingestion
from backend.app.models import Document, DocumentChunk, Source
from backend.app.router import Router
from backend.app.schemas import ImportOptions
from backend.tests.conftest import publish


def test_readable_document_needs_no_structured_columns_and_uses_corrections(env):
    ingest = Ingestion(env.service, env.store)
    original = b"Dr. Deepamala N is a professor. Research interests include networks."
    job = ingest.receive("faculty.txt", original, ImportOptions(kind="document", title="Faculty profile"))
    assert job.preview["valid_count"] == 1 and not job.preview["error_count"]
    assert not job.preview.get("schema_mismatch")
    corrected = "Dr. Deepamala N is a professor. Research interests include Machine Learning."
    ingest.preview(job, pages=[{"page":1,"text":corrected}])
    publish(env, job)
    doc = env.db.scalar(select(Document).where(Document.source_id == job.source_id))
    chunk = env.db.scalar(select(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    assert chunk.text == corrected and chunk.page == 1
    assert env.store.get(env.db.get(Source,job.source_id).storage_key) == original
    result = Router(env.service).ask("Who is Dr. Deepamala N?")
    assert result["kind"] == "passages" and result["records"]
    assert {s["id"] for s in result["sources"]} == {job.source_id}


def test_empty_correction_blocks_processing_and_page_ids_cannot_change(env):
    ingest = Ingestion(env.service, env.store)
    job = ingest.receive("notice.txt", b"An ordinary readable official notice.", ImportOptions(kind="document"))
    with pytest.raises(ValueError, match="page identifiers"):
        ingest.preview(job, pages=[{"page":2,"text":"Changed page"}])
    ingest.preview(job, pages=[{"page":1,"text":""}])
    assert job.preview["error_count"] == 1
    with pytest.raises(ValueError):
        ingest.confirm(job)


def test_explicit_conversion_preserves_original_structured_draft(env):
    ingest = Ingestion(env.service, env.store)
    original = b"An ordinary campus event poster with no spreadsheet columns."
    job = ingest.receive("poster.txt", original, ImportOptions(kind="timetable"))
    assert job.preview["schema_mismatch"]
    converted = ingest.as_document(job)
    assert converted.source_id != job.source_id and converted.options["kind"] == "document"
    assert converted.preview["valid_count"] and not converted.preview["error_count"]
    assert env.db.get(Source,job.source_id).publication_status == "draft"
    assert env.store.get(env.db.get(Source,converted.source_id).storage_key) == original


def test_actual_deepamala_docx_requires_text_not_rows(env):
    path = Path(os.environ.get('CAMPUS_PRIVATE_FACULTY_REFERENCE', str(Path(__file__).resolve().parents[2]/'.scratch/private-fixtures/faculty-profile.docx')))
    if not path.exists():
        pytest.skip("Actual operator-owned faculty DOCX is unavailable on this machine.")
    ingest = Ingestion(env.service,env.store)
    job = ingest.receive(path.name,path.read_bytes(),ImportOptions(kind="document",department="AIML"))
    assert not job.preview["columns"] and job.preview["valid_count"] == 2
    assert "Natural Language Processing" in job.preview["editable_pages"][0]["text"]
    publish(env,job)
    assert job.preview["valid_count"] == 2


@pytest.mark.parametrize("format", ["pdf", "png", "jpg", "scanned.pdf"])
def test_pdf_and_ocr_images_follow_document_review_pipeline(env, format):
    import fitz

    pdf = fitz.open()
    page = pdf.new_page(width=600, height=400)
    page.insert_text((40,80), "Official campus notice. Library opens Monday at nine.", fontsize=18)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2,2))
    if format == "pdf":
        content = pdf.tobytes()
    elif format == "scanned.pdf":
        scanned = fitz.open()
        scanned.new_page(width=600,height=400).insert_image(page.rect,stream=pixmap.tobytes("png"))
        content = scanned.tobytes()
        scanned.close()
    else:
        content = pixmap.tobytes("jpeg" if format == "jpg" else "png")
    pdf.close()
    ingest = Ingestion(env.service,env.store)
    job = ingest.receive("official."+format,content,ImportOptions(kind="document"))
    if format != "pdf":
        assert job.status == "queued"
        ingest.process(job)
        assert job.status == "preview" and job.preview["ocr"]
        with pytest.raises(ValueError,match="OCR"):
            ingest.confirm(job)
        ingest.preview(job,ocr_reviewed=True)
    assert job.preview["valid_count"] and not job.preview["error_count"]
    assert not job.preview.get("schema_mismatch")
    publish(env,job)
    doc = env.db.scalar(select(Document).where(Document.source_id==job.source_id))
    texts = env.db.scalars(select(DocumentChunk.text).where(DocumentChunk.document_id==doc.id)).all()
    assert "Library" in " ".join(texts)
