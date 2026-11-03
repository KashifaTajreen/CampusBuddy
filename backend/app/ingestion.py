"""Idempotent ingestion with persistent previews and a durable database queue."""

import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, or_
from backend.app.models import (
    Source,
    CampusRecord,
    Document,
    DocumentChunk,
    IngestionJob,
    AuditLog,
    uid,
    utcnow,
)
from backend.app.schemas import DOMAIN_SCHEMAS, ImportOptions
from backend.app.extraction import PDF_KINDS, extract_records
from backend.app.retrieval import extract_pages, chunks, embeddings
from backend.app.storage import ObjectStore
from backend.app.auth import resolve_actor
from backend.app.services import CampusService

MAX_BYTES = 15 * 1024 * 1024
ALIASES = {
    "item name": "name",
    "item": "name",
    "cost": "price",
    "faculty": "faculty_code",
    "faculty id": "faculty_code",
    "start time": "start_time",
    "end time": "end_time",
    "start date": "effective_from",
    "end date": "effective_to",
    "office location": "office",
    "qualifications": "qualification",
    "highest qualification": "qualification",
    "degree": "qualification",
    "years of experience": "experience",
    "research": "research_areas",
    "research area": "research_areas",
    "research areas": "research_areas",
    "research interests": "research_areas",
    "biography": "bio",
    "about": "bio",
    "profile": "bio",
    "subjects taught": "subjects",
    "estimated protein": "protein",
    "estimated calories": "calories",
    "canteen location": "location",
    "availability": "available",
    "veg": "vegetarian",
    "day of week": "day",
}


def read_table(filename, content):
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(io.BytesIO(content), keep_default_na=False)
    elif suffix == ".xlsx":
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(e.file_size for e in archive.infolist()) > 80 * 1024 * 1024:
                raise ValueError("Spreadsheet expands beyond the safe size limit.")
        frame = pd.read_excel(io.BytesIO(content), keep_default_na=False)
    elif suffix == ".json":
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError("JSON input must be a list of records.")
        frame = pd.DataFrame(data).fillna("")
    else:
        raise ValueError("Use CSV, XLSX or a JSON list.")
    if not 1 <= len(frame) <= 10000:
        raise ValueError("Import between 1 and 10,000 records.")
    if len(frame.columns) > 100:
        raise ValueError("A spreadsheet may have at most 100 columns.")
    return frame


def detect_mapping(columns, kind):
    fields = DOMAIN_SCHEMAS[kind].model_fields
    mapping = {}
    for col in columns:
        normal = str(col).strip().lower().replace("_", " ")
        name = ALIASES.get(normal, normal.replace(" ", "_"))
        if name in fields:
            mapping[name] = str(col)
    return mapping


def normalize(value, field):
    if field == "day":
        days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        v = str(value).lower()
        if v in days:
            return days.index(v)
        if v in [d[:3] for d in days]:
            return [d[:3] for d in days].index(v)
    if field in ("start_time", "end_time"):
        for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I %p"):
            try:
                return datetime.strptime(str(value), fmt).strftime("%H:%M:%S")
            except ValueError:
                pass
    if field in ("vegetarian", "available", "nutrition_verified", "unread", "cancelled"):
        v = str(value).lower()
        if v in ("yes", "true", "1", "1.0", "veg", "vegetarian", "available"):
            return True
        if v in ("no", "false", "0", "0.0", "non-veg", "non-vegetarian", "unavailable"):
            return False
    if field in ("published", "expires", "effective_from", "effective_to", "date"):
        return str(value)[:10]
    return value


