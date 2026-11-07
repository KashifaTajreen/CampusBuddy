"""Row evidence, real workbook preview and personal identifier boundaries."""
import io
import os
from pathlib import Path
from dataclasses import replace
import pytest
from sqlalchemy import select
from backend.app.models import Document, DocumentChunk, Membership, Source
from backend.app.ingestion import Ingestion
from backend.app.parsing import parse_file
from backend.app.retrieval import Retrieval
from backend.app.router import Router
from backend.app.schemas import ImportOptions
from backend.app.services import CampusService
from backend.tests.test_product_workflows import member, finish


def workbook():
    from openpyxl import Workbook
    b = Workbook()
    s = b.active
    s.title = "Team Details"
    s.append(["Team Number","Student 1 Name","Student 1 USN","Student 2 Name","Student 2 USN"])
    s.append([1,"Synthetic Lead","1RV25CI001","Synthetic Student","1RV25CI077"])
    s.append([2,"Other Student","1RV25CI088","Other Teammate","1RV25CI089"])
    s = b.create_sheet("Mentor Allocation")
    s.append(["Team No.","Team Lead USN","Project Title","Faculty Mentor"])
    s.append([1,"1RV25CI001","Synthetic robotics","Dr. Synthetic Mentor"])
    s.append([2,"1RV25CI088","Other project","Other Private Mentor"])
    out = io.BytesIO()
    b.save(out)
    return out.getvalue()


def student(env, number="1RV25CI077"):
    s = member(env,"student")
    m = env.db.scalar(select(Membership).where(Membership.user_id==s.actor.user_id))
    m.student_number = number
    env.db.commit()
    return CampusService(env.db,replace(s.actor,student_number=number),clock=env.service.clock)


def test_project_workbook_schema_mismatch_precedes_timetable_mapping(env):
    job = Ingestion(env.service,env.store).receive("project.xlsx",workbook(),ImportOptions(kind="timetable"))
    assert job.preview["schema_mismatch"] and job.preview["error_count"] == 1
    assert job.preview["suggested_kind"] == "document" and job.preview["fields"] == []
    assert job.options["kind"] == "timetable"  # No silent reinterpretation.
    with pytest.raises(ValueError):
        Ingestion(env.service,env.store).confirm(job)


def test_workbook_preview_preserves_sheets_headers_and_rows():
    preview = parse_file("project.xlsx",workbook(),preview_only=True)
    assert [t["name"] for t in preview["tables"]] == ["Team Details","Mentor Allocation"]
    assert preview["tables"][0]["rows"][0]["Student 2 USN"] == "1RV25CI077"
    assert "Faculty Mentor" in preview["tables"][1]["columns"]


def test_repeated_team_numbers_do_not_join_different_students_or_sections():
    from backend.app.document_rows import units
    parsed=units([
        dict(name="Students",page=1,rows=[{"Team Number":"1","Student USN":"1RV25CI077","section":"A"},
                                            {"Team Number":"1","Student USN":"1RV25CI088","section":"B"}]),
        dict(name="Mentors",page=2,rows=[{"Team No.":"1","Team Lead USN":"1RV25CI088","section":"B","Faculty Mentor":"Other Private Mentor"}])])
    assert parsed[0]["student_numbers"]==["1RV25CI077"]
    assert parsed[-1]["student_numbers"]==["1RV25CI088"]


def test_unknown_student_identifier_format_is_restricted_not_treated_public(env):
    job=Ingestion(env.service,env.store).receive("other-id.csv",b"Student ID,Faculty Mentor\nUNKNOWN-77,Private mentor\n",ImportOptions(kind="document"))
    assert env.db.get(Source,job.source_id).student_records


def test_a_usn_mentioned_in_description_is_not_a_row_access_grant():
    from backend.app.document_rows import units
    result=units([dict(name="Other team",page=1,rows=[{"Team No.":2,"Team Lead USN":"1RV25CI088",
        "Project Description":"A reference mentions 1RV25CI077","Faculty Mentor":"Other Private Mentor"}])])
    assert result[0]["student_numbers"]==["1RV25CI088"]


def test_mentor_notes_are_not_reinterpreted_as_an_assigned_mentor():
    from backend.app.document_rows import mentor_value
    assert mentor_value({"Mentor notes":"Good project","Mentor email":"private@example.invalid"}) is None
    assert mentor_value({"Faculty Mentor":"Dr. Synthetic Mentor"}) == "Dr. Synthetic Mentor"


def test_student_record_notice_metadata_does_not_offer_whole_workbook_download(env):
    own=student(env)
    job=Ingestion(env.service,env.store).receive("notice.xlsx",workbook(),ImportOptions(kind="document",
        published=env.service.local_now().date()))
    finish(env,env.service,job)
    row=next(r for r in own.notices() if r["source_id"]==job.source_id)
    assert row["original_download_allowed"] is False


