"""Events-only acceptance with synthetic content and disposable databases."""

import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd
import pytest
import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select, func

from backend.app import ingestion as ingestion_module, retrieval
from backend.app.auth import resolve_actor
from backend.app.event_data import OfficialEventData, EventReviewRequest
from backend.app.event_pipeline import EventPipeline
from backend.app.ingestion import Ingestion, work_once
from backend.app.models import Source, Document, DocumentChunk, CampusRecord, Membership
from backend.app.parsing import parse_file
from backend.app.retrieval import Retrieval
from backend.app.router import Router
from backend.app.schemas import ImportOptions
from backend.app.services import CampusService
from backend.tests.test_auth_api import client as client, key as key, token

STUDENT = "00000000-0000-4000-8000-000000002201"
TEXT = """Title: Lantern Arts Fair
Category: Cultural
Description: A synthetic campus art and music activity for local verification only.
Event date: 20 November 2026
Time: 9 AM - 5 PM
Registration deadline: 14 November 2026
Venue: Test Auditorium
Organizer: Fictional Arts Club
Registration fee: Rs.30
Registration URL: https://example.invalid/lantern
Eligibility: Second-year and other undergraduate students may participate.
Team size: 2 to 4 members
Rules: Bring original artwork. Do not copy other participants.
Prizes: Art materials and certificates.
"""


def fixtures(directory):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "event.txt").write_text(TEXT, encoding="utf-8")
    font_path = next((p for p in (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ) if p.is_file()), None)
    font = ImageFont.truetype(str(font_path), 25) if font_path else ImageFont.load_default(size=25)
    image = Image.new("RGB", (1400, 1000), (10, 18, 35))
    ImageDraw.Draw(image).multiline_text((40, 35), TEXT, font=font, fill="white", spacing=15)
    for suffix in ("png", "jpeg", "webp"):
        image.save(directory / ("poster." + suffix))
    pdf = fitz.open()
    page = pdf.new_page(width=900, height=700)
    page.insert_textbox(fitz.Rect(30, 30, 870, 670), TEXT, fontsize=12)
    pdf.save(directory / "digital.pdf")
    pdf.close()
    pdf = fitz.open()
    page = pdf.new_page(width=1400, height=1000)
    page.insert_image(page.rect, stream=(directory / "poster.png").read_bytes())
    pdf.save(directory / "scanned.pdf")
    pdf.close()
    with zipfile.ZipFile(directory / "event.docx", "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        z.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + "".join("<w:p><w:r><w:t>" + escape(line) + "</w:t></w:r></w:p>" for line in TEXT.splitlines())
            + "</w:body></w:document>",
        )
    frame = pd.DataFrame(
        [
            dict(
                title="Lantern Arts Fair",
                category="Cultural",
                description="Synthetic event list with no announced dates.",
                eligibility="Second-year students may participate.",
                rules="Bring original artwork.",
                registration_fee=30,
                fee_currency="INR",
            )
        ]
    )
    frame.to_csv(directory / "events.csv", index=False)
    frame.to_excel(directory / "events.xlsx", index=False)
    return directory


@pytest.fixture(scope="module")
def event_files(tmp_path_factory):
    return fixtures(tmp_path_factory.mktemp("event-formats"))


@pytest.fixture(scope="session")
def e5():
    from backend.app.config import settings
    from backend.app.semantic import LocalE5

    path = Path(settings().embedding_model_path)
    if not path.is_dir() or not (path / "model_quantized.onnx").is_file():
        pytest.skip("Local E5 ONNX model is not installed; real semantic event indexing cannot be verified.")
    return LocalE5(path)


@pytest.fixture
def event_env(env, monkeypatch, e5):
    assert "pytest" in str(env.engine.url.database)
    monkeypatch.setattr(retrieval, "semantic_model", lambda: e5)
    monkeypatch.setattr(ingestion_module, "ObjectStore", lambda: env.store)
    env.db.add(
        Membership(
            user_id=STUDENT,
            tenant_id=env.tenant,
            role="student",
            department="AIML",
            semester=3,
            section="A",
            name="Synthetic event student",
        )
    )
    env.db.commit()
    return env


def pipeline(env, filename="event.txt", content=None, department="All"):
    ingest = Ingestion(env.service, env.store)
    job = ingest.receive(
        filename,
        content or TEXT.encode(),
        ImportOptions(kind="events", event_workflow=True, department=department, demo=True),
    )
    if job.status == "queued":
        env.db.commit()
        work_once(env.factory, env.store)
        env.db.expire_all()
        job = ingest.job(job.id)
    return ingest, job


def validate_and_publish(env, ingest, job):
    EventPipeline(ingest).save_review(
        job,
        EventReviewRequest(
            events=job.preview["event_rows"],
            pages=job.preview["event_pages"],
            ocr_reviewed=bool(job.preview.get("ocr")),
        ),
    )
    assert not job.preview["error_count"], job.preview["errors"]
    ingest.confirm(job)
    job.options = {**job.options, "event_publish_requested": True}
    env.db.commit()
    assert work_once(env.factory, env.store)
    env.db.expire_all()
    assert job.status == "completed", job.error
    assert env.db.get(Source, job.source_id).publication_status == "published"


