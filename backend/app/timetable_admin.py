"""Administrator timetable editing. Writes are validated, clash-checked and audited."""

import hashlib
from datetime import time
from sqlalchemy import select
from fastapi import HTTPException
from backend.app.ingestion import natural_key
from backend.app.models import AuditLog, CampusRecord, Source
from backend.app.schemas import TimetableData

MANUAL_NOTE = b"Timetable entries typed in by administrators. The audit log records each change."


def _overlaps(a, b):
    return (
        a["day"] == b["day"]
        and a["effective_from"] <= b["effective_to"]
        and b["effective_from"] <= a["effective_to"]
        and time.fromisoformat(a["start_time"]) < time.fromisoformat(b["end_time"])
        and time.fromisoformat(b["start_time"]) < time.fromisoformat(a["end_time"])
    )


def _clashes(a, b):
    """Name what two entries compete for, if anything."""
    if not _overlaps(a, b):
        return set()
    found = set()
    if (a["department"], a["semester"], a["section"]) == (b["department"], b["semester"], b["section"]):
        found.add("section")
    if a["room"].lower() == b["room"].lower():
        found.add("room")
    if a["faculty_code"] == b["faculty_code"]:
        found.add("faculty")
    return found


class TimetableEditor:
    def __init__(self, service, store):
        self.c = service
        self.store = store

    def rows(self, department, semester, section):
        c = self.c
        c.actor.check_department_write(department)
        p = CampusRecord.payload
        stmt = c.scope(CampusRecord, "timetable").where(
            CampusRecord.department == department,
            p["semester"].as_integer() == semester,
            p["section"].as_string() == section,
        )
        faculty = {f["code"]: f["name"] for f in c.faculty()}
        output = c.records("timetable", stmt)
        for row in output:
            row["faculty"] = faculty.get(row["faculty_code"], "")
        return output

    def _record(self, record_id):
        c = self.c
        record = c.db.scalar(c.scope(CampusRecord, "timetable").where(CampusRecord.id == record_id))
        if not record:
            raise HTTPException(404, "That timetable entry is unavailable.")
        c.actor.check_department_write(record.department)
        return record

    def _manual_source(self, department, demo):
        """Edited entries cite this source, because the original file no longer describes them."""
        c = self.c
        label = f"manual-timetable:{c.actor.tenant_id}:{department}:{demo}"
        checksum = hashlib.sha256(label.encode()).hexdigest()
        source = c.db.scalar(
            select(Source).where(
                Source.tenant_id == c.actor.tenant_id,
                Source.checksum == checksum,
                Source.kind == "timetable",
                Source.uploaded_by == c.actor.user_id,
            )
        )
        if source:
            return source
        key = f"{c.actor.tenant_id}/manual/{checksum}"
        self.store.put(key, MANUAL_NOTE)
        source = Source(
            tenant_id=c.actor.tenant_id,
            filename=f"Manual timetable edits - {department}.txt",
            kind="timetable",
            checksum=checksum,
            storage_key=key,
            uploaded_by=c.actor.user_id,
            department=department,
            demo=demo,
        )
        c.db.add(source)
        c.db.flush()
        return source

    def _validate(self, data, editing=None):
        c = self.c
        c.actor.check_department_write(data.department)
        if not c.faculty(code=data.faculty_code):
            raise ValueError("That faculty member was not found. Choose someone from the directory.")
        row = data.model_dump(mode="json")
        p = CampusRecord.payload
        stmt = c.scope(CampusRecord, "timetable").where(p["day"].as_integer() == data.day)
        for other in c.db.scalars(stmt):
            if editing is not None and other.id == editing.id:
                continue
            o = other.payload
            found = _clashes(row, o)
            if editing is not None:
                # Imports are not clash-checked, so an entry may already conflict. That must not
                # block fixing its subject or room, but an edit may never add a new conflict.
                found -= _clashes(editing.payload, o)
            if "section" in found:
                raise ValueError(f"This section already has {o['subject']} at that time.")
            if "room" in found:
                raise ValueError(f"Room {o['room']} is already in use at that time ({o['subject']}).")
            if "faculty" in found:
                raise ValueError(f"That faculty member already teaches {o['subject']} at that time.")
        return row

    def _audit(self, action, record):
        c = self.c
        c.db.add(
            AuditLog(
                tenant_id=c.actor.tenant_id,
                user_id=c.actor.user_id,
                action=action,
                details={"record_id": record.id, "department": record.department},
            )
        )

    def _output(self, record):
        output = self.c.serialize(record)
        match = self.c.faculty(code=output["faculty_code"])
        output["faculty"] = match[0]["name"] if match else ""
        return output

    def create(self, data: TimetableData, demo=False):
        row = self._validate(data)
        record = CampusRecord(
            tenant_id=self.c.actor.tenant_id,
            kind="timetable",
            natural_key=natural_key("timetable", row),
            department=data.department,
            source_id=self._manual_source(data.department, demo).id,
            payload=row,
        )
        self.c.db.add(record)
        self.c.db.flush()
        self._audit("timetable.created", record)
        return self._output(record)

    def update(self, record_id, data: TimetableData):
        record = self._record(record_id)
        row = self._validate(data, editing=record)
        demo = self.c.source(record.source_id).demo
        record.payload = row
        record.department = data.department
        record.natural_key = natural_key("timetable", row)
        record.source_id = self._manual_source(data.department, demo).id
        self.c.db.flush()
        self._audit("timetable.updated", record)
        return self._output(record)

    def delete(self, record_id):
        record = self._record(record_id)
        self._audit("timetable.deleted", record)
        self.c.db.delete(record)
        self.c.db.flush()
        return {"deleted": True}
