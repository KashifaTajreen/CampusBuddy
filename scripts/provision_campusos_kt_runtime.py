"""Provision only a restricted backend login; never assign application admin roles."""

import json
import secrets
import shutil
import time

from dotenv import dotenv_values
import psycopg
from psycopg import sql

from scripts.configure_campusos_kt import ROOT, INPUT, validate, validate_database, connection_parameters, replace_fields, SetupError

TARGET='rgqrkgyzxgiwlbuhyohh'
ROLE='campusos_kt_api'


def verify_runtime(url):
    params=connection_parameters(url,TARGET)
    with psycopg.connect(**params) as c:
        c.read_only=True
        row=c.execute('SELECT current_user,rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user').fetchone()
        if row!=(ROLE,False,False,False,False):
            raise SetupError('Runtime role identity or restricted privileges do not match.')
        if not c.execute("SELECT pg_has_role(current_user,'campus_api','MEMBER')").fetchone()[0]:
            raise SetupError('Runtime role lacks the reviewed API group.')
        if c.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner=current_user").fetchone()[0]:
            raise SetupError('Runtime must not own application tables.')
        for statement in ('SELECT student_records FROM public.sources LIMIT 0',
                          'SELECT student_number FROM public.memberships LIMIT 0',
                          'SELECT row_data FROM public.document_chunks LIMIT 0'):
            c.execute(statement)
        c.rollback()


def main():
    values=dotenv_values(INPUT,interpolate=False)
    if validate(values,database=True)!=TARGET:
        raise SetupError('Approved new-project mismatch.')
    completed=json.loads((ROOT/'artifacts/campusos-kt-approved-migration.json').read_text())
    if not completed.get('migration_completed') or completed.get('project_ref')!=TARGET:
        raise SetupError('Verified approved migration is required before runtime setup.')
    directory=ROOT/'.scratch/campusos-kt'/('runtime-config-'+str(time.time_ns()))
    directory.mkdir(parents=True,exist_ok=False)
    for path,name in [(INPUT,'operator-input.env.before'),(ROOT/'.env','backend.env.before')]:
        shutil.copyfile(path,directory/name)
    runtime=values.get('API_DATABASE_URL')
    created=False
    if not runtime:
        params=connection_parameters(values['MIGRATION_DATABASE_URL'],TARGET)
        params['options']='-c lock_timeout=5000 -c statement_timeout=15000'
        with psycopg.connect(**params) as c:
            c.read_only=False
            if c.execute('SELECT version_num FROM public.alembic_version').fetchone()!=('0008',):
                raise SetupError('Remote revision changed; stop.')
            if c.execute('SELECT count(*) FROM pg_roles WHERE rolname=%s',(ROLE,)).fetchone()[0]:
                raise SetupError('Runtime role exists but no local credential is supplied; no password was reset.')
            password=secrets.token_urlsafe(32)
            owner=validate_database(values['MIGRATION_DATABASE_URL'],TARGET)
            user=ROLE+'.'+TARGET if owner.host.endswith('.pooler.supabase.com') else ROLE
            runtime=owner.set(drivername='postgresql+psycopg',username=user,password=password).update_query_dict(
                dict(sslmode=owner.query.get('sslmode','require'))).render_as_string(hide_password=False)
            # Recovery copy is ignored/local; no password is emitted into SQL/logs.
            (directory/'runtime.env').write_text('API_DATABASE_URL='+json.dumps(runtime)+'\n',encoding='utf-8')
            verifier=c.pgconn.encrypt_password(password.encode(),ROLE.encode(),b'scram-sha-256').decode()
            c.execute(sql.SQL('CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD {}').format(
                sql.Identifier(ROLE),sql.Literal(verifier)))
            c.execute(sql.SQL('GRANT campus_api TO {}').format(sql.Identifier(ROLE)))
            c.execute(sql.SQL('GRANT CONNECT ON DATABASE postgres TO {}').format(sql.Identifier(ROLE)))
            c.execute(sql.SQL('ALTER ROLE {} SET search_path=pg_catalog,public,extensions').format(sql.Identifier(ROLE)))
            c.commit()
            created=True
        INPUT.write_text(replace_fields(INPUT,dict(API_DATABASE_URL=runtime)),encoding='utf-8')
    verify_runtime(runtime)
    changes=dict(DATABASE_URL=runtime,FRONTEND_ORIGIN='http://localhost:3001',STORAGE_BACKEND='local')
    if (ROOT/'.scratch/semantic-e5/manifest.json').is_file():
        changes.update(EMBEDDING_PROVIDER='onnx',EMBEDDING_MODEL_PATH=str(ROOT/'.scratch/semantic-e5'))
    (ROOT/'.env').write_text(replace_fields(ROOT/'.env',changes),encoding='utf-8')
    report=dict(project_ref=TARGET,runtime_role=ROLE,role_created=created,verified_non_owner_nobypassrls=True,
                api_readiness_queries_passed=True,admin_memberships_created=False,seed_data_created=False,
                credentials_printed=False,configuration_backup=str(directory.relative_to(ROOT)))
    (ROOT/'artifacts/campusos-kt-runtime.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    try:
        main()
    except SetupError as exc:
        print(str(exc))
        raise SystemExit(2)
    except Exception as exc:
        print('Runtime setup stopped: '+type(exc).__name__+'. Details withheld; inspect state before retrying.')
        raise SystemExit(2)