@pytest.mark.parametrize(
    "name",
    [
        "event.txt",
        "event.docx",
        "digital.pdf",
        "scanned.pdf",
        "poster.png",
        "poster.jpeg",
        "poster.webp",
        "events.csv",
        "events.xlsx",
    ],
)
def test_actual_format_extraction_uses_event_text_not_tables(event_files, name):
    parsed = parse_file(name, (event_files / name).read_bytes(), import_kind="events")
    text = "\n".join(t for _, t in parsed["pages"])
    assert "lantern" in text.lower(), (name, parsed.get("warnings"))
    assert parsed.get("detected_kind") != "menu"
    if name.startswith("poster") or name == "scanned.pdf":
        assert parsed["needs_review"]
    from backend.app.event_extraction import suggest
    rows,_=suggest(parsed['pages'],parsed.get('rows',[]))
    assert rows[0]['title']=='Lantern Arts Fair'
    assert rows[0]['registration_fee']==30
    if name not in ('events.csv','events.xlsx'):
        assert rows[0]['start_date']=='2026-11-20'
        assert rows[0]['registration_deadline']=='2026-11-14'


def test_optional_unknowns_do_not_invent_dates_or_deadlines():
    data = OfficialEventData(
        title="Lantern Arts Fair", description="Join a synthetic local arts activity.", location="NA"
    )
    assert (
        data.start
        is data.end
        is data.start_date
        is data.registration_deadline
        is data.location
        is data.registration_fee
        is None
    )
    with pytest.raises(ValueError):
        OfficialEventData(title="Event", description="Content exists but the title is generic.")
    with pytest.raises(ValueError):
        OfficialEventData(
            title="Lantern Arts Fair", description="Content", registration_url="javascript:alert(1)"
        )
    with pytest.raises(ValueError):
        OfficialEventData(
            title="Lantern Arts Fair", description="Content", start_date="2026-11-20", end_date="2026-11-19"
        )
    from backend.app.event_extraction import suggest
    rows,_=suggest([(1,'Lantern Arts Fair\nFind the organizer online at https://example.invalid/club')],[])
    assert rows[0].get('registration_url') is None
    assert rows[0]['external_links']==['https://example.invalid/club']


def test_review_dual_storage_publication_and_idempotency(event_env):
    env = event_env
    ingest, job = pipeline(env)
    assert job.preview["valid_count"] == 1 and not job.preview["event_reviewed"]
    student = CampusService(env.db, resolve_actor(env.db, STUDENT, env.tenant), clock=env.service.clock)
    assert job.source_id not in str(student.event_listing())
    validate_and_publish(env, ingest, job)
    record = env.db.scalar(select(CampusRecord).where(CampusRecord.source_id == job.source_id))
    document = env.db.scalar(select(Document).where(Document.source_id == job.source_id))
    passages = env.db.scalars(select(DocumentChunk).where(DocumentChunk.document_id == document.id)).all()
    assert record.payload["registration_fee"] == 30 and record.payload["fee_currency"] == "INR"
    assert record.payload["registration_deadline"] == "2026-11-14"
    assert record.payload["start_date"] == "2026-11-20"
    assert passages and all(
        p.embedding_model.startswith("onnx-e5-") and len(p.embedding) == 384 for p in passages
    )
    assert all(p.row_data["event_key"] == record.natural_key for p in passages)
    assert "Bring original artwork" in "\n".join(p.text for p in passages)
    source = env.db.get(Source, job.source_id)
    assert source.approved_by == source.published_by == env.user
    assert (
        ingest.receive(
            "event.txt", TEXT.encode(), ImportOptions(kind="events", event_workflow=True, demo=True)
        ).id
        == job.id
    )
    assert (
        env.db.scalar(
            select(func.count()).select_from(CampusRecord).where(CampusRecord.source_id == source.id)
        )
        == 1
    )
    assert (
        env.db.scalar(select(func.count()).select_from(Document).where(Document.source_id == source.id)) == 1
    )
    result = Router(student).ask("Tell me about Lantern Arts Fair event.")
    assert result["records"] and source.id in {s["id"] for s in result["sources"]}


