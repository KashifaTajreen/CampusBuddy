"""Aggregate actual corrective-pass evidence; the original SQLite is read-only."""
from datetime import datetime
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import re
import sqlite3
import subprocess
import sys
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from scripts.report_phase6 import junit, read

ROOT=Path(__file__).resolve().parents[1]


def speech_evidence(browser):
    from backend.app.speech import capabilities, transcribe
    expected="What is my next class?"
    truth=re.findall(r"\w+",expected.casefold())
    quality=[]
    if capabilities()["local_stt_ready"]:
        audio=(ROOT/"backend/tests/fixtures/speech-question.wav").read_bytes()
        for selection in ("en","auto"):
            output=transcribe(audio,selection)
            hypothesis=re.findall(r"\w+",output["text"].casefold())
            previous=list(range(len(hypothesis)+1))
            for i,word in enumerate(truth,1):
                current=[i]
                for j,other in enumerate(hypothesis,1):
                    current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(word!=other)))
                previous=current
            quality.append(dict(selection=selection,expected=expected,transcript=output["text"],
                word_error_rate=previous[-1]/len(truth),output_language=output["language"],
                language_selection_correct=output["language"]=="en",strategy=output["language_strategy"],
                fixture="Locally synthesized clean English WAV, not human speech"))
    voices=[]
    def collect(suite):
        for spec in suite.get("specs",[]):
            for test in spec.get("tests",[]):
                for result in test.get("results",[]):
                    for attachment in result.get("attachments",[]):
                        if attachment.get("name")=="actual-device-voices" and attachment.get("body"):
                            values=json.loads(base64.b64decode(attachment["body"]))
                            voices.append(dict(project=test.get("projectName"),
                                local_languages=sorted({v["language"] for v in values if v["local"]})))
        for child in suite.get("suites",[]):
            collect(child)
    for suite in browser.get("suites",[]):
        collect(suite)
    return dict(synthetic_english_quality=quality,device_voice_inventory=voices,
        human_quality={language:"Not evaluated: no human recordings provided" for language in
            ("English","Kannada","Hindi","Kannada-English","Hindi-English")},
        browser_views_are_desktop_chrome_emulation=True,synthetic_browser_capture_disables_dsp=True,
        audible_tts_quality="Not evaluated by a human listener")


