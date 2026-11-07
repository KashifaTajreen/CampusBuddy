"""Check a populated legacy upgrade in a database created only for this test."""

import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from backend.app import config
from backend.app.models import CampusRecord, Document, IngestionJob, Institution, Source
from backend.tests.conftest import USER

pytestmark = pytest.mark.postgres


def test_postgres_legacy_upgrade_preserves_data_and_installs_publication(monkeypatch):
    raw_url = os.getenv("TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("TEST_POSTGRES_URL is not configured; legacy PostgreSQL migration needs a disposable cluster.")
    url = make_url(raw_url)
    name = "campus_migration_test_" + uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    target = create_engine(url.set(database=name))
    created = False
    try:
        with admin.connect() as connection:
            # Name is a fixed prefix plus generated hex, never user input.
            connection.execute(text(f'CREATE DATABASE "{name}"'))
            created = True
        monkeypatch.setattr(config, "settings", lambda: SimpleNamespace(
            database_url=url.set(database=name)))
        cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        command.upgrade(cfg, "0002")
        with Session(target) as db:
            db.add(Institution(id="legacy-tenant", name="Synthetic migration fixture", demo=True))
            db.flush()
            for sid, owner in (("processed", None), ("preview", None), ("private", USER)):
                db.add(Source(id=sid, tenant_id="legacy-tenant", filename=sid + ".txt",
                              kind="document" if sid != "processed" else "menu",
                              checksum=sid, storage_key="synthetic/" + sid, uploaded_by=USER,
                              owner_id=owner, department="All", demo=True))
            db.flush()
            db.add(CampusRecord(id="record", tenant_id="legacy-tenant", kind="menu",
                                natural_key="legacy-menu", department="All", source_id="processed",
                                payload={"name": "Legacy synthetic meal", "price": 25}))
            db.add(Document(id="document", tenant_id="legacy-tenant", title="Private synthetic document",
                            department="All", owner_id=USER, published="2026-10-28", source_id="private"))
            for sid, status in (("processed", "completed"), ("preview", "preview")):
                db.add(IngestionJob(id="job-" + sid, tenant_id="legacy-tenant", source_id=sid,
                                    requested_by=USER, department="All", status=status, options={}, preview={}))
            db.commit()

        # Historical 0001 reads current ORM metadata. Remove only the newly added
        # columns in this brand-new database to exercise an actual legacy ADD path.
        new_columns = {"sources": {"publication_status", "approved_by", "approved_at", "published_by",
                                   "published_at", "review_note", "replaces_source_id"},
                       "ingestion_jobs": {"staged_data"}}
        with target.begin() as connection:
            for table, columns in new_columns.items():
                for column in sorted(columns):
                    connection.execute(text(f'ALTER TABLE "{table}" DROP COLUMN "{column}"'))
            inspector = inspect(connection)
            legacy_columns = {
                table: [c["name"] for c in inspector.get_columns(table)]
                for table in inspector.get_table_names() if table != "alembic_version"
            }

            def snapshot():
                return {
                    table: sorted(repr(tuple(row)) for row in connection.execute(text(
                        'SELECT ' + ','.join('"' + c + '"' for c in columns) + ' FROM "' + table + '"'
                    ))) for table, columns in legacy_columns.items()
                }

            before = snapshot()
        command.upgrade(cfg, "0003")
        with target.begin() as connection:
            assert snapshot() == before
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0003"
            assert dict(connection.execute(select(Source.id, Source.publication_status)).all()) == {
                "processed": "published", "preview": "draft", "private": "published",
            }
            assert connection.scalar(select(Source.approved_by).where(Source.id == "processed")) is None
            assert "Legacy" in connection.scalar(select(Source.review_note).where(Source.id == "processed"))
            assert connection.scalar(select(IngestionJob.staged_data).limit(1)) == {}
            assert "ix_source_publication" in {i["name"] for i in inspect(connection).get_indexes("sources")}
            assert "fk_source_replacement" in {f["name"] for f in inspect(connection).get_foreign_keys("sources")}
            assert all(enabled and forced for enabled, forced in connection.execute(text(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                "WHERE relname IN ('sources','campus_records','documents','document_chunks','ingestion_jobs')"
            )))
            assert not connection.scalar(text(
                "SELECT has_column_privilege('campus_api','ingestion_jobs','created_at','INSERT')"
            ))
        command.upgrade(cfg, "head")
        with target.begin() as connection:
            assert snapshot() == before
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0009"
            assert connection.scalar(text(
                "SELECT has_column_privilege('campus_api','ingestion_jobs','created_at','INSERT')"
            ))
            assert not connection.scalar(text(
                "SELECT has_column_privilege('campus_api','ingestion_jobs','staged_data','INSERT')"
            ))
            assert not connection.scalar(text(
                "SELECT has_column_privilege('campus_api','ingestion_jobs','staged_data','UPDATE')"
            ))
            assert not connection.scalar(text("SELECT has_table_privilege('campus_api','sources','UPDATE')"))
    finally:
        target.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()
