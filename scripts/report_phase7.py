"""Combine executed Phase 7 evidence. Model-quality failures are not test passes."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from zoneinfo import ZoneInfo
from scripts.report_phase6 import junit

ROOT=Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT/"artifacts"/name).read_text(encoding="utf-8"))


def main():
    original=ROOT/"data/campus.db"
    baseline=read("development-migration-0005.json")
    digest=hashlib.sha256(original.read_bytes()).hexdigest()
    assert digest==baseline["after_sha256"],"Original development database changed; stop"
    with sqlite3.connect(original.as_uri()+"?mode=ro",uri=True) as db:
        revision=db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        integrity=db.execute("PRAGMA integrity_check").fetchall()
        violations=db.execute("PRAGMA foreign_key_check").fetchall()
    assert revision=="0005" and integrity==[("ok",)] and not violations
    database=read("database-verification.json")
    redis=read("redis-verification.json")
    browsers=read("phase7-frontend.json")["projects"]
    model=read("phase7-model-benchmark.json")
    deployment=read("phase7-deployment-schema.json")
    checks=[]
    for name,command,cwd in (
        ("ruff",[sys.executable,"-m","ruff","check","backend","scripts"],ROOT),
        ("python_dependencies",[sys.executable,"-m","pip","check"],ROOT),
        ("node_dependencies",["npm.cmd" if sys.platform=="win32" else "npm","ls","--depth=0"],ROOT/"frontend"),
        ("diff",["git","diff","--check"],ROOT)):
        result=subprocess.run(command,cwd=cwd,capture_output=True,text=True,encoding="utf-8",errors="replace")
        checks.append(dict(name=name,exit_code=result.returncode,output=result.stdout+result.stderr))
    started=datetime.fromisoformat(database["executed_at"]).timestamp()
    changed=[str(p.relative_to(ROOT)) for p in (ROOT/"backend").rglob("*.py") if p.stat().st_mtime>started]
    for path,sha in model["source_sha256"].items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==sha,"Benchmark source differs from executed evidence"
    snapshot=[]
    for name in ("database-verification.json","backend-database-verification.xml","postgres-tests.xml",
                 "sqlite-migration-tests.xml","redis-verification.json"):
        target="phase7-final-"+name
        shutil.copyfile(ROOT/"artifacts"/name,ROOT/"artifacts"/target)
        snapshot.append(target)
    backend=junit("backend-database-verification.xml")
    postgres=junit("postgres-tests.xml")
    sqlite=junit("sqlite-migration-tests.xml")
    report=dict(executed_at=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
        backend=backend,postgres=postgres,sqlite_migration=sqlite,redis=redis,checks=checks,
        production_frontend=dict(projects=browsers,passed=sum(p.get("browser",{}).get("expected",0) for p in browsers),
            failed=sum(p.get("browser",{}).get("unexpected",0) for p in browsers),
            skipped=sum(p.get("browser",{}).get("skipped",0) for p in browsers)),
        models=model,benchmark_execution_completed=bool(len(model.get("generation",{}).get("cases",[]))==29),
        model_quality_acceptance_passed=False,
        model_quality_note="Observed invalid outputs and unavailable human review prevent generation qualification; Small fails production memory ceiling. See model report.",
        deployment_schema=deployment,inventory=read("phase7-local-audit.json"),
        original_database=dict(revision=revision,unchanged=True,sha256=digest,integrity="ok",foreign_key_violations=0),
        backend_files_changed_since_verification_start=changed,evidence_snapshots=snapshot,
        live_deployed=False,live_verified=False,final_acceptance_complete=False,
        unverified=["Confirmed staging targets and credentials; resource/migration/spending approval",
            "Staging Supabase Auth, signing keys, private Storage and deployed restricted database roles",
            "Hosted worker restarts/load and physically installed HTTPS mobile PWA",
            "Human speech, audible multilingual TTS, native fluency/translation, representative real-campus OCR/retrieval",
            "Qwen comparison not approved; no Qwen downloaded or run"])
    passed=(all(r["failed"]==r["errors"]==r["skipped"]==0 for r in (backend,postgres,sqlite))
        and all(c["exit_code"]==0 for c in checks) and not changed and redis["passed"]
        and database["temporary_container_removed"] and database["existing_docker_inventory_preserved"]
        and redis["temporary_container_removed"] and redis["existing_docker_inventory_preserved"]
        and len(browsers)==3 and all(p.get("browser",{}).get("expected",0)>0 and
            all(p["browser"][k]==0 for k in ("unexpected","skipped","flaky")) and
            all(c["exit_code"]==0 for c in p["checks"]) for p in browsers)
        and all(c["valid"] for c in deployment["checks"]))
    report["local_regression_passed"]=passed
    assert hashlib.sha256(original.read_bytes()).hexdigest()==digest
    (ROOT/"artifacts/phase7-verification.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(dict(local_regression_passed=passed,backend=backend["passed"],browser=report["production_frontend"]["passed"],
        original_unchanged=True,live_deployed=False,final_acceptance_complete=False)))
    assert passed,"Unresolved local regression checks; inspect report"


if __name__=="__main__":
    main()
