"""TEST ONLY: isolate real worker entry points and local object files."""

import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[2]


def fixture_directory():
    parent = (ROOT / ".scratch").resolve()
    directory = Path(os.environ["CAMPUS_TEST_DIRECTORY"]).resolve()
    if not directory.is_relative_to(parent) or directory == parent:
        raise ValueError("Test processes require their own directory inside .scratch.")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def worker_main():
    directory = fixture_directory()
    from sqlalchemy.engine import make_url

    url = make_url(os.environ["DATABASE_URL"])
    if url.get_backend_name() == "sqlite":
        if not Path(url.database).resolve().is_relative_to(directory):
            raise ValueError("The worker test SQLite file must belong to its fixture directory.")
    elif not (url.get_backend_name() == "postgresql" and url.host == "127.0.0.1"
              and url.database == "campus_verify" and url.username == "campus_verify"):
        raise ValueError("Worker process tests accept only the disposable verification PostgreSQL database.")
    os.environ.update(APP_ENV="development", STORAGE_BACKEND="local", SUPABASE_SERVICE_ROLE_KEY="",
                      GEMINI_API_KEY="", LLM_ENABLED="false", LLM_PROVIDER="none", OPENROUTER_API_KEY="", GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="", EMBEDDING_MODEL_PATH=os.environ.get("CAMPUS_TEST_SEMANTIC_PATH", ""))
    from backend.app import storage, ingestion

    storage.ROOT = directory
    if os.environ.get("CAMPUS_TEST_BLOCK_READ") == "1":
        class BlockingStore(storage.ObjectStore):
            def get(self, key):
                (directory / "worker-reading.marker").write_text("Synthetic test pause", encoding="utf-8")
                deadline = time.monotonic() + 60
                while not (directory / "release-worker.marker").exists():
                    if time.monotonic() > deadline:
                        raise RuntimeError("Synthetic test read pause timed out")
                    time.sleep(0.05)
                return super().get(key)

        ingestion.ObjectStore = BlockingStore
    from backend.app.worker import main

    main()


if __name__ == "__main__":
    worker_main()
