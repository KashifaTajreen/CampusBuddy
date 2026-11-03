from dataclasses import replace
from datetime import date
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.app import ingestion as ingestion_module
from backend.app import main
from backend.app.auth import actor, get_session
from backend.app.models import AuditLog, CampusRecord, Membership, Source
from backend.app.schemas import TimetableData
from backend.app.services import CampusService
from backend.app.timetable_admin import MANUAL_NOTE, TimetableEditor
from backend.tests.conftest import NOW

# Saturday, which the seeded timetable leaves empty.
SLOT = dict(
    department="AIML",
    semester=3,
    section="A",
    subject="Robotics Lab",
    faculty_code="AIML-1",
    room="LAB-9",
    day=5,
    start_time="09:00",
    end_time="10:00",
    effective_from="2026-10-01",
    effective_to="2026-12-31",
)
SATURDAY = date(2026, 10, 31)


def entry(**changes):
    return TimetableData(**{**SLOT, **changes})


@pytest.fixture
def editor(env):
    return TimetableEditor(env.service, env.store)


@pytest.fixture
def department_admin(env):
    return CampusService(env.db, replace(env.service.actor, role="department_admin"), clock=lambda: NOW)


@pytest.fixture
def client(env, monkeypatch):
    def session():
        yield env.db

    # Both the editor and the download route must use the test's private store.
    monkeypatch.setattr(ingestion_module, "ObjectStore", lambda: env.store)
    monkeypatch.setattr(main, "ObjectStore", lambda: env.store)
    main.app.dependency_overrides[get_session] = session
    main.app.dependency_overrides[actor] = lambda: env.service.actor
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def seeded_row(env, department="AIML"):
    return env.db.scalar(
        select(CampusRecord).where(
            CampusRecord.tenant_id == env.tenant,
            CampusRecord.kind == "timetable",
            CampusRecord.department == department,
            CampusRecord.payload["semester"].as_integer() == 3,
            CampusRecord.payload["section"].as_string() == "A",
        )
    )


def test_rows_returns_the_selected_section_with_names(editor):
    rows = editor.rows("AIML", 3, "A")
    assert rows
    assert {(r["department"], r["semester"], r["section"]) for r in rows} == {("AIML", 3, "A")}
    assert all(r["faculty"].startswith("Dr. ") for r in rows)
    assert editor.rows("AIML", 3, "ZZ") == []


def test_created_entry_appears_in_the_student_timetable(env, editor):
    created = editor.create(entry())
    assert created["faculty"].startswith("Dr. ")
    assert created["id"]
    env.db.flush()
    assert any(r["id"] == created["id"] for r in env.service.timetable(on=SATURDAY))


def test_update_changes_the_timetable_and_cites_the_manual_source(env, editor):
    row = seeded_row(env)
    original = dict(row.payload)
    original_source = env.db.get(Source, row.source_id)
    changed = TimetableData(**{**original, "room": "LAB-9", "subject": "Moved class"})
    output = editor.update(row.id, changed)
    assert output["room"] == "LAB-9"
    assert output["source_id"] != original_source.id
    assert output["source_filename"] == "Manual timetable edits - AIML.txt"


def test_edit_keeps_the_demo_label(env, editor):
    row = seeded_row(env)
    assert env.db.get(Source, row.source_id).demo is True
    output = editor.update(row.id, TimetableData(**{**row.payload, "room": "LAB-9"}))
    assert output["demo"] is True


def test_manual_sources_are_reused(env, editor):
    first = editor.create(entry())
    second = editor.create(entry(start_time="10:00", end_time="11:00", room="LAB-8"))
    assert first["source_id"] == second["source_id"]


def test_delete_removes_the_entry(env, editor):
    created = editor.create(entry())
    assert editor.delete(created["id"]) == {"deleted": True}
    env.db.flush()
    assert not any(r["id"] == created["id"] for r in env.service.timetable(on=SATURDAY))
    with pytest.raises(HTTPException) as denied:
        editor.delete(created["id"])
    assert denied.value.status_code == 404


def test_each_change_is_audited(env, editor):
    created = editor.create(entry())
    editor.update(created["id"], entry(room="LAB-8"))
    editor.delete(created["id"])
    env.db.flush()
    logs = env.db.scalars(select(AuditLog).where(AuditLog.action.like("timetable.%"))).all()
    assert [log.action for log in logs] == ["timetable.created", "timetable.updated", "timetable.deleted"]
    assert all(log.user_id == env.user and log.details["record_id"] == created["id"] for log in logs)


