"""Create one disposable, loopback-only Redis; run tests, then remove only it."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--databases", action="store_true")
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()
    name = "campus-phase6-redis-"+secrets.token_hex(6)
    password = secrets.token_urlsafe(24)
    env = dict(os.environ, CAMPUS_TEST_REDIS_PASSWORD=password, DATABASE_URL="sqlite:///:memory:",
               LLM_ENABLED="false", LLM_PROVIDER="none", REDIS_ENABLED="false")
    if args.databases:
        # The nested verifier opens the configured development file read-only,
        # then directs all migrations and tests to disposable copies.
        env.pop("DATABASE_URL", None)
    def run(command, **kwargs):
        return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, env=env, check=True, **kwargs)
    before = set(run(["docker","ps","-aq","--no-trunc"]).stdout.split())
    volumes = set(run(["docker","volume","ls","-q"]).stdout.split())
    created = False
    report = {"passed": False, "loopback_only": True, "tmpfs_only": True,
              "generated_test_credentials": True, "credentials_recorded": False}
    try:
        result = run(["docker","run","-d","--name",name,"--label","campusbuddy.phase6="+name,
            "-p","127.0.0.1::6379","--mount","type=tmpfs,destination=/data",
            "--env","CAMPUS_TEST_REDIS_PASSWORD","redis:7-alpine","sh","-c",
            'exec redis-server --save "" --appendonly no --maxmemory 64mb --maxmemory-policy allkeys-lru --requirepass "$CAMPUS_TEST_REDIS_PASSWORD"'])
        container = result.stdout.strip()
        created = True
        info = json.loads(run(["docker","inspect",name]).stdout)[0]
        assert info["Id"] == container and info["Config"]["Labels"]["campusbuddy.phase6"] == name
        assert all(m["Type"] == "tmpfs" for m in info["Mounts"])
        endpoint = info["NetworkSettings"]["Ports"]["6379/tcp"][0]
        assert endpoint["HostIp"] == "127.0.0.1"
        url = f"redis://:{password}@127.0.0.1:{endpoint['HostPort']}/0"
        env["TEST_REDIS_URL"] = url
        import redis
        store = redis.Redis.from_url(url, socket_timeout=1)
        for _ in range(30):
            try:
                if store.ping():
                    break
            except redis.RedisError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Disposable Redis did not become ready.")
        token = secrets.token_hex(16)
        store.set("campus-verification-owner", token)
        env["CAMPUS_REDIS_TEST_TOKEN"] = token
        print("Disposable authenticated Redis is ready on loopback.",flush=True)
        command = ([sys.executable,"-m","scripts.verify_databases","--postgres"] if args.databases else
                   [sys.executable,"-m","pytest","backend/tests/test_cache.py","-q","--junitxml=artifacts/redis-tests.xml"])
        completed = subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=900)
        for filename in ("redis-tests.xml", "backend-database-verification.xml"):
            path = ROOT / "artifacts" / filename
            if path.exists():
                path.write_text(path.read_text(encoding="utf-8").replace(url,"[test Redis URL]").replace(password,"[redacted]"), encoding="utf-8")
        print((completed.stdout+completed.stderr).replace(url,"[test Redis URL]").replace(password,"[redacted]"),flush=True)
        if completed.returncode:
            raise RuntimeError("Redis-enabled verification failed; inspect test reports.")
        if args.benchmark:
            env.update(REDIS_ENABLED="true", REDIS_URL=url)
            benchmark = run([sys.executable, "-m", "scripts.benchmark_phase6", "--label", "redis"], timeout=60)
            print(benchmark.stdout.replace(url, "[test Redis URL]").replace(password, "[redacted]"), flush=True)
        report["passed"] = True
    finally:
        if created:
            info = json.loads(run(["docker","inspect",name]).stdout)[0]
            assert info["Id"] not in before and info["Config"]["Labels"]["campusbuddy.phase6"] == name
            run(["docker","rm","--force",info["Id"]])
            report["temporary_container_removed"] = True
        assert set(run(["docker","ps","-aq","--no-trunc"]).stdout.split()) == before
        assert set(run(["docker","volume","ls","-q"]).stdout.split()) == volumes
        report["existing_docker_inventory_preserved"] = True
        (ROOT / "artifacts/redis-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Removed only this test Redis container; pre-existing Docker inventory preserved.",flush=True)


if __name__ == "__main__":
    main()
