"""Selected-document evidence, lifecycle, conversation and metadata boundaries."""

import pytest
from fastapi import HTTPException
from sqlalchemy import select, delete

from backend.app.auth import resolve_actor
from backend.app.ingestion import Ingestion
from backend.app.models import Document, DocumentChunk, Source
from backend.app.publication import Publication
from backend.app.retrieval import Retrieval
from backend.app.schemas import ImportOptions
from backend.app.services import CampusService
from backend.tests.conftest import publish
from backend.tests.test_auth_api import client as client, key as key, token


def pdf_document(env, name="Alpha", day="Friday", department="All", private=False):
    import fitz

    pdf = fitz.open()
    pdf.new_page().insert_text((50, 50), f"Zirconium scholarship {name} handbook and eligibility.")
    pdf.new_page().insert_text((50, 50), f"Zirconium scholarship deadline is {day}. Reference {name}.")
    payload = pdf.tobytes()
    pdf.close()
    ingestion = Ingestion(env.service, env.store)
    job = ingestion.receive(name + ".pdf", payload, ImportOptions(kind="document", title=name,
        topic="selected-scope", claim=day, department=department, visibility="private" if private else "shared"))
    if private:
        ingestion.confirm(job)
        ingestion.process(job)
        job.status = "completed"
        env.db.flush()
    else:
        publish(env, job)
    document = env.db.scalar(select(Document).where(Document.source_id == job.source_id))
    return document, job


def actor_service(env, user=None, tenant=None):
    return CampusService(env.db, resolve_actor(env.db, user or env.other, tenant or env.tenant),
                         clock=env.service.clock)


def test_similar_pdfs_are_isolated_in_ranking_conflicts_and_page_citations(env):
    first, _ = pdf_document(env)
    second, _ = pdf_document(env, "Beta", "Tuesday")
    retrieval = Retrieval(actor_service(env))
    general = retrieval.search("Zirconium scholarship deadline")
    assert general["conflicts"]
    result = retrieval.search("Zirconium scholarship deadline", document_id=first.id)
    assert result["hits"] and not result["conflicts"]
    assert {h["document_id"] for h in result["hits"]} == {first.id}
    assert {h["source_id"] for h in result["hits"]} == {first.source_id}
    assert any(h["page"] == 2 and "Friday" in h["text"] for h in result["hits"])
    assert all(h["id"] and h["title"] == "Alpha" for h in result["hits"])
    assert second.id not in str(result) and "Tuesday" not in str(result)


@pytest.mark.parametrize("scope", ["tenant", "department", "other_owner", "administrator"])
def test_unauthorized_selection_fails_before_embedding_and_hides_metadata(env, monkeypatch, scope):
    document, _ = pdf_document(env, name="Confidential title", department="AIML" if scope == "department" else "All",
                               private=scope in ("other_owner", "administrator"))
    service = actor_service(env, user=env.reviewer if scope == "administrator" else env.other,
                            tenant=env.other_tenant if scope == "tenant" else env.tenant)
    from backend.app import retrieval

    monkeypatch.setattr(retrieval, "embeddings", lambda _: pytest.fail("Unauthorized embedding work"))
    with pytest.raises(HTTPException) as exc:
        Retrieval(service).search("zirconium", document_id=document.id)
    assert exc.value.status_code == 404
    assert "Confidential title" not in str(exc.value.detail)
    assert document.id not in str(service.selectable_documents())


@pytest.mark.parametrize("state", ["draft", "pending_review", "approved", "revoked", "replaced", "deleted"])
def test_unavailable_documents_are_not_selectable_or_retrievable(env, state):
    document, job = pdf_document(env)
    if state == "deleted":
        env.db.delete(document)
    else:
        env.db.get(Source, job.source_id).publication_status = state
    env.db.flush()
    service = actor_service(env)
    assert document.id not in str(service.selectable_documents())
    with pytest.raises(HTTPException) as exc:
        Retrieval(service).search("zirconium", document_id=document.id)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("empty_chunks", [False, True])
def test_selected_document_insufficient_evidence_never_falls_back_to_other_documents(env, empty_chunks):
    document, _ = pdf_document(env)
    pdf_document(env, "Beta", "Tuesday")
    if empty_chunks:
        env.db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        env.db.flush()
    result = Retrieval(actor_service(env)).search("xylophonicquasarunrecorded", document_id=document.id)
    assert result["missing"] and result["hits"] == [] and result["conflicts"] == []
    assert "Insufficient evidence" in result["answer"]


