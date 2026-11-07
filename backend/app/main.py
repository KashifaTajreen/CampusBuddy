import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date
from typing import Literal
from fastapi import FastAPI, Depends, File, Form, UploadFile, HTTPException, Request, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response, JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from backend.app.config import settings
from backend.app.db import initialize_local, set_identity, validate_production_role
from backend.app.auth import actor, authenticated_user, onboarding_user, get_session, Actor, TOP_ROLES, rate_limit, hash_section_code, generate_section_code
from backend.app.models import (
    SectionCode,
    Institution,
    Membership,
    Conversation,
    Message,
    QueryTrace,
    IngestionJob,
    AuditLog,
    Source,
    Document,
    DocumentChunk,
)
from backend.app.schemas import (
    ChatRequest,
    ImportOptions,
    MappingRequest,
    Preferences,
    MembershipUpdate,
    SourceAction,
    DocumentSelectionUpdate,
    FileAction,
    TimetablePublishRequest,
    JoinSectionRequest,
    GenerateSectionCodeRequest,
    RevokeSectionCodeRequest,
)
from backend.app.services import CampusService
from backend.app.router import Router
from backend.app.tools import ToolKit, TOOLS
from backend.app.ingestion import Ingestion, MAX_BYTES
from backend.app.publication import Publication
from backend.app.event_data import EventReviewRequest
from backend.app.event_pipeline import EventPipeline
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
    from backend.app.ocr import readiness
    from backend.app.speech import capabilities
    logger.info(json.dumps({"event": "local_capabilities", "ocr_ready": settings().ocr_enabled and readiness(
        settings().ocr_tessdata_path, settings().ocr_languages)["ready"], "local_stt_ready": capabilities()["local_stt_ready"]}))
    from backend.app.mcp_server import mcp

    async with mcp.session_manager.run():
        yield


app = FastAPI(
    title="CampusOS Intelligence API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings().app_env != "production" else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings().frontend_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-ID"],
    expose_headers=["Content-Disposition", "X-Request-ID"],
)


@app.middleware("http")
async def request_metadata(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
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
async def unknown_error(request, exc):
    logger.error(json.dumps({"event": "request_failed", "error_type": type(exc).__name__,
                             "request_id": getattr(request.state, "request_id", None)}))
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong. Try again or contact your institution administrator."},
    )


def service(a: Actor = Depends(actor), db: Session = Depends(get_session)):
    return CampusService(db, a)


@app.get("/health")
def health():
    return {"status": "ok", "auth_configured": bool(settings().supabase_url), "version": "0.1.0"}


@app.get("/ready")
def ready():
    from backend.app.db import engine
    from sqlalchemy.exc import SQLAlchemyError
    try:
        # LIMIT 0 checks current schema/grants without returning institutional data.
        with engine.connect() as connection:
            for column in (Source.student_records, Membership.student_number, DocumentChunk.row_data):
                connection.execute(select(column).limit(0))
    except SQLAlchemyError as exc:
        raise HTTPException(503,"Database readiness check failed.") from exc
    return {"status":"ready"}


@app.get("/v1/voice/capabilities")
def voice_capabilities(c: CampusService = Depends(service)):
    from backend.app.speech import capabilities
    return capabilities()


@app.post("/v1/voice/transcribe")
async def voice_transcribe(request: Request, language: Literal["auto", "en", "hi", "kn"] = "auto",
                           c: CampusService = Depends(service)):
    from backend.app.speech import MAX_AUDIO_BYTES, transcribe
    from starlette.concurrency import run_in_threadpool
    rate_limit(c.db, f"voice:{c.actor.tenant_id}:{c.actor.user_id}", 6)
    c.db.commit()  # Failed transcription attempts also consume their rate-limit budget.
    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Recording exceeds the 2 MB limit.")
        chunks.append(chunk)
    return await run_in_threadpool(transcribe, b"".join(chunks), language)


@app.get("/v1/admin/ocr")
def ocr_capabilities(c: CampusService = Depends(service)):
    c.actor.require_admin()
    from backend.app.ocr import readiness
    result = readiness(settings().ocr_tessdata_path, settings().ocr_languages)
    return {**result, "enabled": settings().ocr_enabled}


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
        student_number=a.student_number,
        interests=member.interests,
        institution=institution.name,
        demo=institution.demo,
        timezone=str(c.zone),
        needs_onboarding=bool(a.role == "student" and (not a.section or a.section in ("All", "unassigned", ""))),
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


