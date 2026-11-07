"""Bounded synthetic service benchmark; no original database or live credentials."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics
import tempfile
import time
from zoneinfo import ZoneInfo
from sqlalchemy import event
from sqlalchemy.orm import Session
from backend.app.db import make_engine
from backend.app.models import Base
from backend.app.seed import seed, DEMO_TENANT
from backend.app.auth import resolve_actor
from backend.app.services import CampusService
from backend.tests.conftest import MemoryStore, USER


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    work = Path(tempfile.mkdtemp(prefix="performance-", dir=root / ".scratch"))
    engine = make_engine("sqlite:///" + (work / "benchmark.db").as_posix())
    Base.metadata.create_all(engine)
    now = datetime(2026, 11, 2, 12, tzinfo=ZoneInfo("Asia/Kolkata"))
    with Session(engine) as db:
        seed(db, USER, anchor=now.date(), store=MemoryStore())
        db.commit()
        service = CampusService(db, resolve_actor(db, USER, DEMO_TENANT), clock=lambda: now)
        queries = []
        event.listen(engine, "before_cursor_execute", lambda *_: queries.append(1))
        times, query_counts = [], []
        for _ in range(30):
            start_queries = len(queries)
            started = time.perf_counter()
            result = service.menu(100, True)
            times.append((time.perf_counter() - started) * 1000)
            query_counts.append(len(queries) - start_queries)
            assert result
    report = dict(scope="30 synthetic local service queries; not production HTTP latency", samples=30,
                  p50_ms=round(statistics.median(times), 3), p95_ms=round(sorted(times)[28], 3),
                  median_sql_queries=statistics.median(query_counts), label=args.label)
    from backend.app.config import settings
    report["redis_enabled"] = settings().redis_enabled
    (root / "artifacts" / ("performance-" + args.label + ".json")).write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
    engine.dispose()


if __name__ == "__main__":
    main()
