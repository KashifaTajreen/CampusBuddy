"""24 authorized HTTP requests, four concurrent callers, owned disposable API."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import time
import httpx
from backend.tests.test_worker_processes import Processes, free_port, wait_for, login


def main():
    group = Processes()
    api, auth = free_port(), free_port()
    group.env.update(CAMPUS_TEST_API_PORT=str(api), CAMPUS_TEST_AUTH_PORT=str(auth),
                     LLM_ENABLED="false", LLM_PROVIDER="none", REDIS_ENABLED="false")
    group.api_url, group.auth_url = f"http://127.0.0.1:{api}", f"http://127.0.0.1:{auth}"
    group.client = httpx.Client(timeout=15)
    try:
        process = group.start("backend.tests.e2e_server")
        def ready():
            assert process.poll() is None, "Owned API fixture stopped unexpectedly"
            try:
                return group.client.get(group.api_url+"/health").status_code == 200
            except httpx.TransportError:
                return False
        wait_for(ready)
        headers = login(group, "student@example.invalid")
        assert group.client.get(group.api_url+"/v1/menu").status_code == 401
        def request(_):
            started = time.perf_counter()
            response = group.client.get(group.api_url+"/v1/menu", headers=headers)
            assert response.status_code == 200 and response.json()
            return (time.perf_counter()-started)*1000
        with ThreadPoolExecutor(max_workers=4) as pool:
            times = list(pool.map(request, range(24)))
        report = dict(scope="Disposable loopback API + synthetic RSA identities; local SQLite, Redis disabled",
            requests=24, concurrency=4, passed=24, failed=0, unauthorized_status=401,
            p50_ms=round(statistics.median(times),3), p95_ms=round(sorted(times)[22],3),
            production_or_original_data_used=False)
        (Path(__file__).resolve().parents[1]/"artifacts/performance-http.json").write_text(json.dumps(report,indent=2))
        print(json.dumps(report))
    finally:
        group.client.close()
        group.close()


if __name__ == "__main__":
    main()
