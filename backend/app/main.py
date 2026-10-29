import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from fastapi import FastAPI, Depends, File, Form, UploadFile, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response, JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.db import initialize_local, set_identity, validate_production_role, SessionLocal
from backend.app.auth import actor, authenticated_user, get_session, Actor, TOP_ROLES, rate_limit
from backend.app.models import (
    Institution,
    Membership,
    Conversation,
    Message,
    QueryTrace,
    Source,
    IngestionJob,
    AuditLog,
)
from backend.app.schemas import ChatRequest, ImportOptions, MappingRequest, Preferences, MembershipUpdate
from backend.app.services import CampusService
from backend.app.router import Router
from backend.app.tools import ToolKit, TOOLS
from backend.app.ingestion import Ingestion, MAX_BYTES
from backend.app.storage import ObjectStore
from backend.app.integrations import router as integration_router
from backend.app.mcp_server import mcp, AuthenticatedMCP

logger = logging.getLogger("campusos")
logging.basicConfig(level=logging.INFO, format="%(message)s")


@asynccontextmanager
async def lifespan(app):
    settings().validate_production()
    initialize_local()
    validate_production_role()
    from backend.app.mcp_server import mcp
    from backend.app.ingestion import work_once

    async def in_process_worker():
        logger.info("Embedded background worker started.")
        while True:
            try:
                worked = await asyncio.to_thread(work_once, SessionLocal)
                if not worked:
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"In-process worker error: {e}")
                await asyncio.sleep(5)

    worker_task = asyncio.create_task(in_process_worker())
    try:
        async with mcp.session_manager.run():
            yield
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="CampusOS Intelligence API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings().app_env != "production" else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings().frontend_origin, "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_origin_regex=r"https://.*\.vercel\.app|http://localhost:\d+|http://127\.0\.0\.1:\d+",
    allow_credentials=False,
    allow_private_network=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Request-ID"],
)


@app.middleware("http")
async def request_metadata(request: Request, call_next):
    request_id = str(uuid.uuid4())
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    # No query strings, request bodies, credentials, names, emails or source text.
    logger.info(
        json.dumps(
            {
                "request_id": request_id,
                "method": request.method,
                "status": response.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        )
    )
    return response


@app.exception_handler(ValueError)
async def value_error(_, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)[:500]})