@app.get("/v1/documents")
def selectable_documents(limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0, le=10000),
                         c: CampusService = Depends(service)):
    return c.selectable_documents(limit, offset)


@app.get("/v1/events")
def events(start: date | None = None, days: int = 14, query: str = "", category:str|None=None,
           status:Literal['all','upcoming','ongoing','past','undated']='all',c: CampusService = Depends(service)):
    return [dict(**e,can_attend=c.event_fit(e)['can_attend'] if e.get('start') and e.get('end') else None)
            for e in c.event_listing(start,days,query[:200],category=category,status=status)]


@app.get('/v1/events/{event_id}')
def event_details(event_id:str,c:CampusService=Depends(service)):
    record=c.event_record(event_id)
    rows=c.event_listing(status='all')
    return next((row for row in rows if row['id']==record.id),c.serialize(record))


@app.get("/v1/calendar")
def calendar(start: date | None = None, days: int = 7, c: CampusService = Depends(service)):
    if not 1 <= days <= 14:
        raise ValueError("Choose 1–14 calendar days.")
    from datetime import timedelta

    start = start or c.clock().date()
    return dict(
        academic=c.academic_dates(start, days),
        classes=[r for i in range(days) for r in c.timetable(start + timedelta(days=i))],
        events=c.events(start, days),
        free_slots=c.free_slots(start),
        conflicts=c.conflicts(start, days),
    )


@app.get("/v1/sources/{source_id}")
def source_file(source_id: str, c: CampusService = Depends(service)):
    # Force download; never execute HTML or display an uploaded file in the application origin.
    source, data = ObjectStore().download(c, source_id)
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
    scope = c.actor.scope_key
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
                if m.content.get("selected_document"):
                    c.selected_document(m.content["selected_document"]["id"])
                parts=[m.content]
                while parts:
                    part=parts.pop()
                    parts.extend(part.get("sections",[]))
                    for source in part.get("sources", []):
                        current=c.source(source["id"])
                        if (current.student_records and current.owner_id is None and
                                c.actor.role not in {"institution_admin","department_admin","section_admin"}):
                            # Parent publication alone cannot authorize previously
                            # saved student passages. Legacy/untraceable answers and
                            # replaced row grants fail closed, including each part.
                            ids=set(source.get("chunk_ids") or [])
                            readable=set(c.db.scalars(c.scope(DocumentChunk)
                                .join(Document,Document.id==DocumentChunk.document_id)
                                .where(Document.source_id==current.id,DocumentChunk.id.in_(ids))
                                .with_only_columns(DocumentChunk.id))) if ids else set()
                            if not ids or readable!=ids:
                                raise HTTPException(404,"Saved student evidence is no longer authorized.")
            except HTTPException:
                continue
        result.append(dict(role=m.role, content=m.content))
    return result


@app.get("/v1/conversations/{conversation_id}/selection")
def conversation_selection(conversation_id: str, c: CampusService = Depends(service)):
    row = conversation_for(c, conversation_id)
    scope = c.actor.scope_key
    if row.context.get("_scope") != scope or not row.context.get("document_id"):
        return dict(document=None, unavailable=False)
    try:
        document = c.selected_document(row.context["document_id"])
    except HTTPException:
        return dict(document=None, unavailable=True)
    return dict(document=c.document_metadata(document), unavailable=False)


@app.patch("/v1/conversations/{conversation_id}/selection")
def change_conversation_selection(conversation_id: str, body: DocumentSelectionUpdate,
                                  c: CampusService = Depends(service)):
    row = conversation_for(c, conversation_id)
    document = c.selected_document(body.document_id) if body.document_id is not None else None
    scope = c.actor.scope_key
    if row.context.get("_scope") != scope or row.context.get("document_id") != body.document_id:
        preference = row.context.get("language_preference", "auto") if row.context.get("_scope") == scope else "auto"
        row.context = {"_scope": scope, "document_id": body.document_id, "language_preference": preference}
    result = dict(document=c.document_metadata(document) if document else None, unavailable=False)
    c.db.commit()
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
    scope = c.actor.scope_key
    context = conversation.context if conversation.context.get("_scope") == scope else {}
    document_id = body.document_id if "document_id" in body.model_fields_set else context.get("document_id")
    document = c.selected_document(document_id) if document_id is not None else None
    if document_id != context.get("document_id"):
        context = {"language_preference": context.get("language_preference", "auto")}
    result = Router(c).ask(body.question, context, document_id=document_id, language=body.language)
    result["selected_document"] = c.document_metadata(document) if document else None
    result["context"]["document_id"] = document_id
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