def test_spreadsheet_row_relationships_exact_self_lookup_and_reindex(env):
    own = student(env)
    pipeline = Ingestion(env.service,env.store)
    job = pipeline.receive("project.xlsx",workbook(),ImportOptions(kind="document",department="AIML",semester=3,section="A"))
    assert env.db.get(Source,job.source_id).student_records
    assert not Retrieval(own).search("My skill lab mentor")['hits']
    finish(env,env.service,job)
    answer = Router(own).ask("What mentor is allotted to me for skill lab? My USN is 1rv25ci077.")
    assert "Dr. Synthetic Mentor" in answer["answer"]
    assert "Other Private Mentor" not in str(answer)
    assert answer["records"][0]["sheet"] == "Mentor Allocation"
    doc = env.db.scalar(select(Document).where(Document.source_id==job.source_id))
    hits = Retrieval(own).search("My mentor",document_id=doc.id)
    assert hits["mode"] == "exact_identifier"
    assert all("Other Private Mentor" not in c.text for c in own.db.scalars(own.scope(DocumentChunk)))
    assert not own.original_download_allowed(env.db.get(Source,job.source_id))
    before = [(c.student_numbers,c.row_data) for c in env.db.scalars(select(DocumentChunk).where(DocumentChunk.document_id==doc.id).order_by(DocumentChunk.id))]
    # Re-index keeps reviewed row metadata instead of reconstructing broad chunks.
    from backend.app.main import reindex_source
    result = reindex_source(job.source_id,env.service)
    reindex = pipeline.job(result["id"])
    pipeline.process(reindex)
    after = [(c.student_numbers,c.row_data) for c in env.db.scalars(select(DocumentChunk).where(DocumentChunk.document_id==doc.id))]
    assert sorted(map(str,before)) == sorted(map(str,after))


def test_arbitrary_or_unverified_usn_never_grants_access(env):
    own = student(env)
    job = Ingestion(env.service,env.store).receive("project.xlsx",workbook(),ImportOptions(kind="document"))
    finish(env,env.service,job)
    denied = Router(own).ask("What mentor is allotted to 1rv25ci088?")
    assert not denied["records"] and "Other Private Mentor" not in str(denied)
    assert "only your verified" in denied["answer"]
    unknown = member(env,"student")
    result = Router(unknown).ask("My USN is 1rv25ci077. Who is my skill lab mentor?")
    assert not result["records"] and "no verified USN" in result["answer"]


def test_student_rows_stay_unavailable_after_revocation_or_deletion(env):
    from backend.app.publication import Publication
    from backend.app.files import Files
    own=student(env)
    job=Ingestion(env.service,env.store).receive("lifecycle.xlsx",workbook(),ImportOptions(kind="document"))
    finish(env,env.service,job)
    document=env.db.scalar(select(Document).where(Document.source_id==job.source_id))
    assert Retrieval(own).search("My mentor",document_id=document.id)["hits"]
    Publication(env.service).transition(job.source_id,"revoke","Synthetic withdrawal")
    env.db.commit()
    assert not Retrieval(own).search("My skill lab mentor")["hits"]
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        own.selected_document(document.id)
    Files(env.service,env.store).change(job.source_id,"delete")
    env.db.commit()
    assert not list(env.db.scalars(own.scope(DocumentChunk).where(DocumentChunk.document_id==document.id)))


def test_reported_actual_workbook_local_preview_and_grounded_mentor(env):
    path = Path(os.environ.get('CAMPUS_PRIVATE_WORKBOOK_REFERENCE', str(Path(__file__).resolve().parents[2]/'.scratch/private-fixtures/skill-lab.xlsx')))
    if not path.exists():
        pytest.skip("Reported real campus workbook is not available locally; synthetic workbook regressions still run.")
    payload = path.read_bytes()
    parsed = parse_file(path.name,payload)
    preview = parse_file(path.name,payload,preview_only=True)
    assert len(preview["tables"]) == 3
    assert all(t["total_rows"] == 58 for t in preview["tables"])
    expected = [str(v) for u in parsed["document_units"] if "1RV25CI077" in u["student_numbers"]
                for k,v in u["values"].items() if "mentor" in k.lower() and str(v).strip()]
    assert bool(expected), "Reported student must have linked mentor evidence"
    own = student(env)
    job = Ingestion(env.service,env.store).receive(path.name,payload,ImportOptions(kind="document",department="AIML",semester=3,section="A"))
    finish(env,env.service,job)
    answer = Router(own).ask("What mentor is allotted to me for skill lab? My USN is 1rv25ci077.")
    assert bool(answer["sources"]) and bool(answer["records"])
    assert any(value in answer["answer"] for value in expected), "Answer must match the local workbook evidence"
