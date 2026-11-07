"""Local worker for only the approved project; trusted DB URL stays process-local.

The API keeps its separate restricted login. No migration, seeding or download is
performed here. This local operator profile is not a hosted worker deployment.
"""

import json
import os

from dotenv import dotenv_values
import psycopg

from scripts.configure_campusos_kt import INPUT, SetupError, connection_parameters, validate, validate_database

TARGET = "rgqrkgyzxgiwlbuhyohh"


def main():
    values = dotenv_values(INPUT, interpolate=False)
    if validate(values, database=True) != TARGET:
        raise SetupError("Approved worker project mismatch.")
    params=connection_parameters(values["MIGRATION_DATABASE_URL"],TARGET)
    params['connect_timeout']=15
    with psycopg.connect(**params) as connection:
        connection.read_only = True
        if connection.execute("SELECT version_num FROM public.alembic_version").fetchone() not in (("0008",),("0009",)):
            raise SetupError("Worker requires verified revision 0008 or 0009; stopped.")
        connection.rollback()
    # Existing worker architecture needs a trusted connection to claim jobs across
    # tenants, then reauthorizes each uploader. Never copy this credential to .env.
    url = validate_database(values["MIGRATION_DATABASE_URL"], TARGET)
    os.environ["DATABASE_URL"] = url.set(drivername="postgresql+psycopg").update_query_dict(
        dict(sslmode=url.query.get("sslmode", "require"))).render_as_string(hide_password=False)
    from backend.app.worker import main as worker

    print(json.dumps(dict(event="local_worker_started", project_ref=TARGET,
                          api_credential_unchanged=True, migrations_run=False, seed_data_created=False)), flush=True)
    worker()


if __name__ == "__main__":
    try:
        main()
    except SetupError as exc:
        print(str(exc))
        raise SystemExit(2)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print("Worker stopped: " + type(exc).__name__ + ". Details withheld; inspect state before restarting.")
        raise SystemExit(2)