def job_json(job, c, source=None):
    source = source if source is not None else c.management_source(job.source_id)
    return dict(
        id=job.id,
        source_id=job.source_id,
        filename=source.filename,
        kind=source.kind,
        department=source.department,
        semester=source.semester,
        section=source.section,
        demo=source.demo,
        status=job.status,
        preview=job.preview if source.file_state == "active" else {},
        error=job.error,
        attempts=job.attempts,
        created_at=job.created_at.isoformat(),
        source_status=source.publication_status if source.file_state == "active" else source.file_state,
    )


def committed_job_json(job, c):
    # Request-scoped dependency cleanup can run after the response is sent.
    # Persist queue changes before acknowledging them so an API restart cannot
    # lose an upload/confirmation the client has already seen succeed.
    result = job_json(job, c)
    c.db.commit()
    return result


@app.post("/v1/ingestion/preview")
async def import_preview(
    file: UploadFile = File(...), options: str = Form(...), c: CampusService = Depends(service)
):
    rate_limit(c.db, f"upload:{c.actor.tenant_id}:{c.actor.user_id}", 10)
    content = await file.read(MAX_BYTES + 1)
    try:
        parsed = ImportOptions.model_validate_json(options)
    except ValueError as exc:
        from pydantic import ValidationError
        issues = "; ".join(".".join(str(part) for part in error['loc']) + ': ' + error['msg']
                           for error in exc.errors()) if isinstance(exc, ValidationError) else 'Malformed JSON settings.'
        raise HTTPException(422, "Import settings: " + issues[:1500]) from exc
    from starlette.concurrency import run_in_threadpool
    if parsed.kind=='events' and parsed.event_kind not in ('exam','deadline'):
        parsed=parsed.model_copy(update={'event_workflow':True,'event_reviewed':False})
    job = await run_in_threadpool(Ingestion(c).receive, file.filename or "source", content, parsed)
    return committed_job_json(job, c)


@app.post("/v1/ingestion/{job_id}/mapping")
def update_mapping(job_id: str, body: MappingRequest, c: CampusService = Depends(service)):
    ingestion = Ingestion(c)
    return committed_job_json(ingestion.preview(ingestion.job(job_id), body.mapping, rows=body.rows,
        pages=body.pages,
        timetable_ready_only=body.timetable_ready_only,
        ocr_reviewed=body.ocr_reviewed, reextract=body.reextract, defaults=body.defaults,
        overlap_reviewed=body.overlap_reviewed, reset_mapping=body.reset_mapping), c)


@app.post("/v1/ingestion/{job_id}/as-document")
def preview_original_as_document(job_id: str, c: CampusService = Depends(service)):
    ingestion = Ingestion(c)
    return committed_job_json(ingestion.as_document(ingestion.job(job_id)), c)


@app.post('/v1/ingestion/{job_id}/publish-timetable')
def publish_timetable(job_id: str, body: TimetablePublishRequest, c: CampusService = Depends(service)):
    ingestion=Ingestion(c)
    job=ingestion.job(job_id)
    source=c.management_source(job.source_id)
    c.actor.check_source_write(source)
    if source.kind!='timetable' or source.owner_id is not None or job.requested_by!=c.actor.user_id:
        raise HTTPException(403,'Only the authorized timetable uploader may use this publication action.')
    if source.publication_status=='published':
        return committed_job_json(job,c)
    if job.status=='completed':
        publication=Publication(c)
        if source.publication_status=='pending_review':
            publication.transition(source.id,'approve')
        publication.transition(source.id,'publish')
    else:
        ingestion.preview(job,ocr_reviewed=body.ocr_reviewed,overlap_reviewed=body.overlap_reviewed,
                          timetable_ready_only=body.ready_only)
        ingestion.confirm(job)
        job.options={**job.options,'timetable_publish_requested':True}
    return committed_job_json(job,c)


