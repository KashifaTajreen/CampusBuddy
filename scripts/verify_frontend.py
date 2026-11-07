"""Run frontend regressions against fresh fixtures on unused loopback ports."""

from datetime import datetime, timezone
import argparse
import json
import os
import re
import shutil
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def own_windows_tree(process):
    """A job handle contains only this invocation's npm process and children."""
    if os.name != "nt":
        return lambda: None
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
            ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
            ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong*6),
            ("memory", ctypes.c_size_t*4)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    info = Extended()
    info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(handle, int(process._handle)):
        process.kill()
        if handle:
            kernel.CloseHandle(handle)
        raise RuntimeError("Unable to contain disposable browser verification processes.")
    return lambda: kernel.CloseHandle(handle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-only", action="store_true")
    parser.add_argument("--voice-only", action="store_true")
    parser.add_argument("--production", action="store_true", help="Run browser flows against next start, after an isolated build.")
    parser.add_argument("--spec", choices=["phase7.spec.ts"])
    parser.add_argument("--project", choices=["desktop", "mobile", "tablet"])
    args = parser.parse_args()
    if args.production and args.browser_only:
        parser.error("Production verification requires a fresh build with these fixture URLs.")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    (ROOT / ".scratch").mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="frontend-verification-", dir=ROOT / ".scratch"))
    isolated_frontend = work / "frontend"
    isolated_frontend.mkdir()
    for name in ("src", "public", "tests"):
        shutil.copytree(ROOT / "frontend" / name, isolated_frontend / name)
    for pattern in ("*.json", "*.ts", "*.mjs"):
        for source in (ROOT / "frontend").glob(pattern):
            # Generated next-env points into the running dev cache; typegen/build
            # creates its own version in the disposable copy.
            if source.name != "next-env.d.ts":
                shutil.copy2(source, isolated_frontend / source.name)
    packages = ROOT / "frontend/node_modules"
    junction = isolated_frontend / "node_modules"
    if os.name == "nt":
        command = "New-Item -ItemType Junction -Path '" + str(junction).replace("'", "''") + "' -Target '" + str(packages).replace("'", "''") + "' | Out-Null"
        subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True, capture_output=True)
    else:
        junction.symlink_to(packages, target_is_directory=True)
    sockets = [socket.socket() for _ in range(3)]
    try:
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        ports = [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()
    frontend, api, auth = ports
    env = dict(os.environ, CI="1", NEXT_TELEMETRY_DISABLED="1", SUPABASE_SERVICE_ROLE_KEY="",
        GEMINI_API_KEY="", LLM_ENABLED="false", LLM_PROVIDER="none", OPENROUTER_API_KEY="", GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="",
        REDIS_ENABLED="false", SPEECH_ENABLED="true", OCR_ENABLED="true",
        CAMPUS_TEST_DIRECTORY=str(work / "fixture"), CAMPUS_TEST_BACKGROUND_WORKER="1",
        CAMPUS_TEST_REPOSITORY_ROOT=str(ROOT),
        CAMPUS_TEST_FRONTEND_PORT=str(frontend), CAMPUS_TEST_API_PORT=str(api), CAMPUS_TEST_AUTH_PORT=str(auth),
        NEXT_PUBLIC_API_URL=f"http://127.0.0.1:{api}", NEXT_PUBLIC_SUPABASE_URL=f"http://127.0.0.1:{auth}",
        NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY="test-only-publishable-key")
    env["CAMPUS_TEST_PRODUCTION_FRONTEND"] = "1" if args.production else "0"
    if os.name == "nt" and Path("C:/Program Files/Google/Chrome/Application/chrome.exe").exists():
        env.setdefault("PLAYWRIGHT_CHANNEL", "chrome")
    npm = "npm.cmd" if os.name == "nt" else "npm"
    report = {"executed_at": datetime.now(timezone.utc).isoformat(), "checks": [],
              "fixture_directory": str(work / "fixture"), "loopback_ports": ports,
              "live_supabase": False, "isolated_frontend": str(isolated_frontend)}
    (ROOT / "artifacts").mkdir(exist_ok=True)
    try:
        for name in (("test:e2e",) if args.browser_only else ("typecheck", "build", "test:e2e")):
            print("Running frontend " + name + " with isolated test configuration.", flush=True)
            started = time.time()
            log_path = work / (name.replace(":", "-") + ".log")
            with log_path.open("w", encoding="utf-8") as log:
                command = [npm, "run", name]
                if name == "test:e2e" and (args.voice_only or args.project or args.spec):
                    command.append("--")
                    if args.voice_only:
                        command.append("voice.spec.ts")
                    if args.spec:
                        command.append(r"[\\/]"+re.escape(args.spec)+"$")
                    if args.project:
                        command.extend(["--project", args.project])
                process = subprocess.Popen(command, cwd=isolated_frontend, env=env,
                                           stdout=log, stderr=subprocess.STDOUT)
                cleanup = own_windows_tree(process)
                try:
                    # Full responsive verification now includes real workbook and
                    # speech workflows; keep individual test bounds unchanged.
                    code = process.wait(timeout=900 if name == "test:e2e" else 600)
                except subprocess.TimeoutExpired:
                    code = 124
                finally:
                    cleanup()
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=10)
            report["checks"].append({"name": name, "exit_code": code,
                                     "seconds": round(time.time() - started, 2), "log": str(log_path)})
            print(log_path.read_text(encoding="utf-8", errors="replace")[-6000:], flush=True)
            if name == "test:e2e":
                browser = work / "artifacts/browser-tests.json"
                if browser.exists() and browser.stat().st_mtime >= started:
                    report["browser"] = json.loads(browser.read_text(encoding="utf-8"))["stats"]
                    shutil.copy2(browser, ROOT / "artifacts/browser-tests.json")
            if code:
                return code
        return 0
    finally:
        report["original_frontend_cache_untouched"] = True
        (ROOT / "artifacts/frontend-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
