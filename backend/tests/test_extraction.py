import json
import sys
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from backend.app import extraction, retrieval
from backend.app.ai import NullProvider, ProviderError
from backend.app.config import settings
from backend.app.ingestion import Ingestion
from backend.app.main import job_json
from backend.app.models import CampusRecord
from backend.app.retrieval import extract_pages
from backend.app.schemas import ImportOptions

PROFILE = dict(
    code="PDF-1",
    name="Dr. Leela Pillai",
    department="AIML",
    designation="Professor",
    subjects="Deep Learning",
    office="Block C, Room 12",
    office_hours="Mon, 10:00-11:00",
    qualification="PhD",
    research_areas="Computer vision",
)


def pdf(text="Dr. Leela Pillai teaches Deep Learning."):
    import fitz

    document = fitz.open()
    document.new_page().insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


class Reader:
    """A scripted model that counts how often it is asked to read a document."""

    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls = []

    def chat(self, messages, tools=None, response_format=None):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return {"role": "assistant", "content": self.content}


def use(monkeypatch, provider):
    monkeypatch.setattr(extraction, "get_provider", lambda: provider)
    return provider


def records(*rows):
    return json.dumps({"records": list(rows)})


def receive(env, kind="faculty", filename="staff.pdf", department="AIML"):
    return Ingestion(env.service, env.store).receive(
        filename, pdf(), ImportOptions(kind=kind, department=department)
    )


def faculty_named(env, name):
    return env.db.scalar(
        select(CampusRecord).where(
            CampusRecord.kind == "faculty", CampusRecord.payload["name"].as_string() == name
        )
    )


def test_pdf_preview_then_import_uses_the_reviewed_rows(env, monkeypatch):
    reader = use(monkeypatch, Reader(records(PROFILE)))
    job = receive(env)
    assert job.status == "preview"
    assert job.preview["valid_count"] == 1
    assert job.preview["error_count"] == 0
    ingestion = Ingestion(env.service, env.store)
    ingestion.confirm(job)
    assert ingestion.process(job) == 1
    env.db.flush()
    stored = faculty_named(env, "Dr. Leela Pillai")
    assert stored.payload["research_areas"] == "Computer vision"
    assert stored.source_id == job.source_id
    assert len(reader.calls) == 1


def test_model_is_not_asked_again_after_a_mapping_change(env, monkeypatch):
    reader = use(monkeypatch, Reader(records(PROFILE)))
    job = receive(env)
    ingestion = Ingestion(env.service, env.store)
    ingestion.preview(job, mapping=job.options["mapping"])
    assert job.preview["valid_count"] == 1
    assert job.preview["extracted"] == [PROFILE]
    assert len(reader.calls) == 1


def test_import_needs_reviewed_rows_and_never_calls_the_model(env, monkeypatch):
    reader = use(monkeypatch, Reader(records(PROFILE)))
    job = receive(env)
    job.preview = {k: v for k, v in job.preview.items() if k != "extracted"}
    with pytest.raises(ValueError, match="Preview this PDF again"):
        Ingestion(env.service, env.store).process(job)
    assert len(reader.calls) == 1


def test_invalid_rows_are_shown_for_review_and_block_confirmation(env, monkeypatch):
    use(monkeypatch, Reader(records({**PROFILE, "name": ""})))
    job = receive(env)
    assert job.preview["error_count"] == 1
    with pytest.raises(ValueError):
        Ingestion(env.service, env.store).confirm(job)
    assert faculty_named(env, "") is None


def test_job_json_hides_the_full_extraction(env, monkeypatch):
    use(monkeypatch, Reader(records(PROFILE)))
    output = job_json(receive(env))
    assert "extracted" not in output["preview"]
    assert output["preview"]["records"]


def test_no_local_model_explains_the_alternative(env, monkeypatch):
    use(monkeypatch, NullProvider())
    with pytest.raises(ValueError, match="needs the local model"):
        receive(env)


def test_model_server_failure_is_reported(env, monkeypatch):
    use(monkeypatch, Reader(error=ProviderError("down")))
    with pytest.raises(ValueError, match="local model is unavailable"):
        receive(env)


@pytest.mark.parametrize("content", [records(), '{"records": "none"}'])
def test_empty_result_is_reported(env, monkeypatch, content):
    use(monkeypatch, Reader(content))
    with pytest.raises(ValueError, match="No records were found"):
        receive(env)


@pytest.mark.parametrize("content", ["not json", "{}", ""])
def test_unreadable_model_output_is_reported(env, monkeypatch, content):
    use(monkeypatch, Reader(content))
    with pytest.raises(ValueError, match="could not read this PDF"):
        receive(env)


