"""Upgrade a genuine disposable 0005 layout without rewriting old source bytes."""
import sqlite3
from sqlalchemy import create_engine
from backend.app.models import Base, Institution, Membership, Source
from sqlalchemy.orm import Session
from backend.tests.test_publication_migration import upgrade


def test_0006_adds_scopes_and_maps_legacy_roles_preserving_sources(tmp_path, monkeypatch):
    path = tmp_path / "legacy-0005.db"
    engine = create_engine("sqlite:///"+str(path))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Institution(id="test", name="Migration fixture"))
        db.flush()
        for user, role in (("teacher","faculty"),("operator","platform_admin"),("student","student")):
            db.add(Membership(tenant_id="test", user_id=user, role=role))
        db.add(Source(id="source", tenant_id="test", filename="preserved.txt", checksum="unchanged",
            kind="document", storage_key="unchanged-object", uploaded_by="operator"))
        db.commit()
    engine.dispose()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE alembic_version(version_num VARCHAR(32) PRIMARY KEY)")
        db.execute("INSERT INTO alembic_version VALUES('0005')")
        for table in ("sources", "documents", "campus_records"):
            db.execute(f"ALTER TABLE {table} DROP COLUMN semester")
            db.execute(f"ALTER TABLE {table} DROP COLUMN section")
        before = db.execute("SELECT id,filename,checksum,storage_key FROM sources").fetchall()
    upgrade(path, monkeypatch, "0006")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id,filename,checksum,storage_key FROM sources").fetchall() == before
        assert db.execute("SELECT semester,section FROM sources").fetchall() == [(None,"All")]
        assert dict(db.execute("SELECT user_id,role FROM memberships")) == {
            "teacher":"student", "operator":"institution_admin", "student":"student"}
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        try:
            db.execute("UPDATE memberships SET role='faculty'")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("Legacy role was accepted after the migration")
