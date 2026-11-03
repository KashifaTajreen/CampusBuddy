"""Tenant-owned persistence. Services also apply explicit tenant predicates."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String,
    Text,
    ForeignKey,
    UniqueConstraint,
    JSON,
    DateTime,
    Float,
    Integer,
    Boolean,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector


EMBEDDING_DIMENSION = 1024


def uid():
    return str(uuid.uuid4())


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Institution(Base):
    __tablename__ = "institutions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(60), default="Asia/Kolkata")
    demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Owned:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)


class Membership(Owned, Base):
    __tablename__ = "memberships"
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    role: Mapped[str] = mapped_column(String(30), default="student")
    name: Mapped[str] = mapped_column(String(120), default="Student")
    department: Mapped[str] = mapped_column(String(60), default="AIML")
    semester: Mapped[int] = mapped_column(Integer, default=3)
    section: Mapped[str] = mapped_column(String(20), default="A")
    interests: Mapped[str] = mapped_column(String(500), default="")
    diet: Mapped[str] = mapped_column(String(30), default="any")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)


class Source(Owned, Base):
    __tablename__ = "sources"
    filename: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(30))
    checksum: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500))
    uploaded_by: Mapped[str] = mapped_column(String(36))
    owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    department: Mapped[str] = mapped_column(String(60), default="All")
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("tenant_id", "checksum", "kind", "uploaded_by"),)


class CampusRecord(Owned, Base):
    """Versioned Pydantic payloads, with indexed relational scope and provenance.

    Campus, department, program, semester, section, course, classroom, faculty,
    timetable, menu, events and emails share one ingestion architecture.
    """

    __tablename__ = "campus_records"
    kind: Mapped[str] = mapped_column(String(30), index=True)
    natural_key: Mapped[str] = mapped_column(String(64))
    department: Mapped[str] = mapped_column(String(60), default="All")
    owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "natural_key"),
        Index("ix_record_scope", "tenant_id", "kind", "department"),
    )


class Document(Owned, Base):
    __tablename__ = "documents"
    title: Mapped[str] = mapped_column(String(300))
    department: Mapped[str] = mapped_column(String(60), default="All")
    owner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    category: Mapped[str] = mapped_column(String(40), default="Notice")
    published: Mapped[str] = mapped_column(String(10))
    expires: Mapped[str | None] = mapped_column(String(10), nullable=True)
    topic: Mapped[str] = mapped_column(String(100), default="")
    claim: Mapped[str] = mapped_column(String(300), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)


class DocumentChunk(Owned, Base):
    __tablename__ = "document_chunks"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page: Mapped[int] = mapped_column(Integer)
    section: Mapped[str] = mapped_column(String(300), default="", server_default="")
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(
        JSON().with_variant(Vector(EMBEDDING_DIMENSION), "postgresql"), nullable=True
    )
    embedding_model: Mapped[str] = mapped_column(String(200), default="")


class Conversation(Owned, Base):
    __tablename__ = "conversations"
    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    title: Mapped[str] = mapped_column(String(100), default="New conversation")
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Message(Owned, Base):
    __tablename__ = "messages"
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[str] = mapped_column(String(36))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IngestionJob(Owned, Base):
    __tablename__ = "ingestion_jobs"
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    requested_by: Mapped[str] = mapped_column(String(36))
    department: Mapped[str] = mapped_column(String(60), default="All")
    status: Mapped[str] = mapped_column(String(20), default="preview")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    preview: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Owned, Base):
    __tablename__ = "audit_logs"
    user_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QueryTrace(Owned, Base):
    __tablename__ = "query_traces"
    user_id: Mapped[str] = mapped_column(String(36))
    route: Mapped[str] = mapped_column(String(50))
    tools: Mapped[list] = mapped_column(JSON)
    latency_ms: Mapped[float] = mapped_column(Float)
    success: Mapped[bool] = mapped_column(Boolean)
    result_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IntegrationConnection(Owned, Base):
    __tablename__ = "integration_connections"
    user_id: Mapped[str] = mapped_column(String(36))
    provider: Mapped[str] = mapped_column(String(30))
    encrypted_token: Mapped[str] = mapped_column(Text, default="")
    state_hash: Mapped[str] = mapped_column(String(64), default="")
    state_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", "provider"),)


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    key: Mapped[str] = mapped_column(String(150), primary_key=True)
    window: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer, default=1)