@app.post('/v1/ingestion/{job_id}/extract-event')
def extract_existing_event(job_id:str,c:CampusService=Depends(service)):
    ingestion=Ingestion(c)
    job=ingestion.job(job_id)
    source=c.management_source(job.source_id)
    c.actor.check_source_write(source)
    if (source.kind!='events' or source.owner_id is not None or source.publication_status!='draft'
            or source.file_state!='active' or job.requested_by!=c.actor.user_id):
        raise HTTPException(403,'Only the authorized uploader may extract an active event draft.')
    if job.status in ('queued','processing','extract_queued'):
        return committed_job_json(job,c)
    if job.status not in ('preview','failed','received') or job.options.get('event_reviewed'):
        raise HTTPException(409,'Saved reviewed content cannot be overwritten by OCR. Edit the existing reviewed text instead.')
    if job.options.get('event_kind') in ('exam','deadline'):
        raise HTTPException(409,'Use the existing examination/deadline import workflow for this source.')
    job.options=ImportOptions.model_validate({**job.options,'event_workflow':True,
        'event_reviewed':False,'event_publish_requested':False,'event_index_preview':False,'event_rows':None,'event_pages':None}).model_dump(mode='json')
    return committed_job_json(ingestion.preview(job,reextract=True),c)


@app.post('/v1/ingestion/{job_id}/event-draft')
def save_event_draft(job_id:str,body:EventReviewRequest,c:CampusService=Depends(service)):
    ingestion=Ingestion(c)
    job=ingestion.job(job_id)
    if not job.options.get('event_workflow'):
        raise HTTPException(409,'Extract this existing draft as an event before saving metadata.')
    return committed_job_json(EventPipeline(ingestion).save_review(job,body,reviewed=False),c)


@app.post('/v1/ingestion/{job_id}/event-review')
def review_event(job_id:str,body:EventReviewRequest,c:CampusService=Depends(service)):
    ingestion=Ingestion(c)
    job=ingestion.job(job_id)
    if not job.options.get('event_workflow'):
        raise HTTPException(409,'Upload this as a new Events import to review its structured metadata and text together.')
    EventPipeline(ingestion).save_review(job,body)
    if not job.preview['error_count'] and (not job.preview.get('ocr') or body.ocr_reviewed):
        ingestion.confirm(job)
        job.options={**job.options,'event_index_preview':True,'event_publish_requested':False}
    return committed_job_json(job,c)


@app.post('/v1/ingestion/{job_id}/publish-event')
def publish_event(job_id:str,c:CampusService=Depends(service)):
    ingestion=Ingestion(c)
    job=ingestion.job(job_id)
    source=c.management_source(job.source_id)
    c.actor.check_source_write(source)
    if source.kind!='events' or not job.options.get('event_workflow') or job.requested_by!=c.actor.user_id:
        raise HTTPException(403,'Only the authorized event uploader may use Publish Event.')
    if source.publication_status=='published' or (job.status in ('queued','processing') and job.options.get('event_publish_requested')):
        return committed_job_json(job,c)
    if not job.options.get('event_reviewed'):
        raise HTTPException(409,'Click Validate Event after reviewing its information and text.')
    if not job.preview.get('event_index_ready'):
        raise HTTPException(409,'Event indexing is not ready. Validate Event to prepare or retry draft indexing; publication remains blocked.')
    if job.status=='failed':
        ingestion.preview(job)
    if job.status=='completed':
        publication=Publication(c)
        if source.publication_status=='pending_review':
            publication.transition(source.id,'approve')
        publication.transition(source.id,'publish')
    else:
        ingestion.confirm(job)
        job.options={**job.options,'event_publish_requested':True}
    return committed_job_json(job,c)


@app.get("/v1/admin/sources/{source_id}/content-preview")
def source_content_preview(source_id: str, c: CampusService = Depends(service)):
    from backend.app.parsing import parse_file
    from backend.app.storage import ObjectStore
    from fastapi.responses import JSONResponse
    import hashlib
    rate_limit(c.db, f"preview:{c.actor.tenant_id}:{c.actor.user_id}", 30)
    source = c.management_source(source_id)
    if source.file_state != "active":
        raise HTTPException(404,"The original is unavailable. Deleted and purged files cannot be previewed.")
    content = ObjectStore().get(source.storage_key)
    if hashlib.sha256(content).hexdigest() != source.checksum:
        raise HTTPException(409,"Original file checksum changed. Restore the verified original.")
    result = parse_file(source.filename,content,preview_only=True)
    return JSONResponse(dict(source_id=source.id,filename=source.filename,**result),
        headers={"Cache-Control":"no-store", "X-Content-Type-Options":"nosniff"})


