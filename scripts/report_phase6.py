"""Derive Phase 6 evidence from executed reports; read the original SQLite only."""
from datetime import datetime
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT / "artifacts" / name).read_text(encoding="utf-8"))


def junit(name):
    cases = ElementTree.parse(ROOT / "artifacts" / name).getroot().findall(".//testcase")
    failures = sum(c.find("failure") is not None for c in cases)
    errors = sum(c.find("error") is not None for c in cases)
    skips = [c.find("skipped").get("message", "") for c in cases if c.find("skipped") is not None]
    return dict(evidence=name, tests=len(cases), passed=len(cases)-failures-errors-len(skips),
                failed=failures, errors=errors, skipped=len(skips), skip_reasons=skips)


def main():
    baseline = read("development-migration-0005.json")
    original = Path(baseline["database"]).resolve(strict=True)
    assert original == (ROOT / "data/campus.db").resolve(), "Unexpected original database path"
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
        checks.append(dict(name=name, exit_code=result.returncode, command=command,
                           output=result.stdout+result.stderr))
    browser = read("browser-tests.json")
    device_voices = []
    def collect(suite):
        for spec in suite.get("specs", []):
            for test in spec["tests"]:
                for result in test.get("results", []):
                    for attachment in result.get("attachments", []):
                        if attachment["name"] == "actual-device-voices" and attachment.get("body"):
                            device_voices.append(dict(project=test.get("projectName"),
                                voices=json.loads(base64.b64decode(attachment["body"]))))
        for child in suite.get("suites", []):
            collect(child)
    for suite in browser["suites"]:
        collect(suite)
    build = read("phase6-build-verification.json")
    report = dict(executed_at=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
        phase6_complete=False, status="Local implementation verified; integration and quality acceptance pending",
        backend=junit("backend-database-verification.xml"), postgres=junit("postgres-tests.xml"),
        redis=junit("redis-tests.xml"), sqlite_migration=junit("sqlite-migration-tests.xml"),
        browser=dict(evidence="browser-tests.json", **browser["stats"], device_voice_inventory=device_voices),
        frontend_build=[c for c in build["checks"] if c["name"] in ("typecheck", "build")],
        checks=checks, original_database=dict(revision=revision, sha256=digest,
            unchanged_since_approved_0005=True, integrity="ok", foreign_key_violations=0),
        database_verification="database-verification.json", redis_container=read("redis-verification.json"),
        ocr=dict(evidence="ocr-real-evaluation.json", cases=len(read("ocr-real-evaluation.json")["cases"]),
            mean_item_recall=read("ocr-real-evaluation.json")["mean_item_recall"],
            mean_price_accuracy=read("ocr-real-evaluation.json")["mean_price_accuracy"],
            fixtures="Real raster files generated from synthetic printed menus, not cafeteria photographs"),
        speech=dict(evidence="speech-real.json", real_backend_and_browser_stt=True,
            fixture="Synthetic English WAV; Chrome fake device, not physical microphone",
            tts_contract_mocked=True, actual_device_availability_checked=True,
            audible_quality_verified=False, human_multilingual_accuracy_verified=False),
        performance={name: read("performance-"+name+".json") for name in ("before", "after-batch", "redis", "http")},
        unverified=["Real cafeteria photographs and broader OCR accuracy", "Human Kannada/Hindi/mixed speech and names/codes",
            "Audible naturalness and language-specific local/neural TTS", "Actual HTTPS mobile microphone/browser behavior",
            "Live Supabase Auth login/refresh/key rotation", "Private Supabase Storage bucket and direct-access denials",
            "Deployed restricted API login/pooler and hosted worker restart/load behavior", "Live external model providers"],
        staging_or_production_accessed=False, live_credentials_or_external_inference_used=False)
    target = ROOT / "artifacts/phase6-verification.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert not any(c["exit_code"] for c in checks)
    assert all(report[name]["failed"] == report[name]["errors"] == 0 for name in ("backend", "postgres", "redis", "sqlite_migration"))
    assert not browser["stats"]["unexpected"] and not browser["stats"]["flaky"]
    print(json.dumps(dict(backend=report["backend"], browser=browser["stats"],
                         original_unchanged=True, phase6_complete=False)))


if __name__ == "__main__":
    main()
