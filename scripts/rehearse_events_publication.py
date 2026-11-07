"""Run only events checks in a new loopback/tmpfs PostgreSQL container."""

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import psycopg
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]


def main():
    scratch = ROOT / '.scratch/events-phase2'
    scratch.mkdir(parents=True, exist_ok=True)
    nonce = secrets.token_hex(8)
    password = secrets.token_urlsafe(24)

    def docker(*args):
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=60,
            env={**os.environ, "POSTGRES_PASSWORD": password},
        )
        if result.returncode:
            raise RuntimeError("Owned events Docker operation failed.")
        return result.stdout.strip()

    docker("image", "inspect", "pgvector/pgvector:pg17")  # Reuse installed image; no downloads.
    cid = docker(
        "run",
        "--rm",
        "-d",
        "--name",
        "campusos-events-" + nonce,
        "--label",
        "campusos.events=" + nonce,
        "-p",
        "127.0.0.1::5432",
        "--tmpfs",
        "/var/lib/postgresql/data",
        "--env",
        "POSTGRES_PASSWORD",
        "--env",
        "POSTGRES_DB=campus_events_rehearsal",
        "pgvector/pgvector:pg17",
    )
    try:
        info = json.loads(docker("inspect", cid))[0]
        assert info["Config"]["Labels"]["campusos.events"] == nonce
        port = info["NetworkSettings"]["Ports"]["5432/tcp"][0]
        assert port["HostIp"] == "127.0.0.1" and all(m["Type"] == "tmpfs" for m in info["Mounts"])
        params = dict(
            host="127.0.0.1",
            port=int(port["HostPort"]),
            user="postgres",
            password=password,
            dbname="campus_events_rehearsal",
            connect_timeout=2,
        )
        for attempt in range(60):
            try:
                with psycopg.connect(**params) as c:
                    c.execute("CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN")
                break
            except psycopg.OperationalError:
                if attempt == 59:
                    raise
                time.sleep(0.25)
        url = URL.create(
            "postgresql+psycopg",
            username="postgres",
            password=password,
            host="127.0.0.1",
            port=int(port["HostPort"]),
            database="campus_events_rehearsal",
        ).render_as_string(hide_password=False)
        env = {**os.environ, "DATABASE_URL": url, "TEST_EVENTS_POSTGRES_URL": url}
        for name, command in [
            ("migration", [sys.executable, "-m", "alembic", "upgrade", "0009"]),
            (
                "tests",
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "backend/tests/test_events_postgres.py",
                    "-q",
                    "--junitxml=artifacts/events-postgres-tests.xml",
                ],
            ),
        ]:
            result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
            (scratch / ("postgres-" + name + ".log")).write_text(
                (result.stdout + result.stderr).replace(password, "[REDACTED]"), encoding="utf-8"
            )
            if result.returncode:
                raise RuntimeError("Events local " + name + " failed; sanitized details saved.")
        report = dict(
            passed=True,
            revision="0009",
            loopback_only=True,
            tmpfs_only=True,
            existing_containers_untouched=True,
            remote_resources_untouched=True,
        )
        (ROOT / "artifacts/events-postgres-rehearsal.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
    finally:
        owned = json.loads(docker("inspect", cid))[0]
        if owned["Config"]["Labels"].get("campusos.events") == nonce:
            docker("rm", "-f", cid)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Events rehearsal stopped: " + type(exc).__name__ + ". No remote migrations performed.")
        raise SystemExit(2)