@app.post("/v1/ingestion/{job_id}/confirm")
def confirm_import(job_id: str, c: CampusService = Depends(service)):
    ingestion = Ingestion(c)
    return committed_job_json(ingestion.confirm(ingestion.job(job_id)), c)


@app.post("/v1/ingestion/{job_id}/retry")
def retry_import(job_id: str, c: CampusService = Depends(service)):
    ingestion = Ingestion(c)
    job = ingestion.job(job_id)
    if job.status != "failed":
        raise ValueError("Only failed jobs can be retried.")
    job.status = "received"
    ingestion.preview(job)
    return committed_job_json(job, c)


@app.post("/v1/sources/{source_id}/reindex")
def reindex_source(source_id: str, c: CampusService = Depends(service)):
    Ingestion(c).queue_capacity()
    source = c.management_source(source_id)
    if source.file_state != "active" or source.publication_status != "published":
        raise ValueError("Only an active published source can be re-indexed.")
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
        semester=doc.semester,
        section=doc.section,
        title=doc.title,
        category=doc.category,
        published=doc.published,
        expires=doc.expires,
        topic=doc.topic,
        claim=doc.claim,
        reindex=True,
        demo=source.demo,
        visibility="private" if source.owner_id is not None else "shared",
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
    return job_json(job, c)


@app.get("/v1/ingestion")
def jobs(c: CampusService = Depends(service)):
    rows = list(c.db.scalars(
        select(IngestionJob)
        .where(IngestionJob.tenant_id == c.actor.tenant_id, IngestionJob.requested_by == c.actor.user_id)
        .order_by(IngestionJob.created_at.desc())
        .limit(50)
    ))
    # A demoted uploader must not retain a raw shared preview through job history.
    result = []
    sources={source.id:source for source in c.db.scalars(select(Source).where(
        Source.tenant_id==c.actor.tenant_id,Source.id.in_([row.source_id for row in rows])))}
    for job in rows:
        try:
            source=sources.get(job.source_id)
            if not source or (source.owner_id is not None and source.owner_id!=c.actor.user_id):
                continue
            if source.owner_id is None:
                c.actor.check_source_write(source)
                if source.kind!='document' and c.actor.role not in TOP_ROLES and source.uploaded_by!=c.actor.user_id:
                    continue
            item=job_json(job,c,source)
            if item['source_status']!='purged':
                result.append(item)
        except HTTPException:
            continue
    return result


@app.get("/v1/admin/sources/{source_id}")
def review_source(source_id: str, c: CampusService = Depends(service)):
    return Publication(c).review(source_id)


@app.get("/v1/admin/files")
def admin_files(limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
                c: CampusService = Depends(service)):
    publication = Publication(c)
    return [publication.describe(source) for source in publication.sources(limit=limit, offset=offset)]


@app.get("/v1/admin/canteen-sources")
def canteen_sources(c: CampusService = Depends(service)):
    c.actor.check_department_write("All", "menu")
    result = []
    for source in c.db.scalars(c.scope(Source).where(Source.kind == "menu", Source.owner_id.is_(None)).limit(100)):
        try:
            c.canteen_source(source.id)
            result.append({**Publication(c).describe(source), "actions": []})
        except HTTPException:
            continue
    return result


@app.get("/v1/admin/canteen-sources/{source_id}/rows")
def canteen_edit_rows(source_id: str, c: CampusService = Depends(service)):
    from backend.app.models import CampusRecord
    from backend.app.schemas import MenuData
    source = c.canteen_source(source_id)
    rows = []
    for record in c.db.scalars(c.scope(CampusRecord,"menu").where(CampusRecord.source_id == source.id).limit(10001)):
        row = MenuData.model_validate(record.payload).model_dump(mode="json")
        c.actor.check_department_write(row["department"],"menu")
        rows.append(row)
    if not rows or len(rows)>10000:
        raise HTTPException(409,"Menu rows are missing or exceed the editing limit.")
    return dict(source_id=source.id,records=rows)


@app.post("/v1/admin/sources/{source_id}/file")
def file_action(source_id: str, body: "FileAction", c: CampusService = Depends(service)):
    from backend.app.files import Files
    return Files(c).change(source_id, body.action, body.confirmation, body.replaces_source_id)


