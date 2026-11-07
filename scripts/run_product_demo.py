"""TEST ONLY: complete local product with synthetic identity and disposable data."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import httpx
from backend.tests.test_worker_processes import Processes, free_port, wait_for, login, queue_via_api
from scripts.verify_frontend import own_windows_tree

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--semantic", action="store_true", help="Use the already approved local E5 model.")
    parser.add_argument("--smoke", action="store_true", help="Check API, separate worker and frontend then stop only these processes.")
    args = parser.parse_args()
    group = Processes()
    cleanup = []
    api, auth, frontend = free_port(), free_port(), free_port()
    group.api_url, group.auth_url = f"http://127.0.0.1:{api}", f"http://127.0.0.1:{auth}"
    group.env.update(CAMPUS_TEST_API_PORT=str(api), CAMPUS_TEST_AUTH_PORT=str(auth),
        CAMPUS_TEST_FRONTEND_PORT=str(frontend), LLM_ENABLED="false", LLM_PROVIDER="none", REDIS_ENABLED="false",
        NEXT_PUBLIC_API_URL=group.api_url, NEXT_PUBLIC_SUPABASE_URL=group.auth_url,
        NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY="test-only-publishable-key", NEXT_TELEMETRY_DISABLED="1")
    if args.semantic:
        model = ROOT / ".scratch/semantic-e5"
        assert (model / "manifest.json").is_file(), "Install approved model assets explicitly first."
        group.env.update(CAMPUS_TEST_SEMANTIC_PATH=str(model), EMBEDDING_PROVIDER="onnx")
    else:
        group.env.update(CAMPUS_TEST_SEMANTIC_PATH="", EMBEDDING_PROVIDER="lexical")
    group.client = httpx.Client(timeout=30)
    generated = ROOT / "frontend/next-env.d.ts"
    original = generated.read_bytes()
    report = dict(passed=False, original_database_used=False, live_supabase=False,
                  separate_worker=True, semantic=args.semantic, fixture_directory=str(group.directory))
    try:
        backend = group.start("backend.tests.e2e_server")
        cleanup.append(own_windows_tree(backend))
        def ready():
            assert backend.poll() is None, "Owned demo API stopped; inspect fixture logs."
            try:
                return group.client.get(group.api_url+"/health").status_code == 200
            except httpx.TransportError:
                return False
        wait_for(ready, timeout=90)
        worker = group.worker(once=False)
        cleanup.append(own_windows_tree(worker))
        log = (group.directory / "frontend.log").open("w", encoding="utf-8")
        group.logs.append(log)
        web = subprocess.Popen(["npm.cmd" if os.name == "nt" else "npm", "run", "dev", "--", "--port", str(frontend)],
            cwd=ROOT / "frontend", env=group.env, stdout=log, stderr=subprocess.STDOUT)
        group.children.append(web)
        cleanup.append(own_windows_tree(web))
        url = f"http://127.0.0.1:{frontend}"
        def web_ready():
            assert web.poll() is None, "Owned demo frontend stopped; inspect frontend.log."
            try:
                return group.client.get(url).status_code == 200
            except httpx.TransportError:
                return False
        wait_for(web_ready, timeout=120)
        print("SYNTHETIC TEST DEMO ONLY — never deploy this identity fixture.", flush=True)
        print("Frontend: "+url+"; API: "+group.api_url, flush=True)
        print("Accounts: admin, reviewer, department, section, canteen, student, section-b @example.invalid", flush=True)
        print("Test-only password: CampusTest123!", flush=True)
        print("Disposable database/objects/logs: "+str(group.directory), flush=True)
        if args.smoke:
            job = queue_via_api(group)
            headers = login(group)
            def completed():
                jobs = group.client.get(group.api_url+"/v1/ingestion",headers=headers).json()
                return next((j for j in jobs if j["id"] == job["id"] and j["status"] == "completed"), None)
            result = wait_for(completed, timeout=60)
            assert result["source_status"] == "pending_review" and result["preview"]["duplicate_check"]["checked"]
            report["passed"] = True
            print("Smoke passed: frontend, authenticated API, separate persistent worker, duplicate check and unpublished review state.", flush=True)
        else:
            print("Press Ctrl+C to stop this demo's processes; scratch data is retained.", flush=True)
            while all(p.poll() is None for p in group.children):
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for close in reversed(cleanup):
            close()
        group.close()
        group.client.close()
        generated.write_bytes(original)
        if args.smoke:
            report["processes_stopped"] = all(p.poll() is not None for p in group.children)
            report["next_env_restored"] = generated.read_bytes() == original
            (ROOT/"artifacts/phase6b-demo-smoke.json").write_text(json.dumps(report,indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()