@app.exception_handler(RequestValidationError)
async def validation_error(_, exc):
    errors = [{"field": ".".join(str(x) for x in e["loc"]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(
        status_code=422, content={"detail": "Check the highlighted fields.", "errors": errors}
    )


@app.exception_handler(Exception)
async def unknown_error(_, exc):
    logger.error(json.dumps({"event": "request_failed", "error_type": type(exc).__name__}))
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong. Try again or contact your institution administrator."},
    )


def service(a: Actor = Depends(actor), db: Session = Depends(get_session)):
    return CampusService(db, a)


@app.get("/health")
def health():
    return {"status": "ok", "auth_configured": bool(settings().supabase_url), "version": "0.1.0"}


@app.get("/v1/memberships")
def memberships(user_id: str = Depends(authenticated_user), db: Session = Depends(get_session)):
    set_identity(db, user_id, "")
    rows = db.execute(
        select(Membership, Institution)
        .join(Institution, Institution.id == Membership.tenant_id)
        .where(Membership.user_id == user_id, Membership.active.is_(True))
    ).all()
    return [
        dict(tenant_id=m.tenant_id, institution=i.name, role=m.role, name=m.name, demo=i.demo)
        for m, i in rows
    ]


@app.get("/v1/me")
def me(c: CampusService = Depends(service)):
    a = c.actor
    institution = c.db.get(Institution, a.tenant_id)
    member = c.db.scalar(
        select(Membership).where(Membership.tenant_id == a.tenant_id, Membership.user_id == a.user_id)
    )
    return dict(
        user_id=a.user_id,
        tenant_id=a.tenant_id,
        name=a.name,
        role=a.role,
        department=a.department,
        semester=a.semester,
        section=a.section,
        diet=a.diet,
        interests=member.interests,
        institution=institution.name,
        demo=institution.demo,
        timezone=str(c.zone),
    )


@app.patch("/v1/me")
def preferences(body: Preferences, c: CampusService = Depends(service)):
    m = c.db.scalar(
        select(Membership).where(
            Membership.tenant_id == c.actor.tenant_id, Membership.user_id == c.actor.user_id
        )
    )
    for key, value in body.model_dump().items():
        setattr(m, key, value)
    return {"saved": True}


@app.get("/v1/overview")
def overview(c: CampusService = Depends(service)):
    return dict(
        next_class=c.next_class(),
        classes=c.timetable(),
        notices=c.notices()[:3],
        events=c.events(days=7)[:3],
        exams=c.events(days=90, kind="exam")[:3],
    )


@app.get("/v1/timetable")
def timetable(on: date | None = None, week: bool = False, c: CampusService = Depends(service)):
    return c.week(on) if week else c.timetable(on)


@app.get("/v1/menu")
def menu(
    budget: float = 100,
    vegetarian: bool | None = None,
    min_protein: float = 0,
    query: str = "",
    sort: str = "price",
    c: CampusService = Depends(service),
):
    return c.menu(budget, vegetarian, min_protein, query[:200], sort)


@app.get("/v1/meals")
def meals(
    budget: float = 100, vegetarian: bool | None = None, count: int = 2, c: CampusService = Depends(service)
):
    return c.meals(budget, vegetarian, count)


@app.get("/v1/faculty")
def faculty(query: str = "", c: CampusService = Depends(service)):
    return c.faculty(query[:200])


@app.get("/v1/notices")
def notices(query: str = "", include_outdated: bool = False, c: CampusService = Depends(service)):
    return c.notices(query[:200], include_outdated)


@app.get("/v1/events")
def events(start: date | None = None, days: int = 14, query: str = "", c: CampusService = Depends(service)):
    return [dict(**e, can_attend=c.event_fit(e)["can_attend"]) for e in c.events(start, days, query[:200])]


@app.get("/v1/calendar")
def calendar(start: date | None = None, days: int = 7, c: CampusService = Depends(service)):
    if not 1 <= days <= 14:
        raise ValueError("Choose 1–14 calendar days.")
    from datetime import timedelta

    start = start or c.clock().date()
    return dict(
        classes=[r for i in range(days) for r in c.timetable(start + timedelta(days=i))],
        events=c.events(start, days),
        free_slots=c.free_slots(start),
        conflicts=c.conflicts(start, days),
    )


@app.get("/v1/sources/{source_id}")
def source_file(source_id: str, c: CampusService = Depends(service)):
    source = c.source(source_id)
    # Force download; never execute HTML or display an uploaded file in the application origin.
    data = ObjectStore().get(source.storage_key)
    from urllib.parse import quote

    return Response(
        data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(source.filename, safe=""),
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/v1/conversations")
def conversations(c: CampusService = Depends(service)):
    rows = c.db.scalars(
        select(Conversation)
        .where(Conversation.tenant_id == c.actor.tenant_id, Conversation.owner_id == c.actor.user_id)
        .order_by(Conversation.created_at.desc())
        .limit(30)
    )
    return [dict(id=r.id, title=r.title, created_at=r.created_at.isoformat()) for r in rows]


def conversation_for(c, conversation_id):
    row = c.db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == c.actor.tenant_id,
            Conversation.owner_id == c.actor.user_id,
        )
    )
    if not row:
        raise HTTPException(404, "Conversation not found.")
    return row


@app.get("/v1/conversations/{conversation_id}")
def conversation_history(conversation_id: str, c: CampusService = Depends(service)):
    row = conversation_for(c, conversation_id)
    # Context and historical answers may contain data from a previous department. Invalidate on scope change.
    scope = f"{c.actor.department}:{c.actor.semester}:{c.actor.section}:{c.actor.role}"
    if row.context.get("_scope") != scope:
        return []
    messages = c.db.scalars(
        select(Message)
        .where(
            Message.conversation_id == row.id,
            Message.tenant_id == c.actor.tenant_id,
            Message.owner_id == c.actor.user_id,
        )
        .order_by(Message.created_at)
    ).all()
    result = []
    for m in messages:
        if m.role == "assistant":
            try:
                for source in m.content.get("sources", []):
                    c.source(source["id"])
            except HTTPException:
                continue
        result.append(dict(role=m.role, content=m.content))
    return result


@app.delete("/v1/conversations/{conversation_id}")
def remove_conversation(conversation_id: str, c: CampusService = Depends(service)):
    row = conversation_for(c, conversation_id)
    c.db.delete(row)
    return {"deleted": True}


def answer_chat(body, c):
    if body.conversation_id:
        conversation = conversation_for(c, body.conversation_id)
    else:
        conversation = Conversation(
            tenant_id=c.actor.tenant_id, owner_id=c.actor.user_id, title=body.question[:80], context={}
        )
        c.db.add(conversation)
        c.db.flush()
    scope = f"{c.actor.department}:{c.actor.semester}:{c.actor.section}:{c.actor.role}"
    context = conversation.context if conversation.context.get("_scope") == scope else {}
    result = Router(c).ask(body.question, context)
    conversation.context = {**result.pop("context"), "_scope": scope}
    # Technical traces remain in the developer API, not student chat responses.
    result.pop("trace", None)
    result.pop("route", None)
    result.pop("latency_ms", None)
    result["conversation_id"] = conversation.id
    c.db.add_all(
        [
            Message(
                tenant_id=c.actor.tenant_id,
                conversation_id=conversation.id,
                owner_id=c.actor.user_id,
                role="user",
                content={"text": body.question},
            ),
            Message(
                tenant_id=c.actor.tenant_id,
                conversation_id=conversation.id,
                owner_id=c.actor.user_id,
                role="assistant",
                content=result,
            ),
        ]
    )
    c.db.flush()
    return result


@app.post("/v1/chat")
def chat(body: ChatRequest, c: CampusService = Depends(service)):
    return answer_chat(body, c)


@app.post("/v1/chat/stream")
def chat_stream(body: ChatRequest, c: CampusService = Depends(service)):
    result = answer_chat(body, c)
    c.db.commit()

    def stream():
        # Streams the computed grounded answer. No artificial delays or fabricated reasoning events.
        text = result["answer"]
        for i in range(0, len(text), 64):
            yield "event: delta\ndata: " + json.dumps({"text": text[i : i + 64]}) + "\n\n"
        yield "event: result\ndata: " + json.dumps(result) + "\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@app.get("/v1/tools")
def tool_schemas(c: CampusService = Depends(service)):
    return [
        dict(name=name, description=description, input_schema=schema.model_json_schema())
        for name, (schema, description) in TOOLS.items()
    ]


@app.post("/v1/tools/{name}")
def execute_tool(name: str, body: dict, c: CampusService = Depends(service)):
    toolkit = ToolKit(c)
    try:
        result = toolkit.call(name, **body)
    except Exception:
        c.db.add(
            QueryTrace(
                tenant_id=c.actor.tenant_id,
                user_id=c.actor.user_id,
                route="Tool execution",
                tools=toolkit.trace,
                latency_ms=sum(t["latency_ms"] for t in toolkit.trace),
                success=False,
                result_count=0,
            )
        )
        c.db.commit()
        raise
    c.db.add(
        QueryTrace(
            tenant_id=c.actor.tenant_id,
            user_id=c.actor.user_id,
            route="Tool execution",
            tools=toolkit.trace,
            latency_ms=sum(t["latency_ms"] for t in toolkit.trace),
            success=True,
            result_count=len(result) if isinstance(result, list) else 1,
        )
    )
    return {"data": result}


def job_json(job):
    return dict(
        id=job.id,
        source_id=job.source_id,
        status=job.status,
        preview=job.preview,
        error=job.error,
        attempts=job.attempts,
        created_at=job.created_at.isoformat(),
    )


@app.post("/v1/ingestion/preview")
async def import_preview(
    file: UploadFile = File(...), options: str = Form(...), c: CampusService = Depends(service)
):
    rate_limit(c.db, f"upload:{c.actor.tenant_id}:{c.actor.user_id}", 10)
    content = await file.read(MAX_BYTES + 1)
    try:
        parsed = ImportOptions.model_validate_json(options)
    except ValueError as exc:
        raise ValueError("Check the import settings and try again.") from exc
    from starlette.concurrency import run_in_threadpool

    job = await run_in_threadpool(Ingestion(c).receive, file.filename or "source", content, parsed)
    return job_json(job)


@app.post("/v1/ingestion/{job_id}/mapping")
def update_mapping(job_id: str, body: MappingRequest, c: CampusService = Depends(service)):
    ingestion = Ingestion(c)
    return job_json(ingestion.preview(ingestion.job(job_id), body.mapping))


@app.post("/v1/ingestion/{job_id}/confirm")
def confirm_import(job_id: str, c: CampusService = Depends(service)):
    ingestion = Ingestion(c)
    return job_json(ingestion.confirm(ingestion.job(job_id)))


@app.post("/v1/ingestion/{job_id}/retry")
def retry_import(job_id: str, c: CampusService = Depends(service)):
    ingestion = Ingestion(c)
    job = ingestion.job(job_id)
    if job.status != "failed":
        raise ValueError("Only failed jobs can be retried.")
    job.status = "received"
    ingestion.preview(job)
    return job_json(job)


@app.post("/v1/sources/{source_id}/reindex")
def reindex_source(source_id: str, c: CampusService = Depends(service)):
    source = c.source(source_id)
    c.actor.check_department_write(source.department)
    if source.kind != "document":
        raise ValueError("Re-indexing is available only for documents.")
    from backend.app.models import Document

    doc = c.db.scalar(c.scope(Document).where(Document.source_id == source.id))
    if not doc:
        raise ValueError("Import the document before re-indexing it.")
    pending = c.db.scalar(
        select(IngestionJob).where(
            IngestionJob.tenant_id == c.actor.tenant_id,
            IngestionJob.source_id == source.id,
            IngestionJob.status.in_(["queued", "processing"]),
        )
    )
    if pending:
        raise ValueError("An indexing job is already running for this source.")
    options = ImportOptions(
        kind="document",
        department=doc.department,
        title=doc.title,
        category=doc.category,
        published=doc.published,
        expires=doc.expires,
        topic=doc.topic,
        claim=doc.claim,
        reindex=True,
        demo=source.demo,
    )
    job = IngestionJob(
        tenant_id=c.actor.tenant_id,
        source_id=source.id,
        requested_by=c.actor.user_id,
        department=source.department,
        status="queued",
        options=options.model_dump(mode="json"),
        preview={},
    )
    c.db.add(job)
    c.db.flush()
    return job_json(job)


@app.get("/v1/ingestion")
def jobs(c: CampusService = Depends(service)):
    rows = c.db.scalars(
        select(IngestionJob)
        .where(IngestionJob.tenant_id == c.actor.tenant_id, IngestionJob.requested_by == c.actor.user_id)
        .order_by(IngestionJob.created_at.desc())
        .limit(50)
    )
    return [job_json(j) for j in rows]


@app.get("/v1/admin/system")
def system(c: CampusService = Depends(service)):
    c.actor.require_admin()
    from pathlib import Path

    verification_path = Path(__file__).resolve().parents[2] / "artifacts" / "verification.json"
    try:
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        verification = None
    traces = c.db.scalars(
        select(QueryTrace)
        .where(QueryTrace.tenant_id == c.actor.tenant_id, QueryTrace.user_id == c.actor.user_id)
        .order_by(QueryTrace.created_at.desc())
        .limit(50)
    ).all()
    from backend.app.models import CampusRecord, Document

    counts = {
        kind: c.db.scalar(c.scope(CampusRecord, kind).with_only_columns(func.count()))
        for kind in ["timetable", "faculty", "events", "menu"]
    }
    counts["documents"] = c.db.scalar(c.scope(Document).with_only_columns(func.count()))
    sources = c.db.scalars(c.scope(Source).order_by(Source.created_at.desc()).limit(50)).all()
    audit = c.db.scalars(
        select(AuditLog)
        .where(AuditLog.tenant_id == c.actor.tenant_id, AuditLog.user_id == c.actor.user_id)
        .order_by(AuditLog.created_at.desc())
        .limit(30)
    ).all()
    return dict(
        counts=counts,
        traces=[
            dict(
                id=t.id,
                route=t.route,
                tools=t.tools,
                latency_ms=t.latency_ms,
                success=t.success,
                result_count=t.result_count,
                created_at=t.created_at.isoformat(),
            )
            for t in traces
        ],
        sources=[
            dict(id=s.id, filename=s.filename, kind=s.kind, demo=s.demo, created_at=s.created_at.isoformat())
            for s in sources
        ],
        audit=[dict(action=a.action, details=a.details, created_at=a.created_at.isoformat()) for a in audit],
        models={
            "synthesis": "Gemini configured" if settings().gemini_api_key else "Extractive fallback",
            "embeddings": "Local semantic model"
            if settings().embedding_model_path
            else "Lexical hashing, 384 dimensions",
        },
        tools=list(TOOLS),
        verification=verification,
    )


@app.get("/v1/admin/memberships")
def admin_memberships(c: CampusService = Depends(service)):
    if c.actor.role not in TOP_ROLES:
        raise HTTPException(403, "Institution administrator access is required.")
    rows = c.db.scalars(select(Membership).where(Membership.tenant_id == c.actor.tenant_id))
    return [
        dict(
            user_id=m.user_id,
            name=m.name,
            role=m.role,
            department=m.department,
            semester=m.semester,
            section=m.section,
            active=m.active,
        )
        for m in rows
    ]


@app.post("/v1/admin/memberships")
def save_membership(body: MembershipUpdate, c: CampusService = Depends(service)):
    if c.actor.role not in TOP_ROLES:
        raise HTTPException(403, "Institution administrator access is required.")
    try:
        uuid.UUID(body.user_id)
    except ValueError as exc:
        raise ValueError("Use the user ID from Supabase Auth.") from exc
    if body.user_id == c.actor.user_id:
        raise ValueError("Use a separate institution administrator to change your own role.")
    if c.db.bind.dialect.name == "postgresql":
        from sqlalchemy import text as sql_text

        c.db.execute(
            sql_text("SELECT campus_upsert_membership(:tenant,:user,:role,:dept,:semester,:section,:name)"),
            dict(
                tenant=c.actor.tenant_id,
                user=body.user_id,
                role=body.role,
                dept=body.department,
                semester=body.semester,
                section=body.section,
                name=body.name,
            ),
        )
        c.db.add(
            AuditLog(
                tenant_id=c.actor.tenant_id,
                user_id=c.actor.user_id,
                action="membership.updated",
                details={"role": body.role, "department": body.department},
            )
        )
        return {"saved": True}
    member = c.db.scalar(
        select(Membership).where(
            Membership.tenant_id == c.actor.tenant_id, Membership.user_id == body.user_id
        )
    )
    if member and member.role == "platform_admin":
        raise HTTPException(403, "Platform administrator membership is managed by operators.")
    if not member:
        member = Membership(tenant_id=c.actor.tenant_id, **body.model_dump())
        c.db.add(member)
    else:
        for key, value in body.model_dump().items():
            setattr(member, key, value)
    c.db.add(
        AuditLog(
            tenant_id=c.actor.tenant_id,
            user_id=c.actor.user_id,
            action="membership.updated",
            details={"role": body.role, "department": body.department},
        )
    )
    return {"saved": True}


app.include_router(integration_router)
app.mount("/mcp", AuthenticatedMCP(mcp.streamable_http_app()))