@app.post("/v1/admin/sources/{source_id}/{action}")
def source_action(
    source_id: str, action: Literal["approve", "publish", "reject", "revoke"],
    body: SourceAction, c: CampusService = Depends(service),
):
    return Publication(c).transition(source_id, action, body.reason)


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
        for kind in ["timetable", "faculty", "events", "calendar", "menu"]
    }
    counts["documents"] = c.db.scalar(c.scope(Document).with_only_columns(func.count()))
    publication = Publication(c)
    sources = publication.sources()
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
            publication.describe(s)
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
            student_number=m.student_number,
        )
        for m in rows
    ]


@app.post("/v1/admin/memberships")
def save_membership(body: MembershipUpdate, c: CampusService = Depends(service)):
    if c.actor.role not in TOP_ROLES:
        raise HTTPException(403, "Institution administrator access is required.")
    from backend.app.auth import restrict_test_student
    restrict_test_student(body.user_id,c.actor.tenant_id,body.role)
    from backend.app.cache import touch
    touch(c.db, c.actor.tenant_id)
    try:
        uuid.UUID(body.user_id)
    except ValueError as exc:
        raise ValueError("Use the user ID from Supabase Auth.") from exc
    if body.user_id == c.actor.user_id:
        raise ValueError("Use a separate institution administrator to change your own role.")
    if body.student_number and "student_number" in body.model_fields_set:
        assigned=c.db.scalar(select(Membership.id).where(Membership.tenant_id==c.actor.tenant_id,
            Membership.student_number==body.student_number,Membership.user_id!=body.user_id))
        if assigned:
            raise HTTPException(409,"This verified USN is already assigned. Verify the institutional account mapping before changing it.")
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
        if "student_number" in body.model_fields_set:
            c.db.execute(sql_text("SELECT campus_set_student_number(:tenant,:user,:number)"),
                dict(tenant=c.actor.tenant_id,user=body.user_id,number=body.student_number))
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
    if not member:
        member = Membership(tenant_id=c.actor.tenant_id, **body.model_dump())
        c.db.add(member)
    else:
        for key, value in body.model_dump(exclude_unset=True).items():
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




@app.post("/v1/onboarding/join-section")
def join_section(
    body: JoinSectionRequest,
    user_id: str = Depends(onboarding_user),
    x_tenant_id: str = Header(...),
    db: Session = Depends(get_session),
):
    import hmac
    from datetime import datetime, timezone
    try:
        x_tenant_id = str(uuid.UUID(x_tenant_id))
    except ValueError as exc:
        raise HTTPException(400, "Choose a valid institution.") from exc
    from backend.app.auth import restrict_test_student
    restrict_test_student(user_id,x_tenant_id,'student')
    rate_limit(db, f"join_section:{user_id}", limit=10)
    # Failed code attempts must count even when the request transaction rolls back.
    db.commit()
    set_identity(db, user_id, x_tenant_id)
    if db.bind.dialect.name == "postgresql":
        from backend.app.section_onboarding import postgres_call
        return postgres_call(db, "join", dict(tenant=x_tenant_id, **body.model_dump()))
    from backend.app.db import serialize_sqlite_writes
    serialize_sqlite_writes(db)
    m = db.scalar(select(Membership).where(
        Membership.tenant_id == x_tenant_id, Membership.user_id == user_id))
    if m and (not m.active or m.role != "student"):
        raise HTTPException(403, "Ask your institution administrator to review your membership.")
    now = datetime.now(timezone.utc)
    candidates = db.scalars(
        select(SectionCode).where(
            SectionCode.tenant_id == x_tenant_id,
            SectionCode.department == body.department,
            SectionCode.semester == body.semester,
            SectionCode.section == body.section,
            SectionCode.revoked.is_(False),
            SectionCode.expires_at > now,
        )
    ).all()
    matched = None
    for cand in candidates:
        if hmac.compare_digest(hash_section_code(body.code, cand.salt), cand.code_hash):
            matched = cand
            break
    if matched:
        from backend.app.auth import resolve_actor
        try:
            creator = resolve_actor(db, matched.created_by, x_tenant_id)
            creator.check_department_write(body.department, kind="section_code", semester=body.semester, section=body.section)
        except HTTPException:
            matched = None
    if not matched:
        raise HTTPException(
            400,
            "Invalid or expired section join code. Check with your department office or class representative.",
        )
    if not m:
        m = Membership(
            tenant_id=x_tenant_id,
            user_id=user_id,
            role="student",
            department=body.department,
            semester=body.semester,
            section=body.section,
            name="Student",
            active=True,
        )
        db.add(m)
    else:
        m.department = body.department
        m.semester = body.semester
        m.section = body.section
        m.role = "student"
        m.active = True
    db.commit()
    return {
        "success": True,
        "department": m.department,
        "semester": m.semester,
        "section": m.section,
        "role": m.role,
    }