def test_section_clash_is_rejected(editor):
    editor.create(entry())
    with pytest.raises(ValueError, match="This section already has Robotics Lab"):
        editor.create(
            entry(subject="Other", room="LAB-8", faculty_code="AIML-2", start_time="09:30", end_time="10:30")
        )


def test_room_clash_is_rejected(editor):
    editor.create(entry())
    with pytest.raises(ValueError, match="Room LAB-9 is already in use"):
        editor.create(entry(section="B", room="lab-9", faculty_code="AIML-2"))


def test_faculty_clash_is_rejected(editor):
    editor.create(entry())
    with pytest.raises(ValueError, match="already teaches Robotics Lab"):
        editor.create(entry(section="B", room="LAB-8"))


def test_touching_periods_do_not_clash(editor):
    editor.create(entry())
    assert editor.create(entry(start_time="10:00", end_time="11:00"))


def test_entries_with_separate_validity_dates_do_not_clash(editor):
    editor.create(entry())
    assert editor.create(entry(effective_from="2027-01-01", effective_to="2027-02-01"))


def test_an_entry_does_not_clash_with_itself(editor):
    created = editor.create(entry())
    assert editor.update(created["id"], entry(end_time="10:30"))["end_time"].startswith("10:30")


def clashing_rows(editor):
    """Two seeded sections share a teacher at the same time, because imports are not clash-checked."""
    section_a = editor.rows("AIML", 3, "A")[0]
    twin = next(
        r
        for r in editor.rows("AIML", 3, "B")
        if (r["day"], r["start_time"], r["faculty_code"])
        == (section_a["day"], section_a["start_time"], section_a["faculty_code"])
    )
    return section_a, twin


def fields_of(row, **changes):
    return TimetableData(**{**{k: row[k] for k in SLOT}, **changes})


def test_existing_import_clash_does_not_block_unrelated_edits(editor):
    row, _ = clashing_rows(editor)
    assert editor.update(row["id"], fields_of(row, subject="Renamed"))["subject"] == "Renamed"


def test_edit_cannot_add_a_new_clash_to_an_entry_that_already_has_one(editor):
    row, twin = clashing_rows(editor)
    # The room is new competition with the twin section, even though the teacher clash is old.
    editor.update(twin["id"], fields_of(twin, room="LAB-9"))
    with pytest.raises(ValueError, match="Room LAB-9 is already in use"):
        editor.update(row["id"], fields_of(row, room="LAB-9"))


def test_moving_an_entry_onto_a_busy_teacher_is_rejected(editor):
    row, _ = clashing_rows(editor)
    blocker = editor.create(entry(section="C", start_time="09:00", end_time="10:00"))
    with pytest.raises(ValueError, match="already teaches Robotics Lab"):
        editor.update(
            row["id"],
            fields_of(
                row,
                faculty_code=blocker["faculty_code"],
                day=5,
                start_time="09:00",
                end_time="10:00",
                room="LAB-8",
            ),
        )


def test_new_entries_still_clash_with_imported_rows(editor):
    row, _ = clashing_rows(editor)
    with pytest.raises(ValueError, match="already teaches"):
        editor.create(fields_of(row, section="Z", room="LAB-9"))


def test_faculty_search_includes_research_areas(env):
    assert any(f["code"] == "AIML-1" for f in env.service.faculty(query="education"))


def test_unknown_faculty_is_rejected(editor):
    with pytest.raises(ValueError, match="faculty member was not found"):
        editor.create(entry(faculty_code="NOPE-1"))


def test_student_is_denied_everywhere(env, student):
    denied = TimetableEditor(student, env.store)
    row = seeded_row(env)
    calls = [
        lambda: denied.rows("AIML", 3, "A"),
        lambda: denied.create(entry()),
        lambda: denied.update(row.id, entry()),
        lambda: denied.delete(row.id),
    ]
    for call in calls:
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 403
    assert env.db.get(CampusRecord, row.id) is not None


def test_department_admin_edits_own_department(env, department_admin):
    assert TimetableEditor(department_admin, env.store).create(entry())["department"] == "AIML"


def test_department_admin_is_denied_on_another_department(env, department_admin):
    denied = TimetableEditor(department_admin, env.store)
    for call in (
        lambda: denied.rows("CSE", 3, "A"),
        lambda: denied.create(entry(department="CSE", faculty_code="CSE-1")),
    ):
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 403


