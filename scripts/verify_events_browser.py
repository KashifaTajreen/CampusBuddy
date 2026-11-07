"""Synthetic Events browser check using separate frontend cache, API, DB and worker."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    from backend.app.config import settings
    from backend.tests.test_events_phase2 import fixtures

    directory = Path(tempfile.mkdtemp(prefix="events-browser-", dir=ROOT / ".scratch"))
    frontend = directory / "frontend"
    frontend.mkdir()
    for name in ("src", "public"):
        shutil.copytree(ROOT / "frontend" / name, frontend / name)
    for name in (
        "package.json",
        "package-lock.json",
        "next.config.ts",
        "next-env.d.ts",
        "tsconfig.json",
        "postcss.config.mjs",
    ):
        if (ROOT / "frontend" / name).exists():
            shutil.copy2(ROOT / "frontend" / name, frontend / name)
    # This junction only reuses installed packages; .next and all .env files are separate.
    junction = frontend / "node_modules"
    target = ROOT / "frontend/node_modules"
    if os.name == "nt":
        command = (
            "New-Item -ItemType Junction -Path '"
            + str(junction).replace("'", "''")
            + "' -Target '"
            + str(target).replace("'", "''")
            + "' | Out-Null"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", command], check=True, capture_output=True)
    else:
        junction.symlink_to(target, target_is_directory=True)
    fixture_dir = fixtures(directory / "fixtures")
    env = {
        **os.environ,
        "CAMPUS_TEST_FRONTEND_PORT": "3102",
        "CAMPUS_TEST_API_PORT": "8865",
        "CAMPUS_TEST_AUTH_PORT": "8866",
        "CAMPUS_TEST_EVENTS_FRONTEND_DIR": str(frontend),
        "CAMPUS_TEST_EVENT_FIXTURES": str(fixture_dir),
        "CAMPUS_TEST_DIRECTORY": str(directory / "data"),
        "CAMPUS_TEST_SEMANTIC_PATH": str(Path(settings().embedding_model_path).resolve()),
        "PLAYWRIGHT_CHANNEL": "chrome",
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    command = (
        ["cmd", "/c", "npx", "playwright", "test", "--config=playwright.events.config.ts"]
        if os.name == "nt"
        else ["npx", "playwright", "test", "--config=playwright.events.config.ts"]
    )
    result = subprocess.run(command, cwd=ROOT / "frontend", env=env, timeout=420)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
