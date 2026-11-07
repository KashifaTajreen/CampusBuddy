"""Worker duplicate/conflict reports. Published rows are never silently replaced."""
import re
from sqlalchemy import select
from backend.app.models import Source, CampusRecord, Document, IngestionJob


def title_key(value):
    return re.sub(r"\W+", " ", value.casefold()).strip()


def duplicate_report(service, source, rows=None, logical_key=""):
    from backend.app.ingestion import natural_key, date_ranges_overlap
    published = service.scope(Source).where(Source.owner_id.is_(None), Source.id != source.id,
        Source.kind == source.kind)
    candidates = list(service.db.scalars(published.limit(2001)))
    incomplete = len(candidates) > 2000
    exact = [s.id for s in candidates if s.checksum == source.checksum]
    warnings, conflicts = [], set()
    keys = {natural_key(source.kind, r) for r in rows or []} if source.kind != "document" else set()
    if exact:
        warnings.append("An identical source file is already published.")
    if rows:
        prior = list(service.db.scalars(service.scope(CampusRecord, source.kind)
            .where(CampusRecord.owner_id.is_(None), CampusRecord.source_id != source.id).limit(10001)))
        incomplete = incomplete or len(prior) > 10000
        for record in prior:
            old = record.payload
            if record.natural_key in keys:
                conflicts.add(record.source_id)
            if source.kind == "timetable":
                for row in rows:
                    if (old.get("department") == row.get("department") and old.get("semester") == row.get("semester")
                        and old.get("section") == row.get("section") and old.get("day") == row.get("day")
                        and date_ranges_overlap(old,row)
                        and old["start_time"] < row["end_time"] and row["start_time"] < old["end_time"]):
                        conflicts.add(record.source_id)
            if source.kind == "events":
                for row in rows:
                    if (old.get("kind") == row.get("kind") == "exam" and old.get("department") == row.get("department")
                        and old.get("semester") == row.get("semester") and old.get("section", "All") == row.get("section", "All")
                        and ((old.get('start') and old.get('end') and row.get('start') and row.get('end') and old["start"] < row["end"] and row["start"] < old["end"])
                            or (old.get('start') and row.get('start') and title_key(old["title"]) == title_key(row["title"]) and old["start"][:7] == row["start"][:7]))):
                        conflicts.add(record.source_id)
            if source.kind == "calendar":
                for row in rows:
                    if ((title_key(old["title"]) == title_key(row["title"]) or
                         (old["start_on"] == row["start_on"] and old["end_on"] == row["end_on"] and old["entry_kind"] == row["entry_kind"])) and old["start_on"] <= row["end_on"]
                        and row["start_on"] <= old["end_on"] and old.get("semester") == row.get("semester")
                        and old.get("section") == row.get("section") and old.get("department") == row.get("department")):
                        conflicts.add(record.source_id)
    if source.kind == "document":
        current = service.db.scalar(select(Document).where(Document.source_id == source.id,
            Document.tenant_id == source.tenant_id))
        if current:
            peers = service.db.scalars(service.document_query().where(Document.owner_id.is_(None), Document.id != current.id))
            for document in peers:
                if title_key(document.title) == title_key(current.title) or (current.topic and document.topic == current.topic):
                    conflicts.add(document.source_id)
    if logical_key:
        same_scope = [s.id for s in candidates if (s.department,s.semester,s.section) == (source.department,source.semester,source.section)]
        for job in service.db.scalars(select(IngestionJob).where(IngestionJob.source_id.in_(same_scope))):
            if logical_key == (job.options.get("logical_key") or job.options.get("topic")):
                conflicts.add(job.source_id)
    if conflicts:
        warnings.append("A published logical version or overlapping schedule exists. Review differences and select an explicit replacement where intended.")
    if incomplete:
        warnings.append("Duplicate comparison exceeds its bounded scope. Narrow the source scope before publication.")
    unresolved = (conflicts | set(exact)) - {source.replaces_source_id}
    return dict(checked=True, exact_sources=sorted(exact), conflicting_sources=sorted(conflicts),
                complete=not incomplete,
                blocking=incomplete or (bool(unresolved) if source.kind != "document" else bool(set(exact)-{source.replaces_source_id})),
                warnings=warnings, replacement_id=source.replaces_source_id)
