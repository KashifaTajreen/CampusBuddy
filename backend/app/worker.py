import argparse
import time
import json
from sqlalchemy.exc import OperationalError
from backend.app.db import SessionLocal, initialize_local
from backend.app.ingestion import work_once


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    initialize_local()
    connection_failures = 0
    while True:
        try:
            worked = work_once(SessionLocal)
            connection_failures = 0
        except OperationalError:
            if args.once:
                raise
            connection_failures += 1
            # Sessions roll back on exit. Existing claim leases and source locks
            # remain authoritative; never force an in-flight job back to queued.
            print(json.dumps({"event":"worker_database_reconnect", "attempt":connection_failures}), flush=True)
            time.sleep(min(2 ** min(connection_failures, 4), 10))
            continue
        if args.once:
            break
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    main()
