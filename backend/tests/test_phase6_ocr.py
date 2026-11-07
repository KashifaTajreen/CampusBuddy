"""Real raster OCR plus durable preview/review/publication security checks."""
import io
from pathlib import Path
import subprocess
import pytest
import pymupdf
from PIL import Image
from fastapi import HTTPException
from sqlalchemy import select
from backend.app import ingestion, parsing, ocr
from backend.app.config import ROOT, Settings
from backend.app.ingestion import Ingestion, work_once
from backend.app.models import Source, CampusRecord
from backend.app.schemas import ImportOptions
from backend.tests.conftest import publish
from scripts.evaluate_ocr import printed_menu, ENGLISH, normalized


@pytest.fixture
def raster():
    stream = io.BytesIO()
    printed_menu().save(stream, format="PNG")
    return stream.getvalue()


@pytest.fixture
def local_ocr(monkeypatch):
    directory = ROOT / ".scratch/tessdata"
    if not ocr.readiness(directory)["ready"]:
        pytest.skip("Local English OCR pack is absent; run approved scripts.setup_local_assets --ocr.")
    config = Settings(_env_file=None, ocr_enabled=True, ocr_tessdata_path=str(directory))
    monkeypatch.setattr(ingestion, "settings", lambda: config)
    from backend.app import config as config_module
    monkeypatch.setattr(config_module, "settings", lambda: config)
    return directory


def test_real_image_extracts_names_and_prices(local_ocr, raster):
    parsed = parsing.parse_file("synthetic.png", raster, force_ocr=True)
    rows = {normalized(r["name"]): r for r in parsed["rows"]}
    assert all(rows[normalized(name)]["price"] == str(price) for name, price in ENGLISH)
    assert parsed["ocr_pages"] == [1] and parsed["needs_review"]
    assert not parsed["confidence_available"]


def test_selectable_header_with_scanned_body_is_not_missed(local_ocr, raster):
    with pymupdf.open() as document:
        page = document.new_page(width=750, height=550)
        page.insert_text((30, 25), "Selectable header")
        page.insert_image(pymupdf.Rect(0, 50, 750, 550), stream=raster)
        data = document.tobytes()
    assert parsing.parse_file("mixed-page.pdf", data, probe=True)["needs_ocr"]
    assert parsing.parse_file("mixed-page.pdf", data, force_ocr=True)["ocr_pages"] == [1]


def test_restored_image_requires_new_worker_preview_and_review(env, local_ocr, raster):
    from backend.app.files import Files
    pipeline = Ingestion(env.service, env.store)
    job = pipeline.receive("restore.png", raster, ImportOptions(kind="menu", menu_location="Main", menu_vegetarian=True))
    env.db.commit()
    work_once(env.factory, env.store)
    env.db.expire_all()
    pipeline.preview(job, ocr_reviewed=True)
    publish(env, job)
    Files(env.service, env.store).change(job.source_id, "delete")
    Files(env.service, env.store).change(job.source_id, "restore")
    latest = env.db.scalar(select(ingestion.IngestionJob).where(ingestion.IngestionJob.source_id == job.source_id,
        ingestion.IngestionJob.status == "queued"))
    assert latest and latest.options["ocr_extract_pending"] and not latest.options["ocr_reviewed"]


def test_worker_ocr_is_draft_then_requires_review_before_independent_publication(env, local_ocr, raster):
    pipeline = Ingestion(env.service, env.store)
    job = pipeline.receive("worker-menu.png", raster,
        ImportOptions(kind="menu", menu_location="Test canteen", menu_vegetarian=True))
    assert job.status == "queued" and job.options["ocr_extract_pending"]
    source = env.db.get(Source, job.source_id)
    assert source.publication_status == "draft"
    assert not list(env.db.scalars(select(CampusRecord).where(CampusRecord.source_id == source.id)))
    env.db.commit()
    assert work_once(env.factory, env.store)
    env.db.expire_all()
    assert job.status == "preview" and job.preview["valid_count"] == 3
    with pytest.raises(ValueError, match="review"):
        pipeline.confirm(job)
    pipeline.preview(job, ocr_reviewed=True)
    assert job.options["ocr_reviewed"]
    changed = [{**r, "price": "31"} for r in job.preview["editable_rows"]]
    pipeline.preview(job, rows=changed)
    assert not job.options["ocr_reviewed"]
    with pytest.raises(ValueError, match="review"):
        pipeline.confirm(job)
    pipeline.preview(job, ocr_reviewed=True)
    publish(env, job)
    assert source.publication_status == "published"
    assert len(list(env.db.scalars(select(CampusRecord).where(CampusRecord.source_id == source.id)))) == 3