@app.post("/v1/admin/section-codes")
def create_section_code(body: GenerateSectionCodeRequest, c: CampusService = Depends(service)):
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import update
    c.actor.check_department_write(department=body.department, kind="section_code", semester=body.semester, section=body.section)
    from backend.app.section_onboarding import postgres_call, sqlite_admin
    if c.db.bind.dialect.name == "postgresql":
        raw_code, code_hash, salt = generate_section_code(body.department, body.semester, body.section)
        result = postgres_call(c.db, "create", dict(tenant=c.actor.tenant_id, department=body.department,
            semester=body.semester, section=body.section, hash=code_hash, salt=salt, days=body.expiry_days))
        return {**result, "code": raw_code}
    sqlite_admin(c, body.department, body.semester, body.section)
    c.db.execute(
        update(SectionCode)
        .where(
            SectionCode.tenant_id == c.actor.tenant_id,
            SectionCode.department == body.department,
            SectionCode.semester == body.semester,
            SectionCode.section == body.section,
            SectionCode.revoked.is_(False),
        )
        .values(revoked=True)
    )
    raw_code, code_hash, salt = generate_section_code(body.department, body.semester, body.section)
    expires = datetime.now(timezone.utc) + timedelta(days=body.expiry_days)
    record = SectionCode(
        tenant_id=c.actor.tenant_id,
        department=body.department,
        semester=body.semester,
        section=body.section,
        code_hash=code_hash,
        salt=salt,
        created_by=c.actor.user_id,
        expires_at=expires,
        revoked=False,
    )
    c.db.add(record)
    c.db.commit()
    return {
        "id": record.id,
        "code": raw_code,
        "department": record.department,
        "semester": record.semester,
        "section": record.section,
        "expires_at": expires.isoformat(),
    }


@app.get("/v1/admin/section-codes")
def list_section_codes(c: CampusService = Depends(service)):
    from datetime import datetime, timezone
    c.actor.require_admin()
    if c.actor.role == "canteen_manager":
        raise HTTPException(403, "Canteen Managers cannot manage section enrollment.")
    stmt = select(SectionCode.id, SectionCode.department, SectionCode.semester, SectionCode.section,
                  SectionCode.expires_at, SectionCode.revoked, SectionCode.created_at).where(SectionCode.tenant_id == c.actor.tenant_id)
    if c.actor.role in ("department_admin", "section_admin"):
        stmt = stmt.where(SectionCode.department == c.actor.department)
    if c.actor.role == "section_admin":
        stmt = stmt.where(
            SectionCode.semester == c.actor.semester,
            SectionCode.section == c.actor.section,
        )
    rows = c.db.execute(stmt.order_by(SectionCode.created_at.desc()).limit(100)).all()
    now = datetime.now(timezone.utc)
    return [
        {
            "id": r.id,
            "department": r.department,
            "semester": r.semester,
            "section": r.section,
            "expires_at": r.expires_at.replace(tzinfo=r.expires_at.tzinfo or timezone.utc).isoformat(),
            "revoked": r.revoked,
            "is_active": not r.revoked and r.expires_at.replace(tzinfo=r.expires_at.tzinfo or timezone.utc) > now,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


@app.post("/v1/admin/section-codes/revoke")
def revoke_section_code(body: RevokeSectionCodeRequest, c: CampusService = Depends(service)):
    c.actor.require_admin()
    from backend.app.section_onboarding import postgres_call, sqlite_admin
    if c.db.bind.dialect.name == "postgresql":
        return postgres_call(c.db, "revoke", dict(tenant=c.actor.tenant_id, code_id=body.code_id))
    rec = c.db.scalar(
        select(SectionCode).where(
            SectionCode.tenant_id == c.actor.tenant_id,
            SectionCode.id == body.code_id,
        )
    )
    if not rec:
        raise HTTPException(404, "Section code not found.")
    c.actor.check_department_write(department=rec.department, kind="section_code", semester=rec.semester, section=rec.section)
    sqlite_admin(c, rec.department, rec.semester, rec.section)
    rec.revoked = True
    c.db.commit()
    return {"revoked": True}


app.include_router(integration_router)
app.mount("/mcp", AuthenticatedMCP(mcp.streamable_http_app()))
