"""File lifecycle. Visibility changes commit before irreversible object deletion."""
from fastapi import HTTPException
from sqlalchemy import select, delete, text
from sqlalchemy.exc import DBAPIError
from backend.app.auth import resolve_actor
from backend.app.db import serialize_sqlite_writes, set_identity
from backend.app.models import Document, DocumentChunk, CampusRecord, IngestionJob, Message, Conversation, AuditLog
from backend.app.ingestion import Ingestion
from backend.app.publication import Publication
from backend.app.schemas import ImportOptions
from backend.app.storage import ObjectStore
from backend.app.cache import touch
from backend.app.parsing import parse_file

WITHDRAWN = dict(answer="Source withdrawn. Ask again using current published information.", kind="text", records=[], sources=[], followups=[])


class Files:
    def __init__(self, service, store=None):
        self.c = service
        self.store = store or ObjectStore()

    def change(self, source_id, action, confirmation="", replaces_source_id=None):
        c = self.c
        serialize_sqlite_writes(c.db)
        c.actor = resolve_actor(c.db, c.actor.user_id, c.actor.tenant_id)
        source = c.management_source(source_id, lock=c.db.bind.dialect.name != "postgresql")
        if action=='delete_draft':
            c.actor.require_admin()
            if source.publication_status!='draft' or source.file_state!='active' or source.published_at:
                raise HTTPException(409,'Only an active, unpublished draft can be deleted here.')
            if confirmation!='DELETE '+source.id:
                raise HTTPException(409,'Confirm deletion of this exact draft.')
            self._transition(source,'delete_draft',confirmation,None)
            return self.change(source_id,'purge',confirmation)
        if action == "purge":
            if confirmation != "DELETE " + source.id or source.file_state not in ("deleted", "purge_pending"):
                raise HTTPException(409, "Soft-delete first, then type DELETE followed by the exact source ID.")
            self._transition(source, "begin_purge", confirmation, None)
            c.db.commit()  # Durable tombstone before any irreversible storage operation.
            try:
                self.store.delete(source.storage_key)
            except Exception:
                raise HTTPException(503, "File remains hidden in purge_pending. Storage cleanup failed; explicitly retry permanent deletion.") from None
            # Reauthorize after the network boundary, and retain the tombstone if finalization fails.
            set_identity(c.db, c.actor.user_id, c.actor.tenant_id)
            c.actor = resolve_actor(c.db, c.actor.user_id, c.actor.tenant_id)
            source = c.management_source(source_id)
            self._transition(source, "finish_purge", confirmation, None)
            c.db.commit()
        else:
            if action in ("restore", "rollback"):
                jobs = c.db.scalars(select(IngestionJob).where(IngestionJob.source_id == source.id,
                    IngestionJob.tenant_id == c.actor.tenant_id).order_by(IngestionJob.created_at.desc())).all()
                options = next((j.options for j in jobs if j.options.get("kind") and not j.options.get("reindex")), None)
                if not options:
                    raise HTTPException(409, "Original import metadata is unavailable; upload a distinct reviewed version.")
                options = ImportOptions.model_validate({**options, "reindex": False, "supersedes_id": None,
                    "replaces_source_id": replaces_source_id if action == "rollback" else None})
                if source.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".pdf")):
                    options.ocr_extract_pending = parse_file(source.filename, self.store.get(source.storage_key), probe=True).get("needs_ocr", False)
                    options.ocr_reviewed = False
            with c.db.begin_nested():
                self._transition(source, action, confirmation, replaces_source_id)
                if action in ("restore", "rollback"):
                    job = IngestionJob(tenant_id=c.actor.tenant_id, source_id=source.id, requested_by=c.actor.user_id,
                        department=source.department, options=options.model_dump(mode="json"), status="received")
                    c.db.add(job)
                    c.db.flush()
                    Ingestion(c, self.store).preview(job)
        c.db.flush()
        return Publication(c).describe(source)

    def _transition(self, source, action, confirmation, replacement):
        c = self.c
        touch(c.db, c.actor.tenant_id)
        if c.db.bind.dialect.name == "postgresql":
            try:
                c.db.execute(text("SELECT campus_file_action(:sid,:act,:confirmation,:replacement)"),
                    dict(sid=source.id, act=action, confirmation=confirmation, replacement=replacement))
            except DBAPIError as exc:
                if getattr(exc.orig, "sqlstate", None) not in ("42501", "22023"):
                    raise
                raise HTTPException(409, "File action refused. Check current permissions, state and confirmation.") from exc
            c.db.refresh(source)
            return
        if action in ("delete","delete_draft") and source.file_state == "active" and (action!='delete_draft' or source.publication_status=='draft'):
            source.file_state, source.publication_status = "deleted", "revoked"
        elif action == "begin_purge" and source.file_state in ("deleted", "purge_pending"):
            source.file_state = "purge_pending"
        elif action == "finish_purge" and source.file_state == "purge_pending":
            source.file_state, source.filename, source.storage_key, source.review_note = "purged", "[purged]", "", ""
            source.checksum = "purged:" + source.id
            source.approved_by = source.approved_at = source.published_by = source.published_at = None
        elif (action == "restore" and source.file_state == "deleted") or (action == "rollback" and source.file_state == "active" and source.publication_status == "replaced"):
            if action == "rollback":
                previous = c.management_source(replacement, lock=True) if replacement else None
                if not previous or previous.id == source.id or previous.file_state != "active" or previous.publication_status != "published" or previous.owner_id is not None or previous.kind != source.kind or previous.department != source.department or previous.semester != source.semester or previous.section != source.section:
                    raise HTTPException(409, "Select a matching current published replacement.")
            source.file_state, source.publication_status, source.uploaded_by = "active", "draft", c.actor.user_id
            source.approved_by = source.approved_at = source.published_by = source.published_at = None
            source.review_note = ""
            source.replaces_source_id = replacement if action == "rollback" else None
        else:
            raise HTTPException(409, "This file cannot perform that lifecycle action.")
        docs = list(c.db.scalars(select(Document.id).where(Document.source_id == source.id, Document.tenant_id == c.actor.tenant_id)))
        if action in ("delete", "delete_draft", "begin_purge", "finish_purge", "rollback"):
            c.db.execute(delete(DocumentChunk).where(DocumentChunk.document_id.in_(docs), DocumentChunk.tenant_id == c.actor.tenant_id))
            c.db.execute(delete(CampusRecord).where(CampusRecord.source_id == source.id, CampusRecord.tenant_id == c.actor.tenant_id))
            for message in c.db.scalars(select(Message).where(Message.tenant_id == c.actor.tenant_id, Message.role == "assistant")):
                if source.id in str(message.content):
                    message.content = dict(WITHDRAWN)
            for conversation in c.db.scalars(select(Conversation).where(Conversation.tenant_id == c.actor.tenant_id)):
                if conversation.context.get("document_id") in docs:
                    conversation.context = {}
        for job in c.db.scalars(select(IngestionJob).where(IngestionJob.source_id == source.id, IngestionJob.tenant_id == c.actor.tenant_id)):
            job.status, job.preview, job.error = "cancelled", {}, None
            if action == "finish_purge":
                job.options, job.staged_data = {}, {}
        if action == "finish_purge":
            c.db.execute(delete(Document).where(Document.source_id == source.id, Document.tenant_id == c.actor.tenant_id))
        c.db.add(AuditLog(tenant_id=c.actor.tenant_id, user_id=c.actor.user_id, action="file."+action,
                        details={"source_id": source.id, "replaces_source_id": replacement}))
        c.db.flush()
