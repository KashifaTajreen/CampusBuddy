"""Verify a read-only SQLite backup and, optionally, a new disposable pgvector container.

Never accepts a PostgreSQL URL: generated credentials can address only the container
created by this invocation. Existing containers/volumes are inventoried, never changed.
Backups stay in ignored .scratch; reports contain no source rows or credentials.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]


def sqlite_snapshot(connection):
    schema = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    tables = {}
    for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted = '"' + table.replace('"', '""') + '"'
        rows = sorted(repr(row) for row in connection.execute(f"SELECT * FROM {quoted}"))
        tables[table] = {
            "count": len(rows),
            "sha256": hashlib.sha256("\n".join(rows).encode()).hexdigest(),
        }
    return {"schema_sha256": hashlib.sha256(repr(schema).encode()).hexdigest(), "tables": tables}


def sqlite_verify(work, run):
    # Import config only: importing app.main would initialize an application engine.
    from backend.app.config import settings
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy.engine import make_url

    url = make_url(settings().database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise RuntimeError("Configured development database must be an existing SQLite file.")
    original = Path(url.database).resolve(strict=True)
    if settings().app_env != "development":
        raise RuntimeError("Only the development SQLite database may be backed up by this command.")
    original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    backup = work / "development-backup.db"
    with sqlite3.connect(original.as_uri() + "?mode=ro", uri=True) as source:
        source.execute("BEGIN")
        revision = source.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        if revision not in ("0003", "0004", "0005"):
            raise RuntimeError("Unexpected original SQLite revision; verify it manually before proceeding.")
        before = sqlite_snapshot(source)
        with sqlite3.connect(backup) as destination:
            source.backup(destination)
    disposable = work / "migration-copy.db"
    shutil.copyfile(backup, disposable)
    migration_env = {
        "DATABASE_URL": "sqlite:///" + disposable.as_posix(),
        "APP_ENV": "development",
    }
    run([sys.executable, "-m", "alembic", "upgrade", revision], migration_env)
    with sqlite3.connect(disposable) as db:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [(revision,)]
        required = {
            "publication_status", "approved_by", "approved_at", "published_by", "published_at",
            "review_note", "replaces_source_id",
        }
        assert required <= {row[1] for row in db.execute("PRAGMA table_info(sources)")}
        assert "staged_data" in {row[1] for row in db.execute("PRAGMA table_info(ingestion_jobs)")}
        assert "ix_source_publication" in {row[1] for row in db.execute("PRAGMA index_list(sources)")}
        assert "ck_source_publication_status" in db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
        ).fetchone()[0]
        after = sqlite_snapshot(db)
    # Preserve the supported original revision, including an already upgraded 0005.
    assert before == after, "Migrated copy differs from its original snapshot. Investigate before proceeding."
    run([sys.executable, "-m", "alembic", "upgrade", "head"], migration_env)
    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    with sqlite3.connect(disposable) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchall() == [(head,)]
    # 0005 adds one defaulted source column. Compare all pre-existing columns/rows,
    # not whole-row hashes whose shape necessarily changes with an additive migration.
    with sqlite3.connect(backup) as old, sqlite3.connect(disposable) as new:
        for table in before["tables"]:
            if table == "alembic_version":
                continue
            quoted = '"' + table.replace('"', '""') + '"'
            columns = [r[1] for r in old.execute(f"PRAGMA table_info({quoted})")]
            projection = ','.join('"' + col.replace('"', '""') + '"' for col in columns)
            assert sorted(map(repr, old.execute(f"SELECT {projection} FROM {quoted}"))) == sorted(map(repr, new.execute(f"SELECT {projection} FROM {quoted}")))
        if "file_state" not in {r[1] for r in old.execute("PRAGMA table_info(sources)")}:
            assert new.execute("SELECT count(*) FROM sources WHERE file_state!='active'").fetchone()[0] == 0
    with sqlite3.connect(original.as_uri() + "?mode=ro", uri=True) as source:
        assert sqlite_snapshot(source) == before, "Original changed concurrently during verification."
    assert hashlib.sha256(original.read_bytes()).hexdigest() == original_hash, "Original file changed."
    print("SQLite: consistent backup, integrity, foreign keys, schema and all rows preserved.", flush=True)
    return {"passed": True, "backup": str(backup), "original": str(original),
            "original_sha256": original_hash, "original_unchanged": True,
            "verified_revision": revision, "disposable_copy_head": head, **after}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres", action="store_true", help="Create a temporary Docker pgvector database.")
    args = parser.parse_args()
    os.chdir(ROOT)
    # Avoid reading existing provider credentials in any child application/test.
    safe_env = dict(os.environ, APP_ENV="development", STORAGE_BACKEND="local",
                    GEMINI_API_KEY="", LLM_ENABLED="false", LLM_PROVIDER="none", OPENROUTER_API_KEY="", GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="",
                    SUPABASE_SERVICE_ROLE_KEY="", EMBEDDING_MODEL_PATH="")
    work = ROOT / ".scratch" / ("database-verification-" + secrets.token_hex(6))
    work.mkdir(parents=True)
    report = {"executed_at": datetime.now(timezone.utc).isoformat(), "work_directory": str(work)}
    secret_values = []

    def redact(value):
        for secret in secret_values:
            value = value.replace(secret, "[REDACTED]")
        return value

    def run(command, extra=None, *, check=True, timeout=300):
        result = subprocess.run(command, cwd=ROOT, env=dict(safe_env, **(extra or {})),
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=timeout)
        if result.returncode and check:
            print(redact(result.stdout[-16000:] + result.stderr[-16000:]), flush=True)
            raise RuntimeError(f"{command[0]} exited {result.returncode}")
        return result

    def tests(name, paths, extra):
        xml = ROOT / "artifacts" / (name + ".xml")
        result = run([sys.executable, "-m", "pytest", *paths, "-ra", "--tb=short",
                      "--junitxml=" + str(xml)], extra, check=False, timeout=600)
        print(redact(result.stdout + result.stderr), flush=True)
        report[name] = {"exit_code": result.returncode, "junit": str(xml)}
        if xml.exists():
            cases = ElementTree.parse(xml).getroot().findall(".//testcase")
            skipped = [c.find("skipped") for c in cases if c.find("skipped") is not None]
            failed = sum(c.find("failure") is not None for c in cases)
            errors = sum(c.find("error") is not None for c in cases)
            report[name].update(tests=len(cases), passed=len(cases) - len(skipped) - failed - errors,
                                failed=failed, errors=errors, skipped=len(skipped),
                                skip_reasons=[redact(s.get("message", "")) for s in skipped])
        return result.returncode == 0

    try:
        report["sqlite"] = sqlite_verify(work, run)
        isolated = {"DATABASE_URL": "sqlite:///" + (work / "application-test.db").as_posix(),
                    "TEST_POSTGRES_URL": ""}
        assert tests("sqlite-migration-tests", ["backend/tests/test_publication_migration.py"], isolated)
        if not args.postgres:
            return 0

        def container_ids():
            return set(run(["docker", "ps", "-aq", "--no-trunc"]).stdout.split())

        def volumes():
            return set(run(["docker", "volume", "ls", "--format", "{{.Name}}"]).stdout.split())

        existing_containers, existing_volumes = container_ids(), volumes()
        name = "campusbuddy-verify-" + secrets.token_hex(8)
        password = secrets.token_urlsafe(32)
        secret_values.append(password)
        # Pass the password through the child environment, not a command argument or file.
        safe_env["POSTGRES_PASSWORD"] = password
        created = False
        try:
            print("Starting isolated pgvector container (pulling image if absent).", flush=True)
            result = run([
                "docker", "run", "--detach", "--name", name,
                "--label", "campusbuddy.verification=" + name,
                "--env", "POSTGRES_PASSWORD", "--env", "POSTGRES_USER=campus_verify",
                "--env", "POSTGRES_DB=campus_verify", "--publish", "127.0.0.1::5432",
                "--tmpfs", "/var/lib/postgresql/data:rw", "pgvector/pgvector:pg16",
            ], timeout=300)
            created = True
            container_id = result.stdout.strip().splitlines()[-1]
            info = json.loads(run(["docker", "inspect", name]).stdout)[0]
            assert info["Id"] == container_id
            assert info["Config"]["Labels"]["campusbuddy.verification"] == name
            assert all(m["Type"] == "tmpfs" for m in info["Mounts"]), "Unexpected persistent mount"
            ports = info["NetworkSettings"]["Ports"]["5432/tcp"]
            assert len(ports) == 1 and ports[0]["HostIp"] == "127.0.0.1"
            port = ports[0]["HostPort"]
            url = f"postgresql+psycopg://campus_verify:{password}@127.0.0.1:{port}/campus_verify"
            secret_values.append(url)
            pg_env = dict(isolated, DATABASE_URL=url, TEST_POSTGRES_URL=url)
            for _ in range(60):
                ready = run(["docker", "exec", name, "pg_isready", "-U", "campus_verify",
                             "-d", "campus_verify"], check=False)
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Temporary PostgreSQL did not become ready within 60 seconds.")
            run([sys.executable, "-m", "alembic", "upgrade", "head"], pg_env)
            print("PostgreSQL: migrations through head completed.", flush=True)
            report["postgres"] = {"migrated_to": report["sqlite"]["disposable_copy_head"], "tmpfs_only": True, "loopback_only": True}
            pg_ok = tests("postgres-tests", ["backend/tests/test_postgres.py",
                                             "backend/tests/test_postgres_migration.py"], pg_env)
            if not pg_ok:
                raise RuntimeError("PostgreSQL verification failed; see JUnit results before broader testing.")
            # Unit/API tests use isolated SQLite fixtures; RLS tests use only this temporary container.
            regression_env = dict(pg_env, DATABASE_URL=isolated["DATABASE_URL"])
            all_ok = tests("backend-database-verification", ["backend/tests"], regression_env)
            if not all_ok:
                raise RuntimeError("Database verification tests failed; see JUnit results.")
        finally:
            if created:
                info = json.loads(run(["docker", "inspect", name]).stdout)[0]
                assert info["Id"] not in existing_containers
                assert info["Config"]["Labels"]["campusbuddy.verification"] == name
                run(["docker", "rm", "--force", info["Id"]])
                print("Removed only this invocation's temporary PostgreSQL container.", flush=True)
                report["temporary_container_removed"] = True
            assert container_ids() == existing_containers, "Docker container inventory changed concurrently."
            assert volumes() == existing_volumes, "Docker volume inventory changed concurrently."
            report["existing_docker_inventory_preserved"] = True
        return 0
    except Exception as exc:
        # Never expose SQLAlchemy connection URLs or generated credentials on failure.
        report["error"] = redact(str(exc))
        print("Verification failed: " + report["error"], flush=True)
        return 1
    finally:
        target = ROOT / "artifacts" / "database-verification.json"
        target.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Report: " + str(target), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