def validate_records(frame, kind, mapping, service, department):
    schema = DOMAIN_SCHEMAS[kind]
    errors = []
    rows = []
    for index, raw in frame.iterrows():
        try:
            data = {
                field: normalize(raw[col], field)
                for field, col in mapping.items()
                if col in frame.columns and field in schema.model_fields and str(raw[col]) != ""
            }
            data.setdefault("department", department)
            if kind == "emails" and not data.get("extracted_dates"):
                data["extracted_dates"] = sorted(
                    set(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", str(data.get("body", ""))))
                )
            row = schema.model_validate(data).model_dump(mode="json")
            if kind not in ("emails",):
                service.actor.check_department_write(row.get("department", department))
            if kind == "timetable" and not service.faculty(code=row["faculty_code"]):
                raise ValueError("Faculty code was not found. Import faculty first, then map faculty_code.")
            rows.append(row)
        except (ValidationError, ValueError) as exc:
            msg = (
                "; ".join(".".join(str(x) for x in e["loc"]) + ": " + e["msg"] for e in exc.errors())
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            errors.append(dict(row=int(index) + 2, message=msg))
    return rows, errors


def natural_key(kind, row, owner=None):
    keys = {
        "faculty": ["code"],
        "timetable": [
            "department",
            "semester",
            "section",
            "day",
            "start_time",
            "subject",
            "effective_from",
            "effective_to",
        ],
        "menu": ["name", "location"],
        "events": ["title", "start", "location"],
        "emails": ["sender", "subject", "received"],
        "exceptions": ["timetable_id", "date"],
    }
    base = {k: row.get(k) for k in keys[kind]}
    if owner:
        base["owner"] = owner
    return hashlib.sha256(json.dumps(base, sort_keys=True).encode()).hexdigest()


class Ingestion:
    def __init__(self, service, store=None):
        self.c = service
        self.store = store or ObjectStore()

    def job(self, job_id):
        a = self.c.actor
        job = self.c.db.scalar(
            select(IngestionJob).where(
                IngestionJob.id == job_id,
                IngestionJob.tenant_id == a.tenant_id,
                IngestionJob.requested_by == a.user_id,
            )
        )
        if not job:
            raise HTTPException(404, "Import job not found.")
        return job

    def receive(self, filename, content, options: ImportOptions):
        c = self.c
        a = c.actor
        if options.kind != "emails":
            a.check_department_write(options.department)
        elif options.department not in ("All", a.department):
            raise HTTPException(403, "Import email only into your own department.")
        if not content or len(content) > MAX_BYTES:
            raise ValueError("Choose a nonempty file smaller than 15 MB.")
        filename = Path(filename).name[:255]
        allowed = (
            {".pdf", ".txt", ".md"}
            if options.kind == "document"
            else {".csv", ".xlsx", ".json", ".eml"}
            if options.kind == "emails"
            else {".csv", ".xlsx", ".json", ".pdf"}
            if options.kind in PDF_KINDS
            else {".csv", ".xlsx", ".json"}
        )
        if Path(filename).suffix.lower() not in allowed:
            raise ValueError("Unsupported file type for this import.")
        checksum = hashlib.sha256(content).hexdigest()
        existing = c.db.scalar(
            select(Source).where(
                Source.tenant_id == a.tenant_id,
                Source.checksum == checksum,
                Source.kind == options.kind,
                Source.uploaded_by == a.user_id,
            )
        )
        if existing:
            job = c.db.scalar(
                select(IngestionJob).where(
                    IngestionJob.source_id == existing.id, IngestionJob.tenant_id == a.tenant_id
                )
            )
            if job:
                return job
        sid = uid()
        key = f"{a.tenant_id}/{sid}/{checksum}"
        self.store.put(key, content)
        source = Source(
            id=sid,
            tenant_id=a.tenant_id,
            filename=filename,
            kind=options.kind,
            checksum=checksum,
            storage_key=key,
            uploaded_by=a.user_id,
            owner_id=a.user_id if options.kind == "emails" else None,
            department=options.department,
            demo=options.demo,
        )
        c.db.add(source)
        c.db.flush()
        job = IngestionJob(
            tenant_id=a.tenant_id,
            source_id=sid,
            requested_by=a.user_id,
            department=options.department,
            options=options.model_dump(mode="json"),
            status="received",
        )
        c.db.add(job)
        c.db.flush()
        self.preview(job)
        c.db.add(
            AuditLog(
                tenant_id=a.tenant_id,
                user_id=a.user_id,
                action="ingestion.received",
                details={"job_id": job.id, "kind": options.kind, "bytes": len(content)},
            )
        )
        return job

    def preview(self, job, mapping=None):
        if job.status in ("queued", "processing", "completed"):
            return job
        source = self.c.source(job.source_id)
        content = self.store.get(source.storage_key)
        options = ImportOptions.model_validate(job.options)
        extracted = None
        if mapping is not None:
            options.mapping = mapping
            job.options = options.model_dump(mode="json")
        if options.kind == "document":
            # Full PDF extraction is delegated to the worker; only size/type metadata is previewed here.
            job.preview = dict(
                filename=source.filename,
                bytes=len(content),
                message="The worker will extract pages, index passages and retain the original after confirmation.",
                columns=[],
                errors=[],
            )
        else:
            if source.filename.lower().endswith(".eml"):
                from email import policy
                from email.parser import BytesParser
                from email.utils import parsedate_to_datetime

                message = BytesParser(policy=policy.default).parsebytes(content)
                body = message.get_body(preferencelist=("plain",)) if message.is_multipart() else message
                received = (
                    parsedate_to_datetime(message["Date"]).isoformat()
                    if message.get("Date")
                    else self.c.clock().isoformat()
                )
                content = json.dumps(
                    [
                        dict(
                            sender=str(message.get("From", "Unknown")),
                            subject=str(message.get("Subject", "Untitled")),
                            body=body.get_content() if body else "",
                            received=received,
                            unread=True,
                        )
                    ]
                ).encode()
                filename = "email.json"
            elif source.filename.lower().endswith(".pdf"):
                # The model is not deterministic, so it reads the PDF once. Mapping changes reuse these rows.
                extracted = job.preview.get("extracted") or extract_records(
                    source.filename, content, options.kind, self.c
                )
                content = json.dumps(extracted).encode()
                filename = "extracted.json"
            else:
                filename = source.filename
            frame = read_table(filename, content)
            selected = options.mapping or detect_mapping(frame.columns, options.kind)
            options.mapping = selected
            job.options = options.model_dump(mode="json")
            rows, errors = validate_records(frame, options.kind, selected, self.c, options.department)
            job.preview = dict(
                columns=[str(x) for x in frame.columns],
                fields=[
                    dict(name=name, required=field.is_required())
                    for name, field in DOMAIN_SCHEMAS[options.kind].model_fields.items()
                ],
                mapping=selected,
                records=rows[:10],
                raw=frame.head(5).astype(str).to_dict(orient="records"),
                valid_count=len(rows),
                errors=errors[:100],
                error_count=len(errors),
            )
            if extracted is not None:
                job.preview["extracted"] = extracted
        job.status = "preview"
        job.error = None
        self.c.db.flush()
        return job

    def confirm(self, job):
        if job.status == "completed":
            return job
        if job.status != "preview" or job.preview.get("error_count", 0):
            raise ValueError("Fix mapping and validation errors before confirming this import.")
        job.status = "queued"
        job.error = None
        self.c.db.add(
            AuditLog(
                tenant_id=self.c.actor.tenant_id,
                user_id=self.c.actor.user_id,
                action="ingestion.confirmed",
                details={"job_id": job.id},
            )
        )
        return job

    def process(self, job):
        options = ImportOptions.model_validate(job.options)
        c = self.c
        if options.kind != "emails":
            c.actor.check_department_write(options.department)
        source = c.source(job.source_id)
        content = self.store.get(source.storage_key)
        if options.kind == "document":
            pages = extract_pages(source.filename, content)
            parts = chunks(pages)
            title = options.title or source.filename
            # Headings go into the embedding so each passage keeps its document context.
            vectors, model = embeddings([f"{title} > {section}\n{t}" for _, section, t in parts])
            existing = c.db.scalar(c.scope(Document).where(Document.source_id == source.id))
            if existing:
                if not job.options.get("reindex"):
                    return len(parts)
                from sqlalchemy import delete

                c.db.execute(
                    delete(DocumentChunk).where(
                        DocumentChunk.tenant_id == c.actor.tenant_id, DocumentChunk.document_id == existing.id
                    )
                )
                for (page, section, text), vector in zip(parts, vectors):
                    c.db.add(
                        DocumentChunk(
                            tenant_id=c.actor.tenant_id,
                            document_id=existing.id,
                            page=page,
                            section=section,
                            text=text,
                            embedding=vector,
                            embedding_model=model,
                        )
                    )
                return len(parts)
            previous = None
            if options.supersedes_id:
                previous = c.db.scalar(c.scope(Document).where(Document.id == options.supersedes_id))
                if not previous:
                    raise ValueError("The previous document is unavailable.")
            document = Document(
                tenant_id=c.actor.tenant_id,
                title=options.title or source.filename,
                category=options.category,
                department=options.department,
                published=str(options.published or c.clock().date()),
                expires=str(options.expires) if options.expires else None,
                topic=options.topic,
                claim=options.claim,
                source_id=source.id,
                version=previous.version + 1 if previous else 1,
                supersedes_id=previous.id if previous else None,
            )
            c.db.add(document)
            c.db.flush()
            for (page, section, text), vector in zip(parts, vectors):
                c.db.add(
                    DocumentChunk(
                        tenant_id=c.actor.tenant_id,
                        document_id=document.id,
                        page=page,
                        section=section,
                        text=text,
                        embedding=vector,
                        embedding_model=model,
                    )
                )
            count = len(parts)
        else:
            if source.filename.lower().endswith(".eml"):
                # EML was fully normalized and validated during preview; a single-message import fits the preview.
                rows = job.preview.get("records", [])
                errors = []
            else:
                if source.filename.lower().endswith(".pdf"):
                    # Import exactly the rows the administrator reviewed, without asking the model again.
                    extracted = job.preview.get("extracted")
                    if not extracted:
                        raise ValueError("Preview this PDF again before importing it.")
                    frame = read_table("extracted.json", json.dumps(extracted).encode())
                else:
                    frame = read_table(source.filename, content)
                rows, errors = validate_records(frame, options.kind, options.mapping, c, options.department)
            if errors:
                raise ValueError("Source validation changed. Preview and validate again.")
            for row in rows:
                owner = c.actor.user_id if options.kind == "emails" else None
                key = natural_key(options.kind, row, owner)
                record = c.db.scalar(
                    select(CampusRecord).where(
                        CampusRecord.tenant_id == c.actor.tenant_id,
                        CampusRecord.kind == options.kind,
                        CampusRecord.natural_key == key,
                    )
                )
                if record:
                    if record.owner_id != owner:
                        raise ValueError("The record owner does not match this import.")
                    # Do not let department admins overwrite another department via a colliding code.
                    if c.actor.role == "department_admin" and record.department != c.actor.department:
                        raise HTTPException(403, "That record belongs to another department.")
                    record.payload = row
                    record.source_id = source.id
                    record.department = row.get("department", options.department)
                else:
                    c.db.add(
                        CampusRecord(
                            tenant_id=c.actor.tenant_id,
                            kind=options.kind,
                            natural_key=key,
                            department=row.get("department", options.department),
                            owner_id=owner,
                            source_id=source.id,
                            payload=row,
                        )
                    )
            count = len(rows)
        c.db.add(
            AuditLog(
                tenant_id=c.actor.tenant_id,
                user_id=c.actor.user_id,
                action="ingestion.completed",
                details={"job_id": job.id, "count": count},
            )
        )
        return count


def work_once(session_factory, store=None):
    with session_factory() as db:
        # Worker uses its own trusted database role. SKIP LOCKED supports multiple PostgreSQL workers.
        stale = utcnow() - timedelta(minutes=10)
        stmt = (
            select(IngestionJob)
            .where(
                or_(
                    IngestionJob.status == "queued",
                    (IngestionJob.status == "processing") & (IngestionJob.claimed_at < stale),
                )
            )
            .order_by(IngestionJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = db.scalar(stmt)
        if not job:
            return False
        job.status = "processing"
        job.claimed_at = utcnow()
        job.attempts += 1
        job_id = job.id
        db.commit()
    with session_factory() as db:
        job = db.scalar(select(IngestionJob).where(IngestionJob.id == job_id).with_for_update())
        try:
            actor = resolve_actor(db, job.requested_by, job.tenant_id)
            # Process + state transition commit atomically. A parsing error rolls back all domain writes.
            count = Ingestion(CampusService(db, actor), store).process(job)
            job.status = "completed"
            job.error = None
            job.preview = {**job.preview, "imported_count": count}
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(IngestionJob, job_id)
            job.status = "failed"
            job.error = (
                str(exc.detail if isinstance(exc, HTTPException) else exc)[:400]
                if isinstance(exc, (ValueError, HTTPException))
                else "Import failed safely. Check file contents and worker configuration, then retry."
            )
            db.commit()
    return True
