import sqlite3
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from backend.app.models import Base,Institution,Membership,Source
from backend.tests.test_publication_migration import upgrade


def test_0007_preserves_original_values_and_quarantines_legacy_spreadsheets(tmp_path,monkeypatch):
    path=tmp_path/"legacy0006.db"
    engine=create_engine("sqlite:///"+str(path))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Institution(id="fixture",name="Synthetic migration"))
        db.flush()
        db.add(Membership(user_id="admin",tenant_id="fixture",role="institution_admin"))
        db.add(Source(id="source",tenant_id="fixture",filename="old.xlsx",kind="document",checksum="preserved",
            storage_key="preserved",uploaded_by="admin"))
        db.commit()
    engine.dispose()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE alembic_version(version_num TEXT PRIMARY KEY)")
        db.execute("INSERT INTO alembic_version VALUES('0006')")
        db.execute("DROP INDEX uq_membership_student_number")
        for table,column in (("memberships","student_number"),("sources","student_records"),("document_chunks","student_numbers"),("document_chunks","row_data")):
            db.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        before=db.execute("SELECT id,filename,checksum,storage_key FROM sources").fetchall()
    upgrade(path,monkeypatch,"0007")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id,filename,checksum,storage_key FROM sources").fetchall()==before
        assert db.execute("SELECT student_records FROM sources").fetchone()==(1,)
        assert db.execute("SELECT student_number FROM memberships").fetchone()==(None,)
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE memberships SET student_number='unverified'")
        assert db.execute("PRAGMA integrity_check").fetchone()==("ok",)
