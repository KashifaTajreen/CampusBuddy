"""Migrate and test NEW loopback/tmpfs PostgreSQL databases; never read .env URLs.

Requires an already installed pgvector/pgvector:pg17 image. Only the container
created by this invocation, identified by its random ownership label, is removed.
"""

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]


def main():
    (ROOT / ".scratch").mkdir(exist_ok=True)
    (ROOT / "artifacts").mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="postgres-verification-", dir=ROOT / ".scratch"))
    nonce, password = secrets.token_hex(8), secrets.token_urlsafe(30)
    cid = None

    def docker(*args):
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60,
                                env={**os.environ, "POSTGRES_PASSWORD": password})
        if result.returncode:
            raise RuntimeError("Disposable Docker operation failed; no existing resources modified.")
        return result.stdout.strip()

    def command(name, args, env):
        result = subprocess.run([sys.executable, *args], cwd=ROOT, env=env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=360)
        log = (result.stdout + result.stderr).replace(password, "[REDACTED]")
        (work / (name + ".log")).write_text(log, encoding="utf-8")
        print(name + ": " + ("PASS" if not result.returncode else "FAIL"), flush=True)
        if result.returncode:
            raise RuntimeError(name + " failed; sanitized log is in the ignored verification directory.")

    try:
        docker("image", "inspect", "pgvector/pgvector:pg17")
        cid = docker("run", "--rm", "-d", "--name", "campusos-verify-" + nonce,
                     "--label", "campusos.verify=" + nonce, "-p", "127.0.0.1::5432",
                     "--tmpfs", "/var/lib/postgresql/data", "--env", "POSTGRES_PASSWORD",
                     "--env", "POSTGRES_USER=campus_verify", "--env", "POSTGRES_DB=campus_verify", "pgvector/pgvector:pg17")
        info = json.loads(docker("inspect", cid))[0]
        assert info["Config"]["Labels"].get("campusos.verify") == nonce
        port = info["NetworkSettings"]["Ports"]["5432/tcp"][0]
        assert port["HostIp"] == "127.0.0.1" and all(m["Type"] == "tmpfs" for m in info["Mounts"])
        params = dict(host="127.0.0.1", port=int(port["HostPort"]), dbname="campus_verify",
                      user="campus_verify", password=password, connect_timeout=2)
        for attempt in range(60):
            try:
                with psycopg.connect(**params, autocommit=True) as conn:
                    conn.execute("CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN")
                    for name in ("campus_import_rehearsal", "campus_timetable_rehearsal", "campus_events_rehearsal"):
                        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                break
            except psycopg.OperationalError:
                if attempt == 59:
                    raise RuntimeError("Disposable PostgreSQL did not become ready.") from None
                time.sleep(0.5)
        env = {k: v for k, v in os.environ.items() if not k.startswith("TEST_")}
        env.update(APP_ENV="development", PYTHONUTF8="1", LLM_ENABLED="false", LLM_PROVIDER="none",
                   REDIS_ENABLED="false", GOOGLE_TEST_STUDENT_ENABLED="false")
        groups = [
            ("core", "campus_verify", "TEST_POSTGRES_URL", ["test_postgres.py", "test_postgres_migration.py"]),
            ("import-policy", "campus_import_rehearsal", "TEST_IMPORT_POSTGRES_URL", ["test_import_policy_postgres.py"]),
            ("timetable", "campus_timetable_rehearsal", "TEST_TIMETABLE_POSTGRES_URL", ["test_timetable_postgres.py"]),
            ("events", "campus_events_rehearsal", "TEST_EVENTS_POSTGRES_URL", ["test_events_postgres.py"]),
        ]
        for label, database, variable, files in groups:
            url = URL.create("postgresql+psycopg", username="campus_verify", password=password,
                             host="127.0.0.1", port=int(port["HostPort"]), database=database)
            scoped = {**env, "DATABASE_URL": url.render_as_string(hide_password=False),
                      variable: url.render_as_string(hide_password=False)}
            command(label + "-migration", ["-m", "alembic", "upgrade", "0009"], scoped)
            with psycopg.connect(**{**params, "dbname": database}) as conn:
                assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("0009",)
                assert conn.execute("SELECT extname FROM pg_extension WHERE extname='vector'").fetchone()
            command(label + "-tests", ["-m", "pytest", *["backend/tests/" + f for f in files],
                    "-q", "--tb=short", "--junitxml=artifacts/github-postgres-" + label + ".xml"], scoped)
        print("All disposable PostgreSQL groups passed. Revision 0009; pgvector verified.", flush=True)
        return 0
    finally:
        if cid:
            owned = json.loads(docker("inspect", cid))[0]
            if owned["Config"]["Labels"].get("campusos.verify") != nonce:
                raise RuntimeError("Ownership check failed; refusing container cleanup.")
            docker("rm", "-f", cid)
            print("Removed only this invocation's temporary container; no named volumes used.", flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("Local PostgreSQL verification stopped: " + type(exc).__name__ + ". See ignored logs.")
        raise SystemExit(2)
