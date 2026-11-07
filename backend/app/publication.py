"""Shared-source review and atomic publication. Private sources are never shared here."""

from fastapi import HTTPException
from sqlalchemy import select, update, text, or_
from sqlalchemy.exc import DBAPIError
from backend.app.auth import resolve_actor, ADMIN_ROLES, TOP_ROLES
from backend.app.models import Source, IngestionJob, Membership, AuditLog, utcnow
from backend.app.ingestion import Ingestion
from backend.app.db import serialize_sqlite_writes
from backend.app.cache import touch


class Publication:
    def __init__(self, service):
        self.c = service

    def sources(self, limit=100, offset=0):
        a = self.c.actor
        a.require_admin()
        stmt = select(Source).where(Source.tenant_id == a.tenant_id, Source.owner_id.is_(None))
        if a.role not in TOP_ROLES:
            if a.role == "canteen_manager":
                stmt = stmt.where(Source.kind == "menu", Source.department == "All")
            else:
                stmt = stmt.where(Source.department == a.department)
                if a.role == "section_admin":
                    stmt = stmt.where(Source.semester == a.semester, Source.section == a.section)
            stmt = stmt.where(or_(Source.kind == "document", Source.uploaded_by == a.user_id))
        return self.c.db.scalars(stmt.order_by(Source.created_at.desc(), Source.id).limit(limit).offset(offset)).all()

    def describe(self, source):
        from backend.app.models import Document
        document = self.c.db.scalar(select(Document).where(Document.source_id==source.id,Document.tenant_id==source.tenant_id)) if source.kind=='document' else None
        replacement = self.c.db.scalar(select(Source.id).where(Source.replaces_source_id==source.id,
            Source.tenant_id==source.tenant_id,Source.publication_status=="published")) if source.publication_status=='replaced' else None
        state = source.publication_status
        actions = []
        if source.file_state != "active":
            state = source.file_state
        if source.owner_id is None:
            if (
                state == "pending_review"
            ):
                actions.append("approve")
            if state == "approved":
                actions.append("publish")
            if state in ("pending_review", "approved"):
                actions.append("reject")
            if state in ("draft", "pending_review", "approved", "published"):
                actions.append("revoke")
        return dict(
            id=source.id,
            filename=source.filename,
            kind=source.kind,
            department=source.department,
            semester=source.semester,
            section=source.section,
            demo=source.demo,
            created_at=source.created_at.isoformat(),
            status=state,
            publication_status=source.publication_status,
            student_records=source.student_records,
            version=document.version if document else None,
            replaced_by=replacement,
            file_state=source.file_state,
            approved_by=source.approved_by,
            published_by=source.published_by,
            approved_at=source.approved_at.isoformat() if source.approved_at else None,
            published_at=source.published_at.isoformat() if source.published_at else None,
            review_note=source.review_note,
            replaces_source_id=source.replaces_source_id,
            original_download_allowed=self.c.original_download_allowed(source),
            actions=actions,
        )

    def job(self, source):
        return self.c.db.scalar(
            select(IngestionJob)
            .where(
                IngestionJob.tenant_id == source.tenant_id,
                IngestionJob.source_id == source.id,
            )
            .order_by(IngestionJob.created_at.desc())
            .limit(1)
        )

    def review(self, source_id):
        self.c.actor.require_admin()
        source = self.c.management_source(source_id)
        if source.owner_id is not None:
            raise HTTPException(404, "Private sources are not part of shared review.")
        job = self.job(source)
        return {
            **self.describe(source),
            "validation": job.preview if job else {},
            "job_status": job.status if job else None,
            "job_error": job.error if job else None,
        }

    def _admin_still_authorized(self, user_id, source, reviewer=False):
        m = self.c.db.scalar(
            select(Membership)
            .where(
                Membership.tenant_id == source.tenant_id,
                Membership.user_id == user_id,
                Membership.active.is_(True),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            not m
            or m.role not in ADMIN_ROLES
        ):
            raise HTTPException(
                403, "The uploader or reviewer no longer has administrator permission for this source."
            )
        from backend.app.auth import Actor
        Actor(m.user_id, m.tenant_id, m.role, m.department, m.semester, m.section, m.name).check_source_write(source)

    def transition(self, source_id, action, reason=""):
        # Keep the state swap and domain activation atomic even if an internal
        # caller catches an error and continues using its outer transaction.
        serialize_sqlite_writes(self.c.db)
        try:
            with self.c.db.begin_nested():
                return self._transition(source_id, action, reason)
        except Exception:
            # Explicit SQL + refresh() does not mark Source as ORM-dirty. A
            # savepoint rollback alone can therefore leave a refreshed status
            # cached even though the database correctly rolled it back.
            self.c.db.expire_all()
            raise

    def _transition(self, source_id, action, reason):
        c = self.c
        # Refresh membership even when a service object was retained by an internal caller.
        c.actor = resolve_actor(c.db, c.actor.user_id, c.actor.tenant_id)
        c.actor.require_admin()
        postgres = c.db.bind.dialect.name == "postgresql"
        # PostgreSQL requires UPDATE privilege even for SELECT ... FOR UPDATE.
        # The restricted API must not have that grant: campus_source_transition
        # takes the row lock and rechecks state/roles before any activation below.
        source = c.management_source(source_id, lock=not postgres)
        if postgres:
            c.db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                         {"key": c.actor.tenant_id+":"+source.kind})
        if postgres:
            c.db.refresh(source)
        if source.owner_id is not None:
            raise HTTPException(404, "Private sources cannot be published as institutional information.")
        if source.replaces_source_id and c.actor.role == "canteen_manager":
            c.canteen_source(source.replaces_source_id)
        expected = source.publication_status
        if source.file_state != "active":
            raise HTTPException(409, "Deleted files cannot enter publication review.")
        target = {
            "approve": "approved",
            "publish": "published",
            "reject": "rejected",
            "revoke": "revoked",
        }.get(action)
        allowed = {
            "approve": {"pending_review"},
            "publish": {"approved"},
            "reject": {"pending_review", "approved"},
            "revoke": {"draft", "pending_review", "approved", "published"},
        }
        if not target or expected not in allowed[action]:
            raise HTTPException(409, "This source is no longer in the required publication state.")
        if action in ("reject", "revoke") and not reason.strip():
            raise ValueError("Give a reason for rejecting or revoking this source.")
        job = self.job(source)
        if action in ("approve", "publish") and (not job or job.status != "completed"):
            raise HTTPException(
                409, "Successful worker processing is required before approval or publication."
            )
        if action in ('approve','publish') and job.preview.get('requires_demo') and not source.demo:
            raise HTTPException(409,'This source contains sample/test content and must remain labeled Demo Data.')
        if action in ('approve','publish') and source.kind=='events' and job.options.get('event_workflow'):
            from backend.app.models import Document,DocumentChunk
            from sqlalchemy import func
            indexed=c.db.scalar(select(func.count()).select_from(DocumentChunk).join(Document,Document.id==DocumentChunk.document_id)
                .where(Document.source_id==source.id,Document.tenant_id==source.tenant_id,
                       DocumentChunk.tenant_id==source.tenant_id,DocumentChunk.embedding.is_not(None),
                       DocumentChunk.embedding_model.like('onnx-e5-%')))
            if not job.preview.get('event_index_ready') or not indexed:
                raise HTTPException(409,'Event semantic indexing has not succeeded. Retry indexing before publication.')
        if action in ("approve", "publish"):
            from backend.app.duplicates import duplicate_report
            report = duplicate_report(c, source, job.staged_data.get("records"), job.options.get("logical_key") or job.options.get("topic", ""))
            if report["blocking"]:
                raise HTTPException(409, "Published information conflicts. Select its explicit replacement when uploading a reviewed version.")

        if c.db.bind.dialect.name == "postgresql":
            # The API has no general UPDATE grant on sources. The database function
            # enforces the same state machine, including independent/current roles.
            try:
                c.db.execute(
                    text("SELECT campus_source_transition(:sid,:expected,:target,:reason)"),
                    dict(sid=source.id, expected=expected, target=target, reason=reason),
                )
            except DBAPIError as exc:
                state = getattr(exc.orig, "sqlstate", None)
                if state not in ("42501", "22023"):
                    raise
                raise HTTPException(
                    403 if state == "42501" else 409,
                    "Publication refused. Check current roles and source state, then refresh.",
                ) from exc
            c.db.refresh(source)
        else:
            if action in ("approve", "publish"):
                self._admin_still_authorized(source.uploaded_by, source)
            if action == "publish":
                if not source.approved_by:
                    raise HTTPException(403, "Authorized approval is required.")
                self._admin_still_authorized(source.approved_by, source, reviewer=True)
            values = dict(publication_status=target, review_note=reason)
            if action == "approve":
                values.update(approved_by=c.actor.user_id, approved_at=utcnow())
            if action == "publish":
                values.update(published_by=c.actor.user_id, published_at=utcnow())
            # Compare-and-set also serializes SQLite writers before activation.
            changed = c.db.execute(
                update(Source)
                .where(
                    Source.id == source.id,
                    Source.tenant_id == c.actor.tenant_id,
                    Source.publication_status == expected,
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            ).rowcount
            if changed != 1:
                raise HTTPException(409, "Source changed during review. Refresh and try again.")
            c.db.refresh(source)
            if action == "publish" and source.replaces_source_id:
                old = c.canteen_source(source.replaces_source_id) if c.actor.role == "canteen_manager" else c.management_source(source.replaces_source_id, lock=True)
                if (
                    old.publication_status != "published"
                    or old.owner_id is not None
                    or old.kind != source.kind
                    or old.department != source.department
                    or old.semester != source.semester or old.section != source.section
                ):
                    raise HTTPException(
                        409, "The replacement target is no longer a matching published source."
                    )

        if action == "publish" and source.kind != "document":
            rows = job.staged_data.get("records")
            if not rows:
                raise HTTPException(409, "Validated staged records are missing. Re-import this source.")
            Ingestion(c).activate_records(source, rows)
        if action == "publish" and source.replaces_source_id:
            c.db.flush()
            if c.db.bind.dialect.name == "postgresql":
                c.db.execute(text("SELECT campus_finish_replacement(:sid)"), {"sid": source.id})
            else:
                old.publication_status = "replaced"
        if action in ("reject", "revoke"):
            c.db.execute(
                update(IngestionJob)
                .where(
                    IngestionJob.source_id == source.id,
                    IngestionJob.tenant_id == source.tenant_id,
                    IngestionJob.status.in_(["received", "preview", "queued", "processing", "failed"]),
                )
                .values(status="cancelled")
            )
        c.db.add(
            AuditLog(
                tenant_id=c.actor.tenant_id,
                user_id=c.actor.user_id,
                action="source." + target,
                details={
                    "source_id": source.id,
                    "reason": reason,
                    "replaces_source_id": source.replaces_source_id,
                },
            )
        )
        c.db.flush()
        touch(c.db, c.actor.tenant_id)
        return self.describe(source)
