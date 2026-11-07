"""One college, scoped administrators, a canteen and a complete published answer."""
from datetime import timedelta
import json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from backend.app.auth import Actor, resolve_actor
from backend.app.models import Membership, Source, Document, IngestionJob
from backend.app.ingestion import Ingestion, work_once
from backend.app.publication import Publication
from backend.app.services import CampusService
from backend.app.schemas import ImportOptions
from backend.app.router import Router
from backend.app.files import Files


def member(env, role, department="AIML", semester=3, section="A"):
    user = str(uuid4())
    env.db.add(Membership(user_id=user, tenant_id=env.tenant, role=role, department=department,
        semester=semester, section=section, name="Synthetic "+role))
    env.db.flush()
    return CampusService(env.db, resolve_actor(env.db, user, env.tenant), clock=env.service.clock)


def finish(env, service, job):
    pipeline = Ingestion(service, env.store)
    pipeline.confirm(job)
    env.db.commit()
    assert work_once(env.factory, env.store)
    env.db.expire_all()
    assert job.status == "completed", job.error
    reviewer = CampusService(env.db, resolve_actor(env.db, env.reviewer, env.tenant), clock=env.service.clock)
    Publication(reviewer).transition(job.source_id, "approve")
    Publication(service).transition(job.source_id, "publish")
    env.db.commit()


def test_section_notice_worker_review_publication_and_selected_isolation(env):
    section = member(env, "section_admin")
    other = member(env, "student", section="B")
    own = member(env, "student")
    job = Ingestion(section, env.store).receive("section-circular.txt", b"Section A robotics briefing begins at 11:30 on Friday.",
        ImportOptions(kind="document", department="AIML", semester=3, section="A", title="Section A circular"))
    with pytest.raises(HTTPException):
        own.source(job.source_id)
    finish(env, section, job)
    assert job.preview["duplicate_check"]["checked"]
    document = env.db.scalar(select(Document).where(Document.source_id == job.source_id))
    assert own.selected_document(document.id)
    with pytest.raises(HTTPException):
        other.selected_document(document.id)
    assert "11:30" in str(Router(own).ask("When does the robotics briefing begin?", document_id=document.id))
    Files(section, env.store).change(job.source_id, "delete")
    with pytest.raises(HTTPException):
        own.selected_document(document.id)


@pytest.mark.parametrize("role,kind,department,semester,section,allowed", [
    ("institution_admin", "document", "CSE", None, "All", True),
    ("department_admin", "calendar", "AIML", None, "All", True),
    ("department_admin", "document", "CSE", None, "All", False),
    ("section_admin", "timetable", "AIML", 3, "A", True),
    ("section_admin", "document", "AIML", 3, "B", False),
    ("section_admin", "events", "All", None, "All", False),
    ("canteen_manager", "menu", "All", None, "All", True),
    ("canteen_manager", "document", "All", None, "All", False),
    ("department_admin", "menu", "AIML", None, "All", False),
    ("student", "document", "AIML", 3, "A", False),
])
def test_five_role_write_matrix(role, kind, department, semester, section, allowed):
    actor = Actor("synthetic", "synthetic", role, "AIML", 3, "A", "Fixture")
    if allowed:
        actor.check_department_write(department, kind, semester, section)
    else:
        with pytest.raises(HTTPException) as exc:
            actor.check_department_write(department, kind, semester, section)
        assert exc.value.status_code == 403


def test_department_admin_cannot_use_all_as_a_college_scope():
    actor = Actor("fixture","fixture","department_admin","All",3,"A","Fixture")
    with pytest.raises(HTTPException):
        actor.check_department_write("All","document")


def test_holiday_upload_changes_timetable_and_next_class_with_citations(env):
    student = member(env, "student")
    today = env.service.local_now().date()
    before = student.timetable(today)
    teachers = student.faculty(subject="Operating Systems")
    assert before
    rows = [dict(title="Synthetic college holiday", start_on=str(today), entry_kind="holiday", classes_cancelled=True)]
    job = Ingestion(env.service, env.store).receive("calendar.json", json.dumps(rows).encode(), ImportOptions(kind="calendar"))
    finish(env, env.service, job)
    assert student.timetable(today) == []
    assert student.faculty(subject="Operating Systems") == teachers
    answer = Router(student).ask("Is today a college holiday?")
    assert answer["kind"] == "calendar" and answer["sources"][0]["id"] == job.source_id
    assert "Synthetic college holiday" in answer["answer"]
    assert student.next_class()["date"] != str(today)
    Files(env.service, env.store).change(job.source_id, "delete")
    assert student.timetable(today)


