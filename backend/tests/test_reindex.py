from sqlalchemy import select, func
from backend.app.ingestion import Ingestion
from backend.app.models import Document, DocumentChunk
from backend.app.schemas import ImportOptions
from backend.tests.conftest import publish


def test_reindex_replaces_chunks_without_duplicating_document(env):
    ingestion = Ingestion(env.service, env.store)
    job = ingestion.receive(
        "reindex.txt",
        b"Reindex verification: campus laboratories close at 17:00.",
        ImportOptions(kind="document", title="Lab hours"),
    )
    publish(env, job)
    env.db.flush()
    document = env.db.scalar(select(Document).where(Document.source_id == job.source_id))
    first_ids = set(env.db.scalars(select(DocumentChunk.id).where(DocumentChunk.document_id == document.id)))
    job.options = {**job.options, "reindex": True}
    job.status = "queued"
    ingestion.process(job)
    env.db.flush()
    second_ids = set(env.db.scalars(select(DocumentChunk.id).where(DocumentChunk.document_id == document.id)))
    assert second_ids and not first_ids & second_ids
    assert (
        env.db.scalar(select(func.count()).select_from(Document).where(Document.source_id == job.source_id))
        == 1
    )