def test_ocr_failure_retry_keeps_draft_without_partial_rows(env, local_ocr, raster, monkeypatch):
    pipeline = Ingestion(env.service, env.store)
    job = pipeline.receive("retry-menu.png", raster, ImportOptions(kind="menu", menu_location="Main", menu_vegetarian=True))
    env.db.commit()
    real = ingestion.parse_file
    def crashed(*_args, **_kwargs):
        raise RuntimeError("synthetic OCR process crash")
    monkeypatch.setattr(ingestion, "parse_file", crashed)
    work_once(env.factory, env.store)
    env.db.expire_all()
    assert job.status == "failed" and job.attempts == 1
    assert env.db.get(Source, job.source_id).publication_status == "draft"
    assert not list(env.db.scalars(select(CampusRecord).where(CampusRecord.source_id == job.source_id)))
    monkeypatch.setattr(ingestion, "parse_file", real)
    pipeline.preview(job)
    env.db.commit()
    work_once(env.factory, env.store)
    env.db.expire_all()
    assert job.status == "preview" and job.attempts == 2 and not job.options["ocr_reviewed"]


def test_student_cannot_queue_shared_ocr(env, student, raster):
    with pytest.raises(HTTPException) as exc:
        Ingestion(student, env.store).receive("denied.png", raster, ImportOptions(kind="menu"))
    assert exc.value.status_code == 403


def test_uncertain_prices_stay_invalid_and_preserve_date_meal():
    rows, issues = ocr.menu_rows("2026-11-02\nBreakfast\nIdli Rs 30-40\nTea 12 15\nDosa\nVeg Rice Rs 60")
    assert [r["price"] for r in rows[:3]] == ["", "", ""]
    assert all(r["served_on"] == "2026-11-02" and r["meal"] == "Breakfast" for r in rows)
    assert rows[-1]["vegetarian"] is True and len(issues) == 3


def test_oversized_and_mislabeled_images_rejected():
    stream = io.BytesIO()
    Image.new("L", (5000, 4001)).save(stream, format="PNG")
    with pytest.raises(ValueError, match="oversized|limit"):
        parsing.parse_inline("large.png", stream.getvalue(), probe=True)
    with pytest.raises(ValueError):
        parsing.parse_inline("evil.webp", b"<svg onload='bad()'/>", probe=True)


def test_parser_timeout_is_actionable(monkeypatch, raster):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("parser", 30)
    monkeypatch.setattr(parsing.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="30-second"):
        parsing.parse_file("timeout.png", raster)


def test_upload_backpressure_rejects_without_storing_file(env, monkeypatch):
    monkeypatch.setattr(ingestion, "settings", lambda: Settings(_env_file=None, ingestion_queue_limit=1))
    first = Ingestion(env.service, env.store).receive("first.txt", b"One unique campus notice.", ImportOptions(kind="document"))
    Ingestion(env.service, env.store).confirm(first)
    before = set(env.store.items)
    with pytest.raises(HTTPException) as exc:
        Ingestion(env.service, env.store).receive("second.txt", b"Another distinct notice.", ImportOptions(kind="document"))
    assert exc.value.status_code == 429 and set(env.store.items) == before


def test_missing_pack_readiness_is_explicit(tmp_path):
    assert not ocr.readiness(tmp_path, "eng+kan")["ready"]
    assert not ocr.readiness(Path("."), "../outside")["ready"]