def test_calendar_period_conflict_is_reported_before_publication(env):
    today = env.service.local_now().date()
    rows = [dict(title="Academic semester period",start_on=str(today),end_on=str(today+timedelta(days=90)),entry_kind="academic")]
    first = Ingestion(env.service,env.store).receive("calendar-v1.json",json.dumps(rows).encode(),ImportOptions(kind="calendar"))
    finish(env,env.service,first)
    second = Ingestion(env.service,env.store).receive("calendar-v2.json",json.dumps([{**rows[0],"title":"Changed semester period"}]).encode(),ImportOptions(kind="calendar"))
    Ingestion(env.service,env.store).confirm(second)
    env.db.commit()
    work_once(env.factory,env.store)
    env.db.expire_all()
    assert second.preview["duplicate_check"]["blocking"]
    with pytest.raises(HTTPException):
        Publication(env.service).transition(second.source_id,"approve")


def test_exam_date_time_venue_upload_and_tomorrow_query(env):
    student = member(env, "student")
    tomorrow = env.service.local_now().date()+timedelta(days=1)
    data = f"subject,date,start_time,end_time,venue\nOperating Systems,{tomorrow},09:30,11:30,Exam Hall\n".encode()
    job = Ingestion(env.service, env.store).receive("exam.csv", data,
        ImportOptions(kind="events", event_kind="exam", department="AIML", semester=3, section="A"))
    assert job.preview["valid_count"] == 1 and not job.preview["error_count"]
    finish(env, env.service, job)
    answer = Router(student).ask("Is there an exam tomorrow, and what time does it begin?")
    exam = next(r for r in answer["records"] if r["source_id"] == job.source_id)
    assert exam["start"].endswith("09:30:00") and exam["location"] == "Exam Hall"
    other = member(env, "student", section="B")
    assert job.source_id not in {r["source_id"] for r in other.events(start=tomorrow, days=1, kind="exam")}


def test_canteen_update_requires_explicit_replacement_and_college_review(env):
    manager = member(env, "canteen_manager")
    row = dict(name="Product test meal", price=40, category="Lunch", vegetarian=True, location="Campus Canteen")
    pipeline = Ingestion(manager, env.store)
    first = pipeline.receive("canteen.json", json.dumps([row]).encode(), ImportOptions(kind="menu"))
    finish(env, manager, first)
    assert pipeline.receive("canteen.json", json.dumps([row]).encode(), ImportOptions(kind="menu")).id == first.id
    blocked = pipeline.receive("changed.json", json.dumps([{**row, "price":55}]).encode(), ImportOptions(kind="menu"))
    pipeline.confirm(blocked)
    env.db.commit()
    work_once(env.factory, env.store)
    env.db.expire_all()
    assert blocked.preview["duplicate_check"]["blocking"]
    with pytest.raises(HTTPException) as exc:
        Publication(env.service).transition(blocked.source_id, "approve")
    assert exc.value.status_code == 409
    replacement = pipeline.receive("replacement.json", json.dumps([{**row, "price":60}]).encode(),
        ImportOptions(kind="menu", replaces_source_id=first.source_id))
    finish(env, manager, replacement)
    assert env.db.get(Source, first.source_id).publication_status == "replaced"
    assert next(r for r in env.service.menu() if r["name"] == row["name"])["price"] == 60


def test_section_source_rejects_outside_rows_and_revoked_scope(env):
    section = member(env, "section_admin")
    rows = [dict(title="Exam", subject="OS", start="2026-11-05T09:00:00", end="2026-11-05T10:00:00",
        location="Hall", kind="exam", semester=3, section="B", department="AIML")]
    job = Ingestion(section, env.store).receive("wrong-section.json", json.dumps(rows).encode(),
        ImportOptions(kind="events", department="AIML", semester=3, section="A"))
    assert job.preview["error_count"]
    with pytest.raises(ValueError):
        Ingestion(section, env.store).confirm(job)
    assert env.db.scalar(select(IngestionJob).where(IngestionJob.id == job.id)).status == "preview"


def test_calendar_section_without_department_is_rejected_during_preview(env):
    rows = [dict(title="Invalid scope",start_on="2026-11-02",semester=3,section="A")]
    job = Ingestion(env.service,env.store).receive("invalid-scope.json",json.dumps(rows).encode(),ImportOptions(kind="calendar"))
    assert job.preview["error_count"]
    with pytest.raises(ValueError):
        Ingestion(env.service,env.store).confirm(job)


def test_latest_examination_circular_uses_metadata_then_one_document(env):
    student = member(env,"student")
    today = env.service.local_now().date()
    sources = []
    for label, offset in (("Old",-1),("Current",0),("Later same-day",0)):
        job = Ingestion(env.service,env.store).receive(f"{label}.txt",
            f"Examination circular says {label} examination registration is required before Friday.".encode(),
            ImportOptions(kind="document",title="Examination circular",published=today+timedelta(days=offset)))
        finish(env,env.service,job)
        sources.append(job.source_id)
    answer = Router(student).ask("What does the latest examination circular say?")
    assert answer["records"] and {r["source_id"] for r in answer["records"]} == {sources[-1]}
    assert "get_latest_notices" in {t["tool"] for t in answer["trace"]}