def test_manual_edits_and_index_failure_keep_draft_for_retry(event_env, monkeypatch):
    from backend.app import event_pipeline

    env = event_env
    ingest, job = pipeline(env)
    rows = job.preview["event_rows"]
    rows[0]["location"] = "Reviewed Test Hall"
    EventPipeline(ingest).save_review(job, EventReviewRequest(events=rows, pages=job.preview["event_pages"]))
    ingest.confirm(job)
    job.options = {**job.options, "event_publish_requested": True}
    env.db.commit()
    original = event_pipeline.event_embeddings
    monkeypatch.setattr(
        event_pipeline,
        "event_embeddings",
        lambda _: (_ for _ in ()).throw(RuntimeError("Synthetic indexing failure")),
    )
    assert work_once(env.factory, env.store)
    env.db.expire_all()
    assert job.status == "failed" and "indexing failed" in job.error.lower()
    assert env.db.get(Source, job.source_id).publication_status == "draft"
    assert job.options["event_rows"][0]["location"] == "Reviewed Test Hall"
    assert (
        env.db.scalar(select(func.count()).select_from(Document).where(Document.source_id == job.source_id))
        == 0
    )
    monkeypatch.setattr(event_pipeline, "event_embeddings", original)
    ingest.preview(job)
    validate_and_publish(env, ingest, job)


def test_undated_events_scope_rag_and_missing_facts(event_env):
    env = event_env
    text = "Title: Lantern Arts Fair\nCategory: Cultural\nDescription: A synthetic campus arts activity.\nRules\nBring original artwork and a canvas."
    ingest, job = pipeline(env, content=text.encode(), department="AIML")
    validate_and_publish(env, ingest, job)
    student = CampusService(env.db, resolve_actor(env.db, STUDENT, env.tenant), clock=env.service.clock)
    listed = student.event_listing(status="undated")
    assert len(listed) == 1 and listed[0]["start_date"] is None and listed[0]["event_status"] == "undated"
    outside = CampusService(env.db, resolve_actor(env.db, env.other, env.tenant), clock=env.service.clock)
    assert not outside.event_listing(query="Lantern")
    router = Router(student)
    first = router.ask("What cultural events are happening?")
    assert first["records"] and first["records"][0]["title"] == "Lantern Arts Fair"
    missing = router.ask("What is the registration fee?", first["context"])
    assert "not available" in missing["answer"] and "today" not in missing["answer"]
    rules = router.ask("What are the rules of this event?", first["context"])
    assert rules["kind"] == "passages" and rules["sources"] and "original artwork" in rules["answer"]
    assert job.source_id not in str(student.notices())
    for question in ('कौन से सांस्कृतिक कार्यक्रम हैं?', 'ಸಾಂಸ್ಕೃತಿಕ ಕಾರ್ಯಕ್ರಮಗಳು ಯಾವುವು?'):
        translated=Router(student).ask(question)
        assert translated['kind']=='events' and any(row['title']=='Lantern Arts Fair' for row in translated['records'])
        assert all(row.get('category')=='Cultural' for row in translated['records'])


def test_timed_event_ended_today_is_in_past_filter(event_env):
    env=event_env
    text='Title: Lantern Morning Session\nDescription: Synthetic local activity.\nEvent date: 28 October 2026\nTime: 9 AM - 10 AM\nRegistration deadline: 27 October 2026'
    ingest,job=pipeline(env,content=text.encode())
    validate_and_publish(env,ingest,job)
    student=CampusService(env.db,resolve_actor(env.db,STUDENT,env.tenant),clock=env.service.clock)
    listed=student.event_listing(status='past',query='Lantern')
    assert len(listed)==1 and listed[0]['event_status']=='past' and listed[0]['registration_status']=='closed'


def test_multiple_event_chunks_do_not_mix_records(event_env):
    env = event_env
    raw = [
        dict(
            title="Lantern Arts Fair",
            category="Cultural",
            description="Arts activities.",
            full_description="Lantern Arts Fair. Rules: bring a canvas.",
        ),
        dict(
            title="Orbit Sports Day",
            category="Sports",
            description="Sports activities.",
            full_description="Orbit Sports Day. Rules: bring sports shoes.",
        ),
    ]
    content = json.dumps(raw).encode()
    ingest, job = pipeline(env, "events.json", content)
    validate_and_publish(env, ingest, job)
    records = env.db.scalars(select(CampusRecord).where(CampusRecord.source_id == job.source_id)).all()
    first = next(r for r in records if r.payload["title"] == "Lantern Arts Fair")
    student = CampusService(env.db, resolve_actor(env.db, STUDENT, env.tenant), clock=env.service.clock)
    hits = Retrieval(student).search("Lantern Arts Fair rules", event_id=first.id)["hits"]
    assert hits and all("sports shoes" not in hit["text"] for hit in hits)


def test_direct_api_student_upload_publish_and_review_denied(client, key, event_env):
    env = event_env
    ingest, job = pipeline(env)
    env.db.commit()
    headers = {"Authorization": "Bearer " + token(key, STUDENT)}
    response = client.post(
        "/v1/ingestion/preview",
        headers=headers,
        files={"file": ("student.txt", TEXT.encode(), "text/plain")},
        data={"options": json.dumps({"kind": "events"})},
    )
    assert response.status_code == 403
    assert client.post(f"/v1/ingestion/{job.id}/publish-event", headers=headers).status_code in (403, 404)
    body = {"events": job.preview["event_rows"], "pages": job.preview["event_pages"]}
    assert client.post(f"/v1/ingestion/{job.id}/event-review", headers=headers, json=body).status_code in (
        403,
        404,
    )
