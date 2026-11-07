import json
from sqlalchemy import select
from backend.app.models import Membership, Source
from backend.tests.test_auth_api import client as client, key as key


def change_role(env, role):
    member = env.db.scalar(select(Membership).where(Membership.user_id == env.user, Membership.tenant_id == env.tenant))
    member.role, member.department, member.semester, member.section = role, "AIML", 3, "A"
    env.db.commit()


def test_section_scope_is_enforced_on_direct_http_upload(client, env, tmp_path, monkeypatch):
    from backend.app import storage
    monkeypatch.setattr(storage,"ROOT",tmp_path)
    change_role(env,"section_admin")
    for section, expected in (("B",403),("A",200)):
        response = client.post("/v1/ingestion/preview", data={"options":json.dumps({"kind":"document",
            "department":"AIML","semester":3,"section":section})},
            files={"file":("section.txt",b"A source-scoped official notice.","text/plain")})
        assert response.status_code == expected
    assert client.get("/v1/admin/memberships").status_code == 403


def test_canteen_manager_cannot_upload_documents_or_assign_roles(client, env):
    change_role(env,"canteen_manager")
    response = client.post("/v1/ingestion/preview",data={"options":json.dumps({"kind":"document"})},
        files={"file":("forbidden.txt",b"Shared official information.","text/plain")})
    assert response.status_code == 403
    assert client.get("/v1/admin/memberships").status_code == 403
    assert client.get("/v1/admin/canteen-sources").status_code == 200


def test_published_canteen_editing_returns_validated_unavailable_rows_without_raw_columns(client, env):
    from backend.app.auth import resolve_actor
    from backend.app.services import CampusService
    from backend.app.ingestion import Ingestion
    from backend.app.schemas import ImportOptions
    from backend.app.publication import Publication
    author = CampusService(env.db,resolve_actor(env.db,env.reviewer,env.tenant),clock=env.service.clock)
    pipeline = Ingestion(author,env.store)
    row = dict(name="Safe menu editing",price=30,vegetarian=True,category="Snack",location="Campus Canteen",
        available=False,private_note="Unmapped original column must not be returned")
    job = pipeline.receive("original.csv.json",json.dumps([row]).encode(),ImportOptions(kind="menu"))
    pipeline.confirm(job)
    pipeline.process(job)
    job.status = "completed"
    env.db.flush()
    Publication(env.service).transition(job.source_id,"approve")
    Publication(author).transition(job.source_id,"publish")
    env.db.commit()
    change_role(env,"canteen_manager")
    response = client.get(f"/v1/admin/canteen-sources/{job.source_id}/rows")
    assert response.status_code == 200
    assert response.json()["records"][0]["available"] is False
    assert "private_note" not in response.text and row["private_note"] not in response.text
    assert client.get(f"/v1/sources/{job.source_id}").status_code == 403
    assert env.db.get(Source,job.source_id).publication_status == "published"


def test_section_document_reindex_preserves_source_scope(client, env):
    from backend.app.auth import resolve_actor
    from backend.app.services import CampusService
    from backend.app.ingestion import Ingestion, work_once
    from backend.app.schemas import ImportOptions
    from backend.app.models import IngestionJob
    from backend.tests.test_product_workflows import finish
    change_role(env,"section_admin")
    author = CampusService(env.db,resolve_actor(env.db,env.user,env.tenant),clock=env.service.clock)
    job = Ingestion(author,env.store).receive("reindex-section.txt",b"A reviewed section-specific source for reindexing.",
        ImportOptions(kind="document",department="AIML",semester=3,section="A"))
    finish(env,author,job)
    response = client.post(f"/v1/sources/{job.source_id}/reindex")
    assert response.status_code == 200
    queued = env.db.get(IngestionJob,response.json()["id"])
    assert queued.options["semester"] == 3 and queued.options["section"] == "A"
    assert work_once(env.factory,env.store)
    env.db.expire_all()
    assert queued.status == "completed"
