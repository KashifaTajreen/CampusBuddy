"""Aggregate executed Phase 6B evidence; never migrate the original database."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from xml.etree import ElementTree

from scripts.report_phase6 import junit, read

ROOT = Path(__file__).resolve().parents[1]


def main():
    baseline = read("development-migration-0005.json")
    original = Path(baseline["database"]).resolve(strict=True)
    assert original == (ROOT / "data/campus.db").resolve(), "Unexpected development database"
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    assert digest == baseline["after_sha256"], "Original database changed; stop and investigate"
    with sqlite3.connect(original.as_uri()+"?mode=ro", uri=True) as db:
        revision = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        integrity = db.execute("PRAGMA integrity_check").fetchall()
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
    assert revision == "0005" and integrity == [("ok",)] and not foreign_keys
    checks = []
    for name, command in (("ruff", [sys.executable, "-m", "ruff", "check", "backend", "scripts"]),
                          ("diff", ["git", "diff", "--check"])):
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        checks.append(dict(name=name, exit_code=result.returncode, output=result.stdout+result.stderr))
    backend = junit("backend-database-verification.xml")
    cases = ElementTree.parse(ROOT / "artifacts" / backend["evidence"]).getroot().findall(".//testcase")
    cache_cases = [c for c in cases if c.get("classname") == "backend.tests.test_cache"]
    frontend = read("frontend-verification.json")
    semantic = read("phase6b-semantic-evaluation.json")
    database = read("database-verification.json")
    redis = read("redis-verification.json")
    smoke = read("phase6b-demo-smoke.json")
    report = dict(executed_at=datetime.now(timezone.utc).isoformat(), phase6b_complete=False,
        status="Local product workflows verified; original migration approval and staging acceptance pending",
        backend=backend, postgres=junit("postgres-tests.xml"),
        sqlite_migration=junit("sqlite-migration-tests.xml"),
        redis_cases_in_full_backend=dict(tests=len(cache_cases),
            passed=sum(not any(c.find(tag) is not None for tag in ("failure", "error", "skipped")) for c in cache_cases),
            evidence=backend["evidence"], note="Subset of full backend total, not additional tests"),
        frontend=frontend, checks=checks,
        semantic=dict(evidence="phase6b-semantic-evaluation.json", model=semantic["model"],
            dimensions=semantic["dimensions"], cases=len(semantic["cases"]), scores=semantic["scores"],
            acceptance_passed=semantic["acceptance_passed"], representative_campus_accuracy_verified=False),
        demo=dict(evidence="phase6b-demo-smoke.json", **smoke),
        original_database=dict(revision=revision, sha256=digest, unchanged_since_approved_0005=True,
            integrity="ok", foreign_key_violations=0, opened_read_only=True),
        migration=dict(required="0006", applied_to_original=False,
            disposable_copy_revision=database["sqlite"]["disposable_copy_head"],
            original_values_preserved=database["sqlite"]["passed"],
            evidence="database-verification.json"),
        docker=dict(postgres_container_removed=database["temporary_container_removed"],
            redis_container_removed=redis["temporary_container_removed"],
            existing_inventory_preserved=database["existing_docker_inventory_preserved"] and redis["existing_docker_inventory_preserved"],
            generated_test_credentials=True, credentials_recorded=False),
        real_integrations=["Local multilingual E5 ONNX/tokenizer inference", "PostgreSQL RLS/pgvector",
            "Authenticated disposable Redis", "SQLite backup and disposable migrations",
            "Persistent separate API/worker processes", "Browser/API review and publication workflows"],
        mocked_or_fixture=["RSA-signed synthetic Auth, not live Supabase", "Supabase Storage HTTP contract",
            "External model providers", "Google provider HTTP"],
        remaining_acceptance=["Explicit approval and maintenance for original database migration 0006",
            "Real staging Supabase login/refresh/JWKS rotation and membership revocation",
            "Private staging bucket and direct anonymous/unauthorized access denial",
            "Hosted restricted API login/pooler and worker restart/concurrency/load",
            "Representative human-reviewed multilingual campus retrieval corpus",
            "Human OCR/speech quality and real mobile HTTPS audio checks"],
        staging_or_production_accessed=False, live_credentials_used=False)
    target = ROOT / "artifacts/phase6b-verification.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert not any(c["exit_code"] for c in checks)
    for key in ("backend", "postgres", "sqlite_migration"):
        assert report[key]["failed"] == report[key]["errors"] == report[key]["skipped"] == 0
    assert len(cache_cases) and report["redis_cases_in_full_backend"]["passed"] == len(cache_cases)
    assert not any(c["exit_code"] for c in frontend["checks"])
    assert not any(frontend["browser"][key] for key in ("skipped", "unexpected", "flaky"))
    assert semantic["acceptance_passed"] and smoke["passed"] and smoke["processes_stopped"]
    assert database["sqlite"]["original_unchanged"] and redis["passed"] and all(report["docker"][key]
        for key in ("postgres_container_removed", "redis_container_removed", "existing_inventory_preserved"))
    print(json.dumps(dict(backend=backend, postgres=report["postgres"], browser=frontend["browser"],
        checks=checks, original_unchanged=True, phase6b_complete=False)))


if __name__ == "__main__":
    main()
