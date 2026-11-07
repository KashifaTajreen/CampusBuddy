"""Upgrade both genuine legacy-shaped SQLite data and a fresh database."""

from pathlib import Path
from types import SimpleNamespace
import sqlite3
from alembic import command
from alembic.config import Config
from backend.app import config


def upgrade(path, monkeypatch, revision="0003"):
    monkeypatch.setattr(config, "settings", lambda: SimpleNamespace(database_url="sqlite:///" + str(path)))
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(cfg, revision)


def test_legacy_migration_preserves_processed_sources_and_hides_previews(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE alembic_version(version_num VARCHAR(32) PRIMARY KEY);
        INSERT INTO alembic_version VALUES('0002');
        CREATE TABLE sources(id VARCHAR(36) PRIMARY KEY,tenant_id VARCHAR(36),filename TEXT,kind TEXT,
          checksum TEXT,storage_key TEXT,uploaded_by TEXT,owner_id TEXT,department TEXT,demo BOOLEAN,created_at DATETIME);
        CREATE TABLE ingestion_jobs(id TEXT PRIMARY KEY,tenant_id TEXT,source_id TEXT,status TEXT);
        CREATE TABLE campus_records(id TEXT PRIMARY KEY,tenant_id TEXT,source_id TEXT,payload JSON);
        CREATE TABLE documents(id TEXT PRIMARY KEY,tenant_id TEXT,source_id TEXT);
        INSERT INTO sources VALUES('old','tenant','menu.json','menu','checksum','original/key','admin',NULL,'All',0,'2026-10-28');
        INSERT INTO sources VALUES('draft','tenant','draft.pdf','document','draft','draft/key','admin',NULL,'All',0,'2026-10-28');
        INSERT INTO sources VALUES('private','tenant','personal.txt','document','private','private/key','owner','owner','All',0,'2026-10-28');
        INSERT INTO ingestion_jobs VALUES('job','tenant','old','completed');
        INSERT INTO ingestion_jobs VALUES('preview','tenant','draft','preview');
        INSERT INTO campus_records VALUES('record','tenant','old','{"price":25}');
        INSERT INTO documents VALUES('doc','tenant','private');
        """)
    upgrade(path, monkeypatch)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0003"
        assert dict(db.execute("SELECT id,publication_status FROM sources")) == {
            "old": "published",
            "draft": "draft",
            "private": "published",
        }
        row = db.execute("SELECT storage_key,approved_by,review_note FROM sources WHERE id='old'").fetchone()
        assert row[0] == "original/key" and row[1] is None and "Legacy" in row[2]
        assert (
            db.execute("SELECT payload FROM campus_records WHERE id='record'").fetchone()[0] == '{"price":25}'
        )
        assert db.execute("SELECT staged_data FROM ingestion_jobs WHERE id='job'").fetchone()[0] == "{}"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_fresh_sqlite_migrations_support_current_schema(tmp_path, monkeypatch):
    path = tmp_path / "fresh.db"
    upgrade(path, monkeypatch)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0003"
        assert "publication_status" in {r[1] for r in db.execute("PRAGMA table_info(sources)")}
        assert "staged_data" in {r[1] for r in db.execute("PRAGMA table_info(ingestion_jobs)")}


def test_timestamp_grant_migration_does_not_change_sqlite_schema_or_data(tmp_path, monkeypatch):
    path = tmp_path / "timestamp-grant.db"
    upgrade(path, monkeypatch)
    with sqlite3.connect(path) as db:
        schema = db.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
        db.execute("INSERT INTO institutions(id,name,timezone,demo) VALUES('fixture','Synthetic','UTC',1)")
    upgrade(path, monkeypatch, "0004")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0004"
        assert db.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall() == schema
        assert db.execute("SELECT * FROM institutions").fetchall() == [("fixture", "Synthetic", "UTC", 1)]