def test_document_pagination_is_authorized_and_owner_can_select_private_document(env):
    private, _ = pdf_document(env, private=True)
    assert private.id in {d["id"] for d in env.service.selectable_documents()["documents"]}
    assert Retrieval(env.service).search("zirconium", document_id=private.id)["hits"]
    service = actor_service(env)
    first = service.selectable_documents(limit=1)
    second = service.selectable_documents(limit=1, offset=first["next_offset"])
    assert len(first["documents"]) == len(second["documents"]) == 1
    assert first["documents"][0]["id"] != second["documents"][0]["id"]
    assert private.id not in str(first) + str(second)


def test_chat_followups_switch_clear_restore_and_citations(client, env):
    first, _ = pdf_document(env)
    second, _ = pdf_document(env, "Beta", "Tuesday")
    env.db.commit()
    reply = client.post("/v1/chat", json={"question": "Zirconium scholarship deadline", "document_id": first.id})
    assert reply.status_code == 200
    body = reply.json()
    cid = body["conversation_id"]
    assert body["selected_document"]["id"] == first.id and body["evidence_status"] == "retrieved_evidence"
    assert body["sources"][0]["document_id"] == first.id and 2 in body["sources"][0]["pages"]
    assert body["sources"][0]["chunk_ids"]
    for question in ("Tell me more about that", "When is it?"):
        followup = client.post("/v1/chat", json={"question": question, "conversation_id": cid}).json()
        assert followup["selected_document"]["id"] == first.id
        assert {r["document_id"] for r in followup["records"]} == {first.id}
    saved = client.get(f"/v1/conversations/{cid}/selection").json()
    assert saved["document"]["id"] == first.id and not saved["unavailable"]
    assert client.patch(f"/v1/conversations/{cid}/selection", json={"document_id": second.id}).status_code == 200
    assert client.get(f"/v1/conversations/{cid}/selection").json()["document"]["id"] == second.id
    switched = client.post("/v1/chat", json={"question": "deadline", "conversation_id": cid,
                                          "document_id": second.id}).json()
    assert "Tuesday" in switched["answer"] and "Friday" not in switched["answer"]
    assert all(r["document_id"] == second.id for r in switched["records"])
    cleared = client.post("/v1/chat", json={"question": "My next class", "conversation_id": cid,
                                          "document_id": None}).json()
    assert cleared["selected_document"] is None and cleared["kind"] == "classes"
    assert client.get(f"/v1/conversations/{cid}/selection").json() == {"document": None, "unavailable": False}
    assert client.patch(f"/v1/conversations/{cid}/selection", json={"document_id": first.id}).status_code == 200
    assert client.patch(f"/v1/conversations/{cid}/selection", json={"document_id": None}).status_code == 200
    assert client.get(f"/v1/conversations/{cid}/selection").json()["document"] is None


def test_revoked_selection_and_empty_answer_metadata_are_rechecked_on_history(client, env, key):
    document, job = pdf_document(env)
    env.db.commit()
    body = client.post("/v1/chat", json={"question": "xylophonicquasarunrecorded", "document_id": document.id}).json()
    cid = body["conversation_id"]
    assert body["evidence_status"] == "insufficient_evidence" and not body["sources"]
    Publication(env.service).transition(job.source_id, "revoke", "Selection withdrawn")
    env.db.commit()
    assert client.get(f"/v1/conversations/{cid}/selection").json() == {"document": None, "unavailable": True}
    assert all(m["role"] != "assistant" for m in client.get(f"/v1/conversations/{cid}").json())
    denied = client.post("/v1/chat", json={"question": "Tell me more", "conversation_id": cid})
    assert denied.status_code == 404 and document.title not in denied.text
    assert client.post("/v1/chat", json={"question": "My next class", "conversation_id": cid,
                                      "document_id": None}).status_code == 200
    client.headers["Authorization"] = "Bearer " + token(key, env.other)
    assert client.get(f"/v1/conversations/{cid}/selection").status_code == 404
    assert client.patch(f"/v1/conversations/{cid}/selection", json={"document_id": None}).status_code == 404


def test_http_document_list_and_direct_tool_reject_cross_owner_selection(client, env, key):
    document, _ = pdf_document(env, private=True)
    env.db.commit()
    client.headers["Authorization"] = "Bearer " + token(key, env.other)
    assert document.id not in client.get("/v1/documents").text
    assert client.post("/v1/tools/search_documents", json={"query": "zirconium", "document_id": document.id}).status_code == 404
    assert client.post("/v1/chat", json={"question": "zirconium", "document_id": document.id}).status_code == 404
    own = client.post("/v1/chat", json={"question": "My next class", "document_id": None}).json()
    endpoint = f"/v1/conversations/{own['conversation_id']}/selection"
    assert client.patch(endpoint, json={"document_id": document.id}).status_code == 404
    assert client.get(endpoint).json()["document"] is None
    assert client.patch(endpoint, json={"document_id": None, "user_id": env.user}).status_code == 422
    client.headers.pop("Authorization")
    assert client.get("/v1/documents").status_code == 401
