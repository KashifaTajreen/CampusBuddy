"""A genuine pre-0005 SQLite shape gains a defaulted column without changing rows."""
import sqlite3
from backend.tests.test_publication_migration import upgrade


def test_file_migration_preserves_old_values(tmp_path, monkeypatch):
    path = tmp_path / "legacy-files.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE alembic_version(version_num VARCHAR(32) PRIMARY KEY);
        INSERT INTO alembic_version VALUES('0004');
        CREATE TABLE sources(id TEXT PRIMARY KEY,filename TEXT,publication_status TEXT);
        INSERT INTO sources VALUES('published','original.pdf','published');
        INSERT INTO sources VALUES('draft','preview.docx','draft');
        """)
        before = db.execute("SELECT * FROM sources ORDER BY id").fetchall()
    upgrade(path, monkeypatch, "0005")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT id,filename,publication_status FROM sources ORDER BY id").fetchall() == before
        assert db.execute("SELECT file_state FROM sources").fetchall() == [("active",), ("active",)]
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0005"