def test_department_admin_cannot_move_an_entry_to_another_department(env, department_admin):
    created = TimetableEditor(department_admin, env.store).create(entry())
    with pytest.raises(HTTPException) as error:
        TimetableEditor(department_admin, env.store).update(
            created["id"], entry(department="CSE", faculty_code="CSE-1")
        )
    assert error.value.status_code == 403


def test_department_admin_cannot_reach_another_departments_entry(env, department_admin):
    row = seeded_row(env, "CSE")
    denied = TimetableEditor(department_admin, env.store)
    for call in (lambda: denied.update(row.id, entry()), lambda: denied.delete(row.id)):
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 404


def test_other_tenant_entry_is_not_found(env, editor):
    foreign = env.db.scalar(
        select(CampusRecord).where(
            CampusRecord.tenant_id == env.other_tenant, CampusRecord.kind == "timetable"
        )
    )
    for call in (lambda: editor.update(foreign.id, entry()), lambda: editor.delete(foreign.id)):
        with pytest.raises(HTTPException) as error:
            call()
        assert error.value.status_code == 404
    assert env.db.get(CampusRecord, foreign.id).payload["room"] != SLOT["room"]


def test_other_tenant_is_invisible_to_clash_checks_and_rows(env, editor):
    foreign = env.db.scalar(
        select(CampusRecord).where(
            CampusRecord.tenant_id == env.other_tenant, CampusRecord.kind == "timetable"
        )
    )
    assert foreign.id not in {r["id"] for r in editor.rows("AIML", 3, "A")}


def test_api_round_trip(client):
    created = client.post("/v1/admin/timetable", json=SLOT)
    assert created.status_code == 200
    body = created.json()
    assert body["subject"] == "Robotics Lab"
    assert body["faculty"].startswith("Dr. ")
    listed = client.get("/v1/admin/timetable", params=dict(department="AIML", semester=3, section="A"))
    assert body["id"] in {r["id"] for r in listed.json()}
    updated = client.patch(f"/v1/admin/timetable/{body['id']}", json={**SLOT, "room": "LAB-8"})
    assert updated.json()["room"] == "LAB-8"
    assert client.delete(f"/v1/admin/timetable/{body['id']}").json() == {"deleted": True}


def test_api_reports_clashes_and_bad_input(client):
    assert client.post("/v1/admin/timetable", json=SLOT).status_code == 200
    clash = client.post("/v1/admin/timetable", json=SLOT)
    assert clash.status_code == 400
    assert "already" in clash.json()["detail"]
    invalid = client.post("/v1/admin/timetable", json={**SLOT, "start_time": "11:00"})
    assert invalid.status_code == 422
    extra = client.post("/v1/admin/timetable", json={**SLOT, "owner_id": "x"})
    assert extra.status_code == 422


def test_api_requires_a_verified_session():
    anonymous = TestClient(main.app)
    assert anonymous.get(
        "/v1/admin/timetable", params=dict(department="AIML", semester=3, section="A")
    ).status_code in (401, 403)
    assert anonymous.post("/v1/admin/timetable", json=SLOT).status_code in (401, 403)
    assert anonymous.delete("/v1/admin/timetable/x").status_code in (401, 403)


def test_api_denies_students(client, env):
    main.app.dependency_overrides[actor] = lambda: replace(env.service.actor, role="student")
    response = client.post("/v1/admin/timetable", json=SLOT)
    assert response.status_code == 403
    env.db.flush()
    assert not env.db.scalars(select(AuditLog).where(AuditLog.action.like("timetable.%"))).all()


def test_manual_source_can_be_downloaded(client):
    created = client.post("/v1/admin/timetable", json=SLOT).json()
    download = client.get(f"/v1/sources/{created['source_id']}")
    assert download.status_code == 200
    assert download.content == MANUAL_NOTE
    assert "Manual%20timetable%20edits" in download.headers["Content-Disposition"]


def test_faculty_profile_fields_are_seeded_and_returned(env):
    profile = env.service.faculty(code="AIML-1")[0]
    assert profile["qualification"] == "PhD"
    assert profile["experience"] == "5 years"
    assert profile["research_areas"]
    assert profile["bio"].startswith("Demo Data")
    assert env.db.scalar(select(Membership).where(Membership.user_id == env.user))