def test_timetable_prompt_lists_directory_and_fills_validity_dates(env, monkeypatch):
    row = dict(
        department="AIML",
        semester=3,
        section="C",
        subject="Deep Learning",
        faculty_code="AIML-1",
        room="Z-1",
        day=5,
        start_time="09:00",
        end_time="10:00",
    )
    reader = use(monkeypatch, Reader(records(row)))
    job = receive(env, kind="timetable")
    assert "AIML-1" in reader.calls[0][0]["content"]
    assert job.preview["error_count"] == 0
    assert job.preview["extracted"][0]["effective_from"] == "2026-10-28"
    assert job.preview["extracted"][0]["effective_to"] == "2027-04-26"


def test_document_text_is_marked_untrusted(env, monkeypatch):
    reader = use(monkeypatch, Reader(records(PROFILE)))
    receive(env)
    assert "untrusted" in reader.calls[0][0]["content"]


def test_pdf_rejected_for_kinds_without_extraction(env, monkeypatch):
    use(monkeypatch, Reader(records(PROFILE)))
    with pytest.raises(ValueError, match="Unsupported file type"):
        receive(env, kind="exceptions")


def test_pdf_import_keeps_department_boundary(env, student, monkeypatch):
    reader = use(monkeypatch, Reader(records(PROFILE)))
    with pytest.raises(Exception, match="administrator"):
        Ingestion(student, env.store).receive("staff.pdf", pdf(), ImportOptions(kind="faculty"))
    assert reader.calls == []


def test_csv_import_still_avoids_the_model(env, monkeypatch):
    reader = use(monkeypatch, Reader(records(PROFILE)))
    content = (
        "code,name,department,subjects,office,office_hours,highest qualification,research interests\n"
        "CSV-1,Dr. Asha,AIML,Statistics,Room 1,Mon,PhD,Bayesian methods\n"
    ).encode()
    job = Ingestion(env.service, env.store).receive(
        "staff.csv", content, ImportOptions(kind="faculty", department="AIML")
    )
    assert reader.calls == []
    assert job.preview["records"][0]["qualification"] == "PhD"
    assert job.preview["records"][0]["research_areas"] == "Bayesian methods"


def fake_docling(monkeypatch, pages=None, error=None):
    class Document:
        def __init__(self):
            self.pages = pages or {}

        def export_to_markdown(self, page_no):
            return self.pages[page_no]

    class Converter:
        seen = []

        def convert(self, stream):
            self.seen.append(stream.name)
            if error:
                raise error
            return SimpleNamespace(document=Document())

    module = SimpleNamespace(DocumentStream=lambda name, stream: SimpleNamespace(name=name, stream=stream))
    monkeypatch.setitem(sys.modules, "docling", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "docling.datamodel", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "docling.datamodel.base_models", module)
    monkeypatch.setattr(retrieval, "docling_converter", Converter)
    monkeypatch.setattr(settings(), "document_parser", "docling")
    return Converter


def test_docling_returns_markdown_per_page(monkeypatch):
    converter = fake_docling(monkeypatch, {2: "| Day | Subject |", 1: "# Timetable"})
    assert extract_pages("timetable.pdf", pdf()) == [(1, "# Timetable"), (2, "| Day | Subject |")]
    assert converter.seen == ["timetable.pdf"]


def test_docling_failure_falls_back_to_text_layer(monkeypatch):
    fake_docling(monkeypatch, error=RuntimeError("conversion failed"))
    assert "Leela" in extract_pages("staff.pdf", pdf())[0][1]


def test_docling_blank_output_falls_back_to_text_layer(monkeypatch):
    fake_docling(monkeypatch, {1: "   "})
    assert "Leela" in extract_pages("staff.pdf", pdf())[0][1]


def test_missing_docling_package_falls_back_to_text_layer(monkeypatch):
    monkeypatch.setattr(settings(), "document_parser", "docling")
    monkeypatch.setitem(sys.modules, "docling.datamodel.base_models", None)
    assert "Leela" in extract_pages("staff.pdf", pdf())[0][1]


def test_docling_is_off_by_default(monkeypatch):
    converter = fake_docling(monkeypatch, {1: "docling text"})
    monkeypatch.setattr(settings(), "document_parser", "pymupdf")
    assert "Leela" in extract_pages("staff.pdf", pdf())[0][1]
    assert converter.seen == []


def test_docling_keeps_page_and_password_checks(monkeypatch):
    import fitz

    converter = fake_docling(monkeypatch, {1: "docling text"})
    document = fitz.open()
    document.new_page()
    locked = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    document.close()
    with pytest.raises(ValueError, match="Unlock"):
        extract_pages("locked.pdf", locked)
    assert converter.seen == []
