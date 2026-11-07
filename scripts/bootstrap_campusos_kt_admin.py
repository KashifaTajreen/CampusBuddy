"""Operator-only first administrator bootstrap after a real Google sign-in.

Uses trusted Supabase Auth tables for the exact configured email after a recent
real Google sign-in. Never reads tokens/passwords or changes Supabase Auth users.
This is not a public enrollment endpoint.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import shutil
import time
from uuid import UUID, uuid4

from dotenv import dotenv_values
import psycopg

from backend.app.config import settings
from scripts.configure_campusos_kt import INPUT, ROOT, SetupError, connection_parameters, replace_fields, validate

TARGET = "rgqrkgyzxgiwlbuhyohh"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approved-project", required=True)
    args = parser.parse_args()
    if not args.execute or args.approved_project != TARGET:
        raise SetupError("Explicit execution for the approved CampusOS-KT project is required.")
    values = dotenv_values(INPUT, interpolate=False)
    if validate(values, database=True) != TARGET:
        raise SetupError("Approved project mismatch.")
    config = settings()
    if config.supabase_url != values["SUPABASE_URL"]:
        raise SetupError("Backend Auth project mismatch.")
    approved_email = (values.get("ADMIN_EMAIL") or "").strip().lower()
    if not approved_email.endswith("@gmail.com"):
        raise SetupError("This approved exception is for the exact configured Gmail account only.")
    params = connection_parameters(values["MIGRATION_DATABASE_URL"], TARGET)
    with psycopg.connect(**params) as connection:
        connection.read_only = True
        accounts = connection.execute("""SELECT u.id,u.email_confirmed_at,u.last_sign_in_at,
            u.deleted_at,u.banned_until,i.identity_data
            FROM auth.users u JOIN auth.identities i ON i.user_id=u.id
            WHERE lower(u.email)=%s AND i.provider='google'""", (approved_email,)).fetchall()
        if len(accounts) != 1:
            raise SetupError("Exactly one matching Google identity is required; stopped.")
        subject, confirmed, last_login, deleted, banned, identity = accounts[0]
        subject = str(UUID(str(subject)))
        if (not confirmed or not last_login or deleted or (banned and banned > datetime.now(timezone.utc))
                or last_login < datetime.now(timezone.utc) - timedelta(minutes=15)
                or not isinstance(identity, dict) or identity.get("email_verified") is not True
                or str(identity.get("email", "")).lower() != approved_email):
            raise SetupError("A recent, confirmed Google identity for ADMIN_EMAIL is required; stopped.")
        connection.rollback()
    if values.get("ADMIN_AUTH_USER_ID") and values["ADMIN_AUTH_USER_ID"] != subject:
        raise SetupError("Existing administrator identity pin differs; stopped.")
    tenant = str(uuid4())
    directory = ROOT / ".scratch/campusos-kt" / ("admin-bootstrap-" + str(time.time_ns()))
    directory.mkdir(parents=True, exist_ok=False)
    updates = {
        INPUT: dict(ADMIN_AUTH_USER_ID=subject, INSTITUTION_ID=tenant),
        ROOT / ".env": dict(GOOGLE_ADMIN_EMAIL=approved_email, GOOGLE_ADMIN_USER_ID=subject),
        ROOT / "frontend/.env.local": dict(NEXT_PUBLIC_ONBOARDING_TENANT_ID=tenant),
    }
    prepared = {path: replace_fields(path, fields) for path, fields in updates.items()}
    for number, path in enumerate(updates):
        shutil.copyfile(path, directory / (str(number) + ".env.before"))
    (directory / "recovery.json").write_text(json.dumps(dict(subject=subject, tenant_id=tenant)), encoding="utf-8")
    params["options"] = "-c lock_timeout=1000 -c statement_timeout=10000"
    with psycopg.connect(**params) as connection:
        connection.read_only = False
        connection.execute("SELECT pg_advisory_xact_lock(126381, 1)")
        if connection.execute("SELECT version_num FROM public.alembic_version").fetchone() != ("0008",):
            raise SetupError("Unexpected database revision; stopped.")
        account = connection.execute(
            "SELECT email,email_confirmed_at FROM auth.users WHERE id=%s", (subject,)).fetchone()
        if not account or str(account[0]).lower() != approved_email or not account[1]:
            raise SetupError("The verified identity does not match the approved project's confirmed Auth account.")
        if connection.execute("SELECT (SELECT count(*) FROM public.institutions),"
                              "(SELECT count(*) FROM public.memberships)").fetchone() != (0, 0):
            raise SetupError("First-account bootstrap requires an empty institution/membership baseline; stopped.")
        institution_name = (values.get("INSTITUTION_NAME") or "").strip()
        if not institution_name:
            raise SetupError("Set INSTITUTION_NAME locally before bootstrap.")
        connection.execute("INSERT INTO public.institutions (id,name,timezone,demo) VALUES (%s,%s,%s,false)",
                           (tenant, institution_name, "Asia/Kolkata"))
        connection.execute("""INSERT INTO public.memberships
            (id,tenant_id,user_id,role,name,department,semester,section,interests,diet,active,student_number)
            VALUES (%s,%s,%s,'institution_admin','CampusOS administrator','All',1,'All','','any',true,NULL)""",
                           (str(uuid4()), tenant, subject))
        connection.execute("""INSERT INTO public.audit_logs (id,tenant_id,user_id,action,details,created_at)
            VALUES (%s,%s,%s,'operator_admin_bootstrap',%s::json,now())""",
                           (str(uuid4()), tenant, subject, json.dumps(dict(verified_google=True, operator_approved=True))))
        connection.commit()
    for path, content in prepared.items():
        path.write_text(content, encoding="utf-8")
    report = dict(project_ref=TARGET, verified_google_identity=True, exact_configured_email_matched=True,
                  institution_id=tenant, administrator_memberships_created=1, auth_accounts_modified=False,
                  campus_records_seeded=False, credentials_printed=False, backend_restart_required=True,
                  configuration_backup=str(directory.relative_to(ROOT)))
    (ROOT / "artifacts/campusos-kt-admin-bootstrap.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except SetupError as exc:
        print(str(exc))
        raise SystemExit(2)
    except Exception as exc:
        print("Administrator bootstrap stopped: " + type(exc).__name__ + ". Details withheld; inspect state before retrying.")
        raise SystemExit(2)