def main():
    baseline=read("development-migration-0005.json")
    original=Path(baseline["database"]).resolve(strict=True)
    assert original==(ROOT/"data/campus.db").resolve(), "Unexpected original database path"
    digest=hashlib.sha256(original.read_bytes()).hexdigest()
    assert digest==baseline["after_sha256"], "Original database changed; stop and investigate"
    with sqlite3.connect(original.as_uri()+"?mode=ro",uri=True) as db:
        revision=db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        integrity=db.execute("PRAGMA integrity_check").fetchall()
        foreign_keys=db.execute("PRAGMA foreign_key_check").fetchall()
    assert revision=="0005" and integrity==[("ok",)] and not foreign_keys
    checks=[]
    for name,command,directory in (
        ("ruff",[sys.executable,"-m","ruff","check","backend","scripts"],ROOT),
        ("python_dependencies",[sys.executable,"-m","pip","check"],ROOT),
        ("node_dependencies",["npm.cmd" if sys.platform=="win32" else "npm","ls","--depth=0"],ROOT/"frontend"),
        ("diff",["git","diff","--check"],ROOT),
    ):
        result=subprocess.run(command,cwd=directory,capture_output=True,text=True)
        checks.append(dict(name=name,exit_code=result.returncode,output=result.stdout+result.stderr))
    database=read("database-verification.json")
    redis=read("redis-verification.json")
    frontend=read("frontend-verification.json")
    browser=read("browser-tests.json")
    backend=junit("backend-database-verification.xml")
    cases=ElementTree.parse(ROOT/"artifacts"/backend["evidence"]).getroot().findall(".//testcase")
    cache_cases=[c for c in cases if c.get("classname")=="backend.tests.test_cache"]
    real_case=next((c for c in cases if c.get("name")=="test_reported_actual_workbook_local_preview_and_grounded_mentor"),None)
    started=datetime.fromisoformat(database["executed_at"]).timestamp()
    changed=[str(p.relative_to(ROOT)) for p in (ROOT/"backend").rglob("*.py") if p.stat().st_mtime>started]
    # Freeze raw reports so future verification of another phase cannot replace
    # the evidence linked from this report.
    snapshots={}
    for name in ("backend-database-verification.xml","postgres-tests.xml","sqlite-migration-tests.xml",
                 "database-verification.json","redis-verification.json","frontend-verification.json","browser-tests.json"):
        target="prephase7-final-"+name
        shutil.copyfile(ROOT/"artifacts"/name,ROOT/"artifacts"/target)
        snapshots[name]=target
    real_path=Path(os.environ.get('CAMPUS_PRIVATE_WORKBOOK_REFERENCE', str(ROOT/'.scratch/private-fixtures/skill-lab.xlsx')))
    workbook=dict(available=real_path.exists(),test_passed=real_case is not None and
        not any(real_case.find(tag) is not None for tag in ("failure","error","skipped")),
        raw_values_recorded=False,external_inference=False)
    if real_path.exists():
        from backend.app.parsing import parse_file
        content=real_path.read_bytes()
        preview=parse_file(real_path.name,content,preview_only=True)
        workbook.update(filename=real_path.name,bytes=len(content),sha256=hashlib.sha256(content).hexdigest(),
            sheets=[dict(name=t["name"],rows=t["total_rows"],columns=t["columns"]) for t in preview["tables"]])
    smoke=read("phase6b-demo-smoke.json")
    shutil.copyfile(ROOT/"artifacts/phase6b-demo-smoke.json",ROOT/"artifacts/prephase7-demo-smoke.json")
    report=dict(executed_at=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
        corrective_pass_complete=False,status="Local code/regression results below; human voice and approved integration/migration acceptance pending",
        backend=backend,postgres=junit("postgres-tests.xml"),sqlite_migration=junit("sqlite-migration-tests.xml"),
        redis_subset=dict(tests=len(cache_cases),passed=sum(not any(c.find(t) is not None for t in ("failure","error","skipped")) for c in cache_cases),included_in_backend_total=True),
        frontend=frontend,browser=browser["stats"],checks=checks,immutable_evidence=snapshots,
        backend_files_changed_after_verification_start=changed,
        real_workbook=workbook,
        original_database=dict(revision=revision,sha256=digest,unchanged=True,read_only=True,integrity="ok",foreign_key_violations=0),
        migration=dict(required="0007",original_upgraded=False,disposable_head=database["sqlite"]["disposable_copy_head"],
            old_application_values_preserved=database["sqlite"]["passed"],legacy_student_spreadsheets_require_review=True),
        docker=dict(temporary_postgres_removed=database["temporary_container_removed"],temporary_redis_removed=redis["temporary_container_removed"],
            existing_inventory_preserved=database["existing_docker_inventory_preserved"] and redis["existing_docker_inventory_preserved"]),
        separate_worker_demo=smoke,
        speech=dict(real_local_inference=True,real_human_recordings_available=False,
            actual_audio_evidence="Synthetic English WAV, actual browser MediaRecorder, synthetic silence/noise; forced-language code-switching contracts use mocks",
            english_auto_requires_supported_result_or_retry=True,
            human_english_hindi_kannada_code_switching_quality_verified=False,audible_tts_naturalness_verified=False,
            new_model_downloads=False,paid_api_added=False,**speech_evidence(browser)),
        unverified=["Human Hindi/Kannada/English/code-switching and noisy microphones", "Audible device TTS and real HTTPS mobile hardware",
            "Approved original database upgrade and trusted institutional USN provisioning", "Live staging Supabase Auth/Storage",
            "Hosted restricted database login/pooler and worker load/restart", "Broader institutional retrieval/OCR quality"],
        staging_or_production_accessed=False,production_credentials_used=False)
    passed=(all(report[k]["failed"]==report[k]["errors"]==0 for k in ("backend","postgres","sqlite_migration"))
        and not any(c["exit_code"] for c in checks+frontend["checks"])
        and not browser["stats"]["unexpected"] and not browser["stats"]["flaky"]
        and redis["passed"] and all(report["docker"].values()) and not changed and smoke["passed"])
    report["local_verification_passed"]=passed
    assert hashlib.sha256(original.read_bytes()).hexdigest()==digest, "Original changed during reporting; investigate"
    (ROOT/"artifacts/prephase7-verification.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(dict(local_verification_passed=passed,backend=backend,browser=browser["stats"],
        original_unchanged=True,corrective_pass_complete=False)))
    assert passed, "Corrective verification has unresolved checks; inspect generated report"


if __name__=="__main__":
    main()
