"""Idempotent ingestion with persistent previews and a durable database queue."""

import hashlib
import io
import json
import re
import zipfile
from itertools import islice
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
from backend.app.retrieval import extract_pages, chunks, embeddings
from backend.app.storage import ObjectStore
from backend.app.auth import resolve_actor
from backend.app.services import CampusService
from backend.app.db import serialize_sqlite_writes
from backend.app.parsing import parse_file, detect_kind, FORMATS
from backend.app.config import settings

MAX_BYTES = 15 * 1024 * 1024
ALIASES = {
    "item name": "name",
    "item": "name",
    "cost": "price",
    "faculty": "faculty_name",
    "faculty name": "faculty_name",
    "course name": "subject",
    "course code": "course_code",
    "batch": "lab_batch",
    "faculty id": "faculty_code",
    "start time": "start_time",
    "end time": "end_time",
    "start date": "effective_from",
    "end date": "effective_to",
    "office location": "office",
    "subjects taught": "subjects",
    "estimated protein": "protein",
    "estimated calories": "calories",
    "canteen location": "location",
    "availability": "available",
    "veg": "vegetarian",
    "day of week": "day",
    "lab batch": "lab_batch",
    "venue": "location",
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
        normal = " ".join(str(col).lower().replace("_", " ").split())
        name = ALIASES.get(normal, normal.replace(" ", "_"))
        if kind=='faculty' and normal in ('faculty','faculty name'):
            name='name'
        if kind == "calendar":
            name = {"start date": "start_on", "end date": "end_on", "date": "start_on", "type": "entry_kind"}.get(normal, name)
        if name in fields:
            mapping[name] = str(col)
    return mapping


def schema_matches(kind, mapping):
    anchors = {
        "timetable": {"subject", "day", "start_time", "end_time", "faculty_code", "room"},
        "faculty": {"code", "name", "office", "email", "subjects"},
        "menu": {"name", "price", "vegetarian", "category", "location"},
        "events": {"title", "subject", "start", "end", "location"},
        "calendar": {"title", "start_on", "end_on", "entry_kind"},
        "emails": {"sender", "subject", "body", "received"},
        "exceptions": {"timetable_id", "date", "cancelled", "room"},
    }
    return len(anchors[kind] & set(mapping)) >= 2


def error_groups(errors):
    groups = {}
    for error in errors:
        group = groups.setdefault(error["message"], dict(message=error["message"], count=0, rows=[]))
        group["count"] += 1
        if len(group["rows"])<5:
            group["rows"].append(error["row"])
    return list(groups.values())[:12]


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
    if field in ("vegetarian", "available", "nutrition_verified", "unread", "cancelled", "classes_cancelled"):
        v = str(value).lower()
        if v in ("yes", "true", "1", "1.0", "veg", "vegetarian", "available"):
            return True
        if v in ("no", "false", "0", "0.0", "non-veg", "non-vegetarian", "unavailable"):
            return False
    if field in ("published", "expires", "effective_from", "effective_to", "date", "start_on", "end_on"):
        return str(value)[:10]
    return value


def missing(value):
    return value is None or str(value).strip().casefold() in {'','na','n/a','null','none','nan','—','-'}


def date_ranges_overlap(a,b):
    return ((a.get('effective_from') or '0001-01-01') <= (b.get('effective_to') or '9999-12-31')
            and (b.get('effective_from') or '0001-01-01') <= (a.get('effective_to') or '9999-12-31'))


def timetable_overlaps(rows):
    for i,row in enumerate(rows):
        for j,other in enumerate(rows[:i]):
            if (row['department']==other['department'] and row['semester']==other['semester'] and row['section']==other['section']
                    and row['day']==other['day'] and row['start_time']<other['end_time'] and other['start_time']<row['end_time']
                    and date_ranges_overlap(row,other)):
                yield j,i


def timetable_ambiguities(rows):
    return [f"{rows[j]['subject']} and {rows[i]['subject']} overlap on weekday {rows[i]['day']} at {rows[i]['start_time'][:5]}. Both are retained; verify applicability. No student assignment is inferred."
            for j,i in islice(timetable_overlaps(rows),12)]


def validate_records(frame, kind, mapping, service, department, semester=None, section="All", event_kind=None, defaults=None):
    if not 1 <= len(frame) <= (1000 if kind == "timetable" else 10000) or len(frame.columns) > 100:
        raise ValueError("Use 1–1,000 timetable rows or 1–10,000 other rows, with at most 100 columns.")
    schema = DOMAIN_SCHEMAS[kind]
    errors = []
    rows = []
    row_positions = []
    for index, raw in frame.iterrows():
        try:
            data = {
                field: normalize(raw[col], field)
                for field, col in mapping.items()
                if col in frame.columns and field in schema.model_fields and not missing(raw[col])
            }
            defaults=defaults or {}
            for field in ('effective_from','effective_to') if kind=='timetable' else ('served_on','location','vegetarian') if kind=='menu' else ():
                if field not in data and not missing(defaults.get(field)):
                    data[field]=defaults[field]
            if kind=='menu' and isinstance(data.get('served_on'),str):
                value=data['served_on'].strip().casefold()
                weekdays=['monday','tuesday','wednesday','thursday','friday','saturday','sunday']
                if value in ('daily','every day','regular','permanent','all days'):
                    data.pop('served_on')
                elif value in weekdays:
                    data['served_day']=weekdays.index(value)
                    data.pop('served_on')
            for field in ('effective_from','effective_to','served_on'):
                if mapping.get(field) and str(mapping[field]).strip().casefold().replace('_',' ') in ('day','weekday','day of week'):
                    raise ValueError(f'{field}: weekday columns cannot be mapped to calendar dates. Clear this mapping.')
            data.setdefault("department", department)
            if kind == 'timetable' and raw.get('__unresolved_subject') and data.get('subject') == raw['__unresolved_subject']:
                raise ValueError('Unresolved OCR subject: verify the original legend and correct this course name/code.')
            if kind == "events":
                headers = {str(k).casefold().replace("_", " "): v for k, v in raw.items()}
                day = str(headers.get("exam date", headers.get("date", "")))[:10]
                for field in ("start", "end"):
                    value = data.get(field) or headers.get(field+" time")
                    if value and day and re.fullmatch(r"\d{1,2}:\d\d(?::\d\d)?(?:\s*[AaPp][Mm])?", str(value)):
                        data[field] = day+"T"+normalize(value, "start_time")
                if not data.get("title") and data.get("subject"):
                    data["title"] = data["subject"]
            if "semester" in schema.model_fields and semester is not None:
                data.setdefault("semester", semester)
                data.setdefault("section", section)
                if str(data["semester"]) != str(semester) or data["section"] != section:
                    raise ValueError("Row lies outside the selected source semester/section.")
            if kind == "events" and event_kind:
                if data.get("kind", event_kind) != event_kind:
                    raise ValueError("Row kind does not match the selected event/exam workflow.")
                data["kind"] = event_kind
            if kind == "emails" and not data.get("extracted_dates"):
                data["extracted_dates"] = sorted(
                    set(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", str(data.get("body", ""))))
                )
            row = schema.model_validate(data).model_dump(mode="json")
            row_semester, row_section = row.get("semester", semester), row.get("section", section)
            if (row_semester is None) != (row_section == "All") or (row_semester is not None and row.get("department", department) == "All"):
                raise ValueError("Section-specific rows require a department, semester and section together.")
            if kind not in ("emails",):
                service.actor.check_department_write(row.get("department", department), kind,
                    row.get("semester", semester), row.get("section", section))
                if department != "All" and row.get("department") not in ("All", department):
                    raise ValueError("Rows outside the source department require an institution-wide (All) import.")
            if kind == "timetable" and row["faculty_code"] and not service.faculty(code=row["faculty_code"]):
                raise ValueError("Faculty code was not found. Import faculty first, then map faculty_code.")
            rows.append(row)
            row_positions.append((int(raw.get('__source_row', int(index)+2)),str(raw.get('__source_sheet',''))))
        except (ValidationError, ValueError) as exc:
            msg = (
                "; ".join(".".join(str(x) for x in e["loc"]) + ": " + e["msg"] for e in exc.errors())
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            errors.append(dict(row=int(raw.get('__source_row',int(index)+2)), sheet=raw.get('__source_sheet',''), message=msg,
                fields=[".".join(str(part) for part in e["loc"]) for e in exc.errors()] if isinstance(exc, ValidationError) else []))
    keys = [natural_key(kind, r) for r in rows]
    if kind == 'timetable':
        unique, seen = [], set()
        for row, key, position in zip(rows, keys, row_positions):
            if key in seen:
                errors.append(dict(row=position[0],sheet=position[1],fields=['subject','day','start_time'], message='Duplicate class: correct or omit this repeated row.'))
            else:
                unique.append(row)
                seen.add(key)
        rows = unique
    elif len(keys) != len(set(keys)):
        errors.append(dict(row=0, message="Duplicate natural keys; remove duplicate rows."))
    return rows, errors


def timetable_frame(frame, mapping):
    kept, skipped = [], []
    for index, row in frame.iterrows():
        data = row.to_dict()
        data.setdefault('__source_row', int(index)+2)
        values = [v for k,v in data.items() if not str(k).startswith('__')]
        subject = data.get(mapping.get('subject','subject'), '')
        if all(missing(v) for v in values) or re.fullmatch(r'(?:short\s+|lunch\s+)?break',str(subject).strip(),re.I):
            skipped.append(dict(row=data['__source_row'],reason='Empty row or explicitly labelled break.'))
        else:
            kept.append(data)
    return pd.DataFrame(kept, columns=list(dict.fromkeys([*frame.columns,'__source_row']))).fillna(''), skipped


def natural_key(kind, row, owner=None):
    keys = {
        "faculty": ["code"],
        "timetable": [
            "department",
            "semester",
            "section",
            "lab_batch",
            "day",
            "start_time",
            "subject",
            "effective_from",
            "effective_to",
        ],
        "menu": ["name", "location", "served_on", "meal"],
        "events": ["title", "start", "location"],
        "emails": ["sender", "subject", "received"],
        "exceptions": ["timetable_id", "date"],
        "calendar": ["title", "start_on", "end_on", "department", "semester", "section"],
    }
    base = {k: row.get(k) for k in keys[kind]}
    if not base.get("lab_batch"):
        base.pop("lab_batch", None)
    for field in ("served_on", "meal"):
        if not base.get(field):
            base.pop(field, None)
    if kind=='menu' and row.get('served_day') is not None:
        base['served_day']=row['served_day']
    if kind == "events" and row.get("semester") is not None:
        base.update(department=row.get("department"), semester=row["semester"], section=row.get("section", "All"))
    if kind=='events' and row.get('event_schema')=='official-v1':
        base.update(department=row['department'],semester=row.get('semester'),section=row.get('section','All'),
                    start_date=row.get('start_date'),start_time=row.get('start_time'),organizer=row.get('organizer'))
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

    def queue_capacity(self):
        from sqlalchemy import func, text
        db, actor = self.c.db, self.c.actor
        serialize_sqlite_writes(db)
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": "queue:"+actor.tenant_id+":"+actor.user_id})
        count = db.scalar(select(func.count()).select_from(IngestionJob).where(
            IngestionJob.tenant_id == actor.tenant_id, IngestionJob.requested_by == actor.user_id,
            IngestionJob.status.in_(["queued", "processing", "extract_queued"])))
        if count >= settings().ingestion_queue_limit:
            raise HTTPException(429, "Your ingestion queue is full. Wait for a job to finish.", headers={"Retry-After": "10"})

    def receive(self, filename, content, options: ImportOptions):
        c = self.c
        a = c.actor
        private = options.visibility == "private"
        if options.timetable_publish_requested or options.event_publish_requested or options.event_index_preview:
            raise ValueError('Use the explicit Publish Timetable action after extraction and validation.')
        if options.reindex:
            raise ValueError("Use the authorized re-index endpoint for an existing source.")
        if not private:
            a.check_department_write(options.department, options.kind, options.semester, options.section)
        elif options.department not in ("All", a.department):
            raise HTTPException(403, "Import personal information only into your own department.")
        replacement = options.replaces_source_id
        if options.supersedes_id:
            previous = c.db.scalar(c.scope(Document).where(Document.id == options.supersedes_id))
            if not previous:
                raise ValueError("The previous document is unavailable.")
            if replacement and replacement != previous.source_id:
                raise ValueError("Replacement source and previous document do not match.")
            replacement = previous.source_id
        if replacement:
            previous_source = c.canteen_source(replacement) if a.role == "canteen_manager" else c.management_source(replacement)
            if (
                previous_source.owner_id is not None
                or previous_source.publication_status != "published"
                or previous_source.kind != options.kind
                or previous_source.department != options.department
                or previous_source.semester != options.semester or previous_source.section != options.section
            ):
                raise ValueError("Replace a published shared source of the same kind and department.")
        if not content or len(content) > MAX_BYTES:
            raise ValueError("Choose a nonempty file smaller than 15 MB.")
        filename = Path(filename).name[:255]
        allowed = (
            FORMATS
            if options.kind == "document"
            else {".csv", ".xlsx", ".json", ".eml"}
            if options.kind == "emails"
            else FORMATS
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
            if (existing.department, existing.semester, existing.section) != (options.department, options.semester, options.section):
                raise ValueError("Identical bytes already have a different source scope. Upload a distinct reviewed version.")
            if existing.owner_id != (a.user_id if private else None):
                raise ValueError("These bytes already exist with different visibility. Upload a distinct version.")
            if existing.file_state != "active" or existing.publication_status in ("revoked", "rejected", "replaced"):
                raise ValueError("That source is closed. Upload a corrected, distinct version.")
            job = c.db.scalar(
                select(IngestionJob).where(
                    IngestionJob.source_id == existing.id, IngestionJob.tenant_id == a.tenant_id
                )
            )
            if job:
                if (options.event_workflow and not job.options.get('event_workflow')
                        and existing.publication_status=='draft' and job.status in ('preview','failed')
                        and job.options.get('event_kind') not in ('exam','deadline')):
                    # Re-uploading an old draft must not strand it in the legacy
                    # structured-table validator. Preserve source and job IDs.
                    job.options=ImportOptions.model_validate({**job.options,'event_workflow':True,
                        'event_reviewed':False,'event_publish_requested':False}).model_dump(mode='json')
                    return self.preview(job)
                return job
        sid = uid()
        self.queue_capacity()
        suffix = Path(filename).suffix.lower()
        probe = parse_file(filename, content, probe=True) if suffix in {".pdf", ".png", ".jpg", ".jpeg", ".webp"} else {}
        deferred = bool(probe.get("needs_ocr"))
        ocr_blocked=deferred and not settings().ocr_enabled
        options.ocr_extract_pending, options.ocr_reviewed = deferred, False
        parsed = None if deferred or suffix == ".eml" else parse_file(filename, content,import_kind=options.kind)
        from backend.app.document_rows import contains_student_records
        student_records = options.kind == "document" and parsed is not None and contains_student_records(parsed)
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
            student_records=student_records,
            owner_id=a.user_id if private else None,
            department=options.department,
            semester=options.semester,
            section=options.section,
            demo=options.demo,
            replaces_source_id=replacement,
        )
        c.db.add(source)
        c.db.flush()
        job = IngestionJob(
            tenant_id=a.tenant_id,
            source_id=sid,
            requested_by=a.user_id,
            department=options.department,
            options=options.model_dump(mode="json"),
            status="preview" if ocr_blocked else "queued" if deferred else "received",
        )
        c.db.add(job)
        c.db.flush()
        if deferred:
            job.preview = dict(filename=filename, format=suffix.lstrip("."), message="Local OCR queued. The worker will prepare an editable preview; this does not publish data.", errors=[], warnings=["OCR requires administrator verification before confirmation."])
            if ocr_blocked:
                job.preview={**job.preview,"message":"Original preview is available. OCR is disabled; enable local OCR or supply a text/table export before processing.",
                    "needs_ocr":True,"error_count":1}
        else:
            self.preview(job, parsed=parsed)
        c.db.add(
            AuditLog(
                tenant_id=a.tenant_id,
                user_id=a.user_id,
                action="ingestion.received",
                details={"job_id": job.id, "kind": options.kind, "bytes": len(content)},
            )
        )
        return job

    def as_document(self, job):
        source = self.c.management_source(job.source_id)
        self.c.actor.require_admin()
        if source.publication_status != "draft" or source.file_state != "active":
            raise ValueError("Only an active draft can be imported as a separate general document.")
        options = ImportOptions.model_validate({**job.options, "kind": "document", "mapping": {},
            "corrected_rows": None, "corrected_pages": None, "reindex": False,
            "replaces_source_id": None, "supersedes_id": None})
        return self.receive(source.filename, self.store.get(source.storage_key), options)

    @staticmethod
    def document_text(parsed, options):
        if options.corrected_pages is None:
            return parsed
        supplied = [(p.page, p.text.strip()) for p in options.corrected_pages]
        original = [(int(p), t.strip()) for p, t in parsed["pages"]]
        if supplied == original:
            return parsed
        if parsed.get("document_units"):
            raise ValueError("Structured document rows must retain their original identities; upload a corrected version instead.")
        original_pages = {int(p) for p, _ in parsed["pages"]}
        if {p.page for p in options.corrected_pages} != original_pages:
            raise ValueError("Text corrections must preserve the original page identifiers for citations.")
        return {**parsed, "pages": [(p.page, p.text) for p in options.corrected_pages]}

    def preview(self, job, mapping=None, rows=None, parsed=None, ocr_reviewed=False, reextract=False, defaults=None, overlap_reviewed=False, reset_mapping=False, pages=None, timetable_ready_only=False):
        source = self.c.management_source(job.source_id)
        if source.file_state != "active" or source.publication_status != "draft":
            raise ValueError("Only a draft source can be previewed or remapped.")
        if job.status in ("queued", "processing", "extract_queued", "completed"):
            return job
        content = self.store.get(source.storage_key)
        options = ImportOptions.model_validate(job.options)
        if options.kind == 'timetable':
            options.timetable_ready_only = timetable_ready_only
        if defaults is not None:
            allowed={'effective_from','effective_to','menu_date','menu_location','menu_vegetarian','demo'}
            if set(defaults)-allowed:
                raise ValueError('Unsupported upload defaults.')
            options=ImportOptions.model_validate({**options.model_dump(mode='json'),**defaults})
            if defaults.get('demo') is True and not source.demo:
                if self.c.db.bind.dialect.name=='postgresql':
                    from sqlalchemy import text
                    if not self.c.db.scalar(text("SELECT to_regprocedure('public.campus_mark_draft_demo(text)')")):
                        raise ValueError('Marking this existing draft Demo Data requires approved migration 0009.')
                    self.c.db.execute(text('SELECT campus_mark_draft_demo(:sid)'),{'sid':source.id})
                    self.c.db.refresh(source)
                else:
                    source.demo=True
        if rows is not None or mapping is not None or defaults is not None:
            options.overlap_reviewed=False
        if overlap_reviewed:
            options.overlap_reviewed=True
        if reextract:
            options.corrected_rows, options.mapping, options.ocr_reviewed = None, {}, False
            options.corrected_pages = None
            options.ocr_extract_pending = True
            options.timetable_publish_requested = False
            options.event_publish_requested = False
            options.event_index_preview = False
            # The restricted API cannot write worker-owned staged_data. Keep
            # the old private extraction until the worker atomically replaces
            # it; ocr_extract_pending always takes precedence over that cache.
            job.options = options.model_dump(mode="json")
        if options.ocr_extract_pending:
            if not settings().ocr_enabled:
                raise ValueError("OCR is disabled. Preview the original, then enable local OCR or supply a text/table export before processing.")
            self.queue_capacity()
            job.status, job.error = "queued", None
            job.preview = {**job.preview, "message": "Local OCR queued for the worker; no data is published."}
            return job
        if pages is not None:
            options = ImportOptions.model_validate({**options.model_dump(mode="json"), "corrected_pages": pages})
            self.c.db.add(AuditLog(tenant_id=self.c.actor.tenant_id, user_id=self.c.actor.user_id,
                action="ingestion.text_corrected", details={"job_id":job.id, "pages":len(pages)}))
        if rows is not None or mapping is not None or pages is not None:
            options.ocr_reviewed = False
        if ocr_reviewed:
            options.ocr_reviewed = True
        job.options = options.model_dump(mode="json")
        if parsed is None and job.staged_data.get("ocr_result"):
            parsed = job.staged_data["ocr_result"]
        if options.kind=='events' and options.event_workflow:
            from backend.app.event_pipeline import EventPipeline
            return EventPipeline(self).preview(job,source,options,parsed)
        if parsed and parsed.get("needs_review") and options.kind == "menu":
            parsed = {**parsed, "rows": [{**r, "location": r.get("location") or options.menu_location,
                "vegetarian": options.menu_vegetarian if r.get("vegetarian") == "" and options.menu_vegetarian is not None else r.get("vegetarian", "")} for r in parsed["rows"]]}
        if rows is not None:
            if options.kind == "document" or not rows or len(json.dumps(rows)) > 4_000_000 or any(len(r) > 100 for r in rows):
                raise ValueError("Provide 1–10,000 structured rows within the 4 MB correction limit.")
            options.corrected_rows = rows
            job.options = options.model_dump(mode="json")
            self.c.db.add(AuditLog(tenant_id=self.c.actor.tenant_id, user_id=self.c.actor.user_id,
                action="ingestion.corrected", details={"job_id": job.id, "rows": len(rows)}))
        if mapping is not None:
            options.mapping = {} if reset_mapping else mapping
            job.options = options.model_dump(mode="json")
        if options.kind == "document":
            parsed = self.document_text(parsed or parse_file(source.filename, content), options)
            parts = chunks(parsed["pages"])
            job.preview = dict(
                filename=source.filename,
                bytes=len(content),
                message="Readable document text is ready for review. No spreadsheet columns are required. Confirm to index passages; publication requires your explicit review.",
                columns=[],
                errors=[] if parts else [dict(row=0, message="No usable text was extracted. Supply readable text or retry OCR with a clearer original.")],
                error_count=0 if parts else 1,
                valid_count=len(parts),
                detected_type=detect_kind(parsed),
                format=parsed["format"],
                warnings=parsed["warnings"],
                passages=[dict(page=p, text=t[:1000]) for p, t in parsed["pages"][:10]],
                editable_pages=[dict(page=p, text=t) for p, t in parsed["pages"]],
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
            else:
                filename = source.filename
            parsed = parsed or parse_file(filename, content,import_kind=options.kind)
            raw_rows = options.corrected_rows if options.corrected_rows is not None else parsed["rows"]
            if not raw_rows:
                job.preview = dict(format=parsed['format'],detected_type=detect_kind(parsed),schema_mismatch=True,
                    suggested_kind=detect_kind(parsed),columns=[],fields=[],errors=[],valid_count=0,error_count=1,
                    message='No usable structured rows were extracted. The original is preserved. Re-extract a clearer scan, review the extracted text, or supply corrected CSV/JSON rows.',
                    warnings=parsed.get('warnings',[])+[issue['message'] for issue in parsed.get('ocr_issues',[])])
                job.status, job.error = 'preview', None
                return job
            frame = pd.DataFrame(raw_rows).fillna("")
            selected = options.mapping or detect_mapping(frame.columns, options.kind)
            skipped_timetable = []
            if options.kind == 'timetable':
                frame, skipped_timetable = timetable_frame(frame, selected)
                raw_rows=frame.to_dict('records')
            if not schema_matches(options.kind, selected):
                job.preview = dict(format=parsed["format"], detected_type=detect_kind(parsed),
                    schema_mismatch=True, suggested_kind=detect_kind(parsed),
                    message="This file does not resemble the selected information type. Choose General official document to preserve its sheets, headers and row relationships, or upload a matching table.",
                    columns=list(map(str,frame.columns)), fields=[], errors=[], error_count=1,
                    valid_count=0, warnings=parsed["warnings"])
                if options.kind=='timetable':
                    job.preview={**job.preview,'editable_rows':raw_rows,'review_count':len(raw_rows),
                        'review_errors':[dict(row=int(r.get('__source_row',i+2)),sheet=r.get('__source_sheet',''),
                            message='Map or enter the essential day, time and course-name fields.') for i,r in enumerate(raw_rows)]}
                job.status, job.error = "preview", None
                self.c.db.flush()
                return job
            options.mapping = selected
            job.options = options.model_dump(mode="json")
            rows, errors = validate_records(frame, options.kind, selected, self.c, options.department,
                options.semester, options.section, options.event_kind,dict(effective_from=options.effective_from,
                    effective_to=options.effective_to,served_on=options.menu_date,location=options.menu_location,vegetarian=options.menu_vegetarian)) if len(frame) else ([],[])
            review_errors = list(errors) if options.kind == 'timetable' else []
            if options.kind == 'timetable' and options.timetable_ready_only and rows:
                errors = []
            overlap_warnings=timetable_ambiguities(rows) if options.kind=='timetable' else []
            requires_demo=parsed.get('requires_demo',False)
            if requires_demo and not source.demo:
                errors.append(dict(row=0,fields=['demo'],message='Workbook explicitly contains SAMPLE/TEST DATA. Mark this draft Demo Data before confirmation.'))
            keys = [natural_key(options.kind, r, source.owner_id) for r in rows]
            existing_count = len(list(self.c.db.scalars(self.c.scope(CampusRecord, options.kind)
                .where(CampusRecord.natural_key.in_(keys)).with_only_columns(CampusRecord.id)))) if keys else 0
            job.preview = dict(
                detected_type=detect_kind(parsed),
                format=parsed["format"],
                warnings=parsed["warnings"] + (["Corrected rows override extraction; the worker revalidates them."] if options.corrected_rows is not None else [])
                    + ([f"{existing_count} published key(s) will be updated only after approval and publication."] if existing_count else []),
                editable_rows=raw_rows,
                skipped_rows=parsed.get('skipped_rows',[])+skipped_timetable,
                skipped_count=len(parsed.get('skipped_rows',[]))+len(skipped_timetable),
                review_count=len({(e['row'],e.get('sheet','')) for e in review_errors}),
                review_errors=review_errors,
                review_rows=[i for i,r in enumerate(raw_rows) if (int(r.get('__source_row',i+2)),r.get('__source_sheet','')) in {(e['row'],e.get('sheet','')) for e in review_errors}],
                ready_only=options.timetable_ready_only,
                overlap_warnings=overlap_warnings,
                parallel_records=[{k:rows[i][k] for k in ('day','start_time','subject')} for i in sorted({i for pair in timetable_overlaps(rows) for i in pair})] if options.kind=='timetable' else [],
                overlap_reviewed=options.overlap_reviewed,
                defaults=dict(effective_from=str(options.effective_from or ''),effective_to=str(options.effective_to or ''),
                    menu_date=str(options.menu_date or ''),menu_location=options.menu_location),
                requires_demo=requires_demo,
                course_mapping_text=parsed.get('course_mapping_text',''),
                columns=[str(x) for x in frame.columns],
                fields=[
                    dict(name=name, required=field.is_required() and name not in ({"department"} | ({"semester","section"} if options.semester is not None else set())))
                    for name, field in DOMAIN_SCHEMAS[options.kind].model_fields.items()
                ],
                mapping=selected,
                records=rows[:10],
                raw=frame.head(5).astype(str).to_dict(orient="records"),
                valid_count=len(rows),
                errors=errors[:100],
                error_groups=error_groups(errors),
                error_count=len(errors),
            )
        job.status = "preview"
        if parsed and parsed.get("needs_review"):
            job.preview = {**job.preview, "ocr": True, "ocr_reviewed": options.ocr_reviewed,
                "ocr_pages": parsed.get("ocr_pages", []), "ocr_issues": parsed.get("ocr_issues", []),
                "confidence_available": False,
                "warnings": job.preview.get("warnings", []) + ["OCR has no calibrated confidence score. Check names, prices, diet and location against the original."]}
        job.error = None
        self.c.db.flush()
        return job

    def confirm(self, job):
        source = self.c.management_source(job.source_id)
        if source.file_state != "active" or source.publication_status != "draft":
            raise ValueError("Only a draft source can be submitted for processing.")
        if job.status == "completed":
            return job
        if job.status != "preview" or job.preview.get("error_count", 0):
            raise ValueError("Fix mapping and validation errors before confirming this import.")
        if job.preview.get("schema_mismatch") or job.preview.get("valid_count", 1) == 0:
            raise ValueError("No validated records are ready. Correct the extracted rows before confirmation.")
        if job.options.get('event_workflow') and not job.options.get('event_reviewed'):
            raise ValueError('Validate Event after reviewing its details and full text.')
        if job.preview.get('overlap_warnings') and not job.options.get('overlap_reviewed'):
            raise ValueError('Review and acknowledge the overlapping timetable entries before confirming.')
        if job.preview.get("ocr") and not job.options.get("ocr_reviewed"):
            raise ValueError("Explicitly verify the OCR preview against the original before confirmation.")
        self.queue_capacity()
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
        source = c.management_source(job.source_id, lock=True)
        if job.requested_by != c.actor.user_id or options.kind != source.kind:
            raise HTTPException(403, "The job does not match its source or requester.")
        if job.status not in ("queued", "processing"):
            raise ValueError("Only a queued or claimed job can be processed.")
        expected = "published" if options.reindex else "draft"
        if source.file_state != "active" or source.publication_status != expected:
            raise ValueError("The source is closed or is no longer in the required state.")
        if options.department != source.department:
            raise ValueError("The source department cannot change during processing.")
        if options.semester != source.semester or options.section != source.section:
            raise ValueError("The source semester/section cannot change during processing.")
        content = self.store.get(source.storage_key)
        if hashlib.sha256(content).hexdigest() != source.checksum:
            raise ValueError("Original file checksum changed. Restore the verified original or upload a new version.")
        if job.preview.get('requires_demo') and not source.demo:
            raise ValueError('This sample/test workbook must be labeled Demo Data before processing.')
        if options.ocr_extract_pending:
            if not settings().ocr_enabled:
                raise ValueError("OCR is disabled; this extraction job cannot run until it is explicitly enabled.")
            parsed = parse_file(source.filename, content, force_ocr=True, languages=options.ocr_languages,import_kind=options.kind)
            job.staged_data = {"ocr_result": parsed}
            options.ocr_extract_pending = False
            options.ocr_reviewed = False
            job.options = options.model_dump(mode="json")
            job.status = "received"
            self.preview(job, parsed=parsed)
            return 0
        if job.staged_data.get("ocr_result", {}).get("needs_review") and not options.ocr_reviewed:
            raise ValueError("OCR must be reviewed before processing confirmed information.")
        if options.kind=='events' and options.event_workflow:
            from backend.app.event_pipeline import EventPipeline
            count=EventPipeline(self).process(job,source,options)
            if options.event_index_preview:
                # Validation prepares semantic evidence under the still-private
                # draft. This is not approval, activation or publication.
                options.event_index_preview=False
                job.options=options.model_dump(mode='json')
                job.status='preview'
                c.db.add(AuditLog(tenant_id=c.actor.tenant_id,user_id=c.actor.user_id,
                    action='event.draft_indexed',details={'job_id':job.id,'chunks':job.preview['indexed_chunks']}))
                return count
        elif options.kind == "document":
            if options.reindex:
                # Re-embedding must not silently replace approved OCR text with a
                # new recognition result (or require a different language pack).
                saved = list(c.db.execute(select(DocumentChunk.page, DocumentChunk.text, DocumentChunk.student_numbers, DocumentChunk.row_data)
                    .join(Document, Document.id == DocumentChunk.document_id)
                    .where(Document.source_id == source.id, Document.tenant_id == c.actor.tenant_id,
                           DocumentChunk.tenant_id == c.actor.tenant_id)
                    .order_by(DocumentChunk.page, DocumentChunk.id)))
                parts = [(r.page,r.text) for r in saved]
                metadata = [dict(student_numbers=r.student_numbers,row_data=r.row_data) for r in saved]
                if not parts:
                    raise ValueError("No approved passages remain. Restore or upload the source for review.")
            else:
                parsed = job.staged_data.get("ocr_result") or (
                    parse_file(source.filename,content) if Path(source.filename).suffix.lower() in {".xlsx",".csv",".json"}
                    else dict(pages=extract_pages(source.filename,content)))
                parsed = self.document_text(parsed, options)
                from backend.app.document_rows import contains_student_records
                source.student_records = source.student_records or contains_student_records(parsed)
                units = parsed.get("document_units", [])
                parts = [(u["page"],u["text"]) for u in units] if units else chunks(parsed["pages"])
                metadata = [dict(student_numbers=u["student_numbers"],row_data={k:v for k,v in u.items() if k not in {"text","student_numbers"}}) for u in units] if units else [dict(student_numbers=[],row_data={}) for _ in parts]
            if not parts:
                raise ValueError("No usable text is available for document indexing.")
            vectors, model = embeddings([t for _, t in parts])
            existing = c.db.scalar(
                select(Document).where(Document.tenant_id == c.actor.tenant_id, Document.source_id == source.id)
            )
            if existing:
                from sqlalchemy import delete

                c.db.execute(
                    delete(DocumentChunk).where(
                        DocumentChunk.tenant_id == c.actor.tenant_id, DocumentChunk.document_id == existing.id
                    )
                )
                for (page, text), vector, meta in zip(parts, vectors, metadata):
                    c.db.add(
                        DocumentChunk(
                            tenant_id=c.actor.tenant_id,
                            document_id=existing.id,
                            page=page,
                            text=text,
                            embedding=vector,
                            embedding_model=model,
                            **meta,
                        )
                    )
                if job.options.get("reindex"):
                    return len(parts)
                # Restored/rolled-back documents must return to independent review.
                source.publication_status = "pending_review" if source.owner_id is None else "published"
                if source.owner_id is not None:
                    source.published_by, source.published_at = c.actor.user_id, utcnow()
                job.preview = {**job.preview, "passages": [dict(page=p, text=t[:1000]) for p, t in parts[:10]], "valid_count": len(parts)}
                from backend.app.duplicates import duplicate_report
                job.preview = {**job.preview, "duplicate_check": duplicate_report(c, source, logical_key=options.logical_key or options.topic)}
                c.db.add(AuditLog(tenant_id=c.actor.tenant_id, user_id=c.actor.user_id,
                    action="ingestion.completed", details={"job_id": job.id, "count": len(parts)}))
                return len(parts)
            previous = None
            if source.replaces_source_id:
                previous = c.db.scalar(
                    c.scope(Document).where(Document.source_id == source.replaces_source_id)
                )
            if options.supersedes_id:
                if not previous:
                    raise ValueError("The previous document is unavailable.")
            document = Document(
                tenant_id=c.actor.tenant_id,
                title=options.title or source.filename,
                category=options.category,
                department=options.department,
                semester=source.semester,
                section=source.section,
                owner_id=source.owner_id,
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
            for (page, text), vector, meta in zip(parts, vectors, metadata):
                c.db.add(
                    DocumentChunk(
                        tenant_id=c.actor.tenant_id,
                        document_id=document.id,
                        page=page,
                        text=text,
                        embedding=vector,
                        embedding_model=model,
                        **meta,
                    )
                )
            count = len(parts)
            job.preview = {
                **job.preview,
                "passages": [dict(page=page, text=text[:1000]) for page, text in parts[:10]],
                "valid_count": count,
            }
        else:
            if source.filename.lower().endswith(".eml"):
                # EML was fully normalized and validated during preview; a single-message import fits the preview.
                rows = job.preview.get("records", [])
                errors = []
            else:
                raw_rows = options.corrected_rows if options.corrected_rows is not None else job.staged_data["ocr_result"]["rows"] if job.staged_data.get("ocr_result") else parse_file(source.filename, content,import_kind=options.kind)["rows"]
                if options.kind == "menu" and job.staged_data.get("ocr_result"):
                    raw_rows = [{**r, "location": r.get("location") or options.menu_location,
                        "vegetarian": options.menu_vegetarian if r.get("vegetarian") == "" and options.menu_vegetarian is not None else r.get("vegetarian", "")} for r in raw_rows]
                frame=pd.DataFrame(raw_rows).fillna('')
                if options.kind=='timetable':
                    frame,_=timetable_frame(frame,options.mapping)
                rows, errors = validate_records(
                    frame, options.kind, options.mapping, c, options.department,
                    options.semester, options.section, options.event_kind,dict(effective_from=options.effective_from,
                        effective_to=options.effective_to,served_on=options.menu_date,location=options.menu_location,vegetarian=options.menu_vegetarian)
                ) if len(frame) else ([],[])
                if options.kind=='timetable' and options.timetable_ready_only and rows:
                    errors=[]
                if options.kind=='timetable' and timetable_ambiguities(rows) and not options.overlap_reviewed:
                    raise ValueError('Overlapping timetable entries require explicit administrator review.')
            if errors:
                raise ValueError("Source validation changed. Preview and validate again.")
            if source.owner_id is None:
                retained = dict(review_errors=job.preview.get('review_errors',[]),
                    editable_rows=job.preview.get('editable_rows',[])) if source.kind=='timetable' else {}
                job.staged_data = {"records": rows, **retained}
            else:
                self.activate_records(source, rows)
            count = len(rows)
        if source.owner_id is None:
            from backend.app.duplicates import duplicate_report
            job.preview = {**job.preview, "duplicate_check": duplicate_report(c, source,
                job.staged_data.get("records"), options.logical_key or options.topic)}
            source.publication_status = "pending_review"
        elif c.db.bind.dialect.name == "postgresql":
            # The API may activate only its own private source, never a shared source.
            from sqlalchemy import text as sql_text

            c.db.flush()
            c.db.execute(sql_text("SELECT campus_private_source_ready(:sid)"), {"sid": source.id})
            c.db.refresh(source)
        else:
            source.publication_status = "published"
            source.published_by = c.actor.user_id
            source.published_at = utcnow()
        c.db.add(
            AuditLog(
                tenant_id=c.actor.tenant_id,
                user_id=c.actor.user_id,
                action="ingestion.completed",
                details={"job_id": job.id, "count": count},
            )
        )
        return count

    def activate_records(self, source, rows):
        """Called only by publication (shared) or successful personal ingestion."""
        c = self.c
        for raw in rows:
            schema = DOMAIN_SCHEMAS[source.kind]
            if source.kind == 'events' and raw.get('event_schema') == 'official-v1':
                from backend.app.event_data import OfficialEventData
                schema = OfficialEventData
            row = schema.model_validate(raw).model_dump(mode="json")
            if source.owner_id is None:
                c.actor.check_department_write(row.get("department", source.department), source.kind,
                    row.get("semester", source.semester), row.get("section", source.section))
            if source.kind == "timetable" and row["faculty_code"] and not c.faculty(code=row["faculty_code"]):
                raise ValueError("Faculty information is no longer published. Publish faculty before this timetable.")
            owner = source.owner_id
            key = natural_key(source.kind, row, owner)
            record = c.db.scalar(
                select(CampusRecord).where(
                    CampusRecord.tenant_id == c.actor.tenant_id,
                    CampusRecord.kind == source.kind,
                    CampusRecord.natural_key == key,
                ).with_for_update()
            )
            if record:
                if record.owner_id != owner:
                    raise ValueError("The record owner does not match this import.")
                previous = c.db.get(Source, record.source_id)
                if previous and previous.publication_status == "published" and record.source_id != source.id and source.replaces_source_id != record.source_id:
                    raise ValueError("Explicit replacement is required before overwriting published records.")
                if c.actor.role == "department_admin" and record.department != c.actor.department:
                    raise HTTPException(403, "That record belongs to another department.")
                record.payload = row
                record.source_id = source.id
                record.department = row.get("department", source.department)
                record.semester = row.get("semester", source.semester)
                record.section = row.get("section", source.section)
            else:
                c.db.add(CampusRecord(
                    tenant_id=c.actor.tenant_id, kind=source.kind, natural_key=key,
                    department=row.get("department", source.department), owner_id=owner,
                    source_id=source.id, payload=row,
                    semester=row.get("semester", source.semester), section=row.get("section", source.section),
                ))


def work_once(session_factory, store=None):
    with session_factory() as db:
        serialize_sqlite_writes(db)
        # Worker uses its own trusted database role. SKIP LOCKED supports multiple PostgreSQL workers.
        stale = utcnow() - timedelta(minutes=10)
        stmt = (
            select(IngestionJob)
            .where(
                or_(
                    IngestionJob.status.in_(["queued", "extract_queued"]),
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
        serialize_sqlite_writes(db)
        # Lock source before job, matching publication/revocation lock order.
        source_id = db.scalar(select(IngestionJob.source_id).where(IngestionJob.id == job_id))
        db.scalar(select(Source).where(Source.id == source_id).with_for_update())
        job = db.scalar(select(IngestionJob).where(IngestionJob.id == job_id).with_for_update())
        if job.status != "processing":
            return True
        try:
            actor = resolve_actor(db, job.requested_by, job.tenant_id)
            # Process + state transition commit atomically. A parsing error rolls back all domain writes.
            count = Ingestion(CampusService(db, actor), store).process(job)
            extracted_preview = job.status == "preview"
            job.status = "preview" if extracted_preview else "completed"
            job.error = None
            job.preview = {**job.preview, "imported_count": count}
            if not extracted_preview and job.options.get('kind')=='timetable' and job.options.get('timetable_publish_requested'):
                from backend.app.publication import Publication
                from backend.app.db import set_identity
                set_identity(db,actor.user_id,actor.tenant_id)
                db.flush()
                publication=Publication(CampusService(db,actor))
                publication.transition(source_id,'approve')
                publication.transition(source_id,'publish')
                job.preview={**job.preview,'published_count':count}
            if not extracted_preview and job.options.get('kind')=='events' and job.options.get('event_publish_requested'):
                from backend.app.publication import Publication
                from backend.app.db import set_identity
                set_identity(db,actor.user_id,actor.tenant_id)
                db.flush()
                publication=Publication(CampusService(db,actor))
                publication.transition(source_id,'approve')
                publication.transition(source_id,'publish')
                job.preview={**job.preview,'published_count':count}
            db.commit()
        except Exception as exc:
            db.rollback()
            serialize_sqlite_writes(db)
            source = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
            job = db.scalar(select(IngestionJob).where(IngestionJob.id == job_id).with_for_update())
            if job.status != "processing":
                return True  # A concurrent revocation already cancelled this claim.
            if not source or source.file_state != "active" or source.publication_status in ("revoked", "rejected", "replaced"):
                job.status = "cancelled"
                job.error = "The source is no longer active."
                db.commit()
                return True
            job.status = "failed"
            job.error = (
                str(exc.detail if isinstance(exc, HTTPException) else exc)[:400]
                if isinstance(exc, (ValueError, HTTPException))
                else "Import failed safely. Check file contents and worker configuration, then retry."
            )
            db.commit()
    return True
