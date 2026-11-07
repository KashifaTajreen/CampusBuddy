"""Reviewed new-project migration gate. Default/check/verify modes are read-only.

Execution flags are not permission: an operator must explicitly approve the plan
before anyone runs --execute. Credentials never appear in command arguments.
"""

import argparse
import hashlib
import json
import os

from dotenv import dotenv_values

from scripts.configure_campusos_kt import ROOT, INPUT, validate, validate_database, connection_parameters, SetupError

TARGET = 'rgqrkgyzxgiwlbuhyohh'
MANIFEST = ROOT / 'artifacts/campusos-kt-migration-approval.json'
TABLES = ('institutions','memberships','sources','campus_records','documents','document_chunks',
          'conversations','messages','ingestion_jobs','audit_logs','query_traces',
          'integration_connections','rate_buckets','section_codes')


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def approved_sources():
    data = json.loads(MANIFEST.read_text())
    payload = data['plan']
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',',':')).encode()).hexdigest()
    if digest != data['review_sha256'] or payload['project_ref'] != TARGET or payload['revision'] != '0008':
        raise SetupError('Approval manifest identity/hash mismatch; stop for review.')
    for name, expected in payload['source_hashes'].items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT.resolve()) or checksum(path) != expected:
            raise SetupError('Reviewed source changed; regenerate and review the plan before execution.')
    archive = (ROOT / payload['backup']).resolve()
    if not archive.is_relative_to((ROOT/'.scratch/campusos-kt').resolve()) or checksum(archive) != payload['backup_sha256']:
        raise SetupError('Reviewed local backup hash mismatch; migration refused.')
    rehearsal = json.loads((ROOT/'artifacts/campusos-kt-local-rehearsal.json').read_text())
    if rehearsal.get('status') != 'passed' or not rehearsal.get('original_unchanged'):
        raise SetupError('A passing owned local rehearsal is required.')
    return data


def execution_permission(args, review):
    if args.execute and (args.approved_project != TARGET or args.review_sha256 != review['review_sha256']):
        raise SetupError('Execution requires the exact approved project and reviewed plan SHA256.')


def inspect_target(values, after=False):
    import psycopg
    if validate_database(values['MIGRATION_DATABASE_URL'],TARGET).database != 'postgres':
        raise SetupError('The reviewed target database is postgres; another database is refused.')
    report = dict(project_ref=TARGET, read_only=True)
    with psycopg.connect(**connection_parameters(values['MIGRATION_DATABASE_URL'],TARGET)) as c:
        c.read_only=True
        with c.cursor() as q:
            q.execute("SELECT current_setting('transaction_read_only'),current_setting('server_version')")
            read_only, version = q.fetchone()
            if read_only!='on' or version.split('.')[0]!='17':
                raise SetupError('Read-only protection or reviewed PostgreSQL major version mismatch.')
            q.execute("SELECT to_regclass('public.alembic_version')")
            exists = q.fetchone()[0]
            revisions=[]
            if exists:
                q.execute('SELECT version_num FROM public.alembic_version')
                revisions=[r[0] for r in q.fetchall()]
            q.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            tables=[r[0] for r in q.fetchall()]
            report.update(server_version=version,revisions=revisions,public_tables=tables)
            counts={}
            for schema,table in [('auth','users'),('auth','identities'),('storage','buckets'),('storage','objects')]:
                q.execute('SELECT count(*) FROM '+schema+'.'+table)
                counts[schema+'.'+table]=q.fetchone()[0]
            report['managed_user_data_counts']=counts
            if not after:
                if tables or revisions or any(counts.values()):
                    raise SetupError('Database differs from the reviewed empty baseline; inspect and refresh backup/approval.')
                q.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f')")
                if q.fetchone()[0]:
                    raise SetupError('Unexpected public relation; migration requires a refreshed review.')
                q.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND left(p.proname,7)='campus_'")
                if q.fetchone()[0]:
                    raise SetupError('Unexpected CampusOS function; migration requires a refreshed review.')
                q.execute("SELECT count(*) FROM pg_roles WHERE rolname='campus_api'")
                if q.fetchone()[0]:
                    raise SetupError('An unexpected existing API group requires review; no role changed.')
            else:
                if revisions!=['0008'] or set(tables)!=set(TABLES)|{'alembic_version'}:
                    raise SetupError('Post-migration schema/revision mismatch; do not start the API.')
                q.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(%s) AND c.relrowsecurity AND c.relforcerowsecurity",(list(TABLES),))
                if q.fetchone()[0]!=len(TABLES):
                    raise SetupError('Expected application RLS controls are incomplete.')
                q.execute("SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolcanlogin FROM pg_roles WHERE rolname='campus_api'")
                if q.fetchone()!=(False,False,False,False,False):
                    raise SetupError('API group has unexpected privileges.')
                for role in ('anon','authenticated'):
                    for table in (*TABLES,'alembic_version'):
                        q.execute("SELECT has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR has_any_column_privilege(%s,%s,'SELECT,INSERT,UPDATE,REFERENCES')",(role,'public.'+table,role,'public.'+table))
                        if q.fetchone()[0]:
                            raise SetupError('Public client table privileges remain; do not start the API.')
                    q.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND left(p.proname,7)='campus_' AND has_function_privilege(%s,p.oid,'EXECUTE')",(role,))
                    if q.fetchone()[0]:
                        raise SetupError('Public client function privileges remain; do not start the API.')
                q.execute("SELECT has_column_privilege('campus_api','public.section_codes','code_hash','SELECT') OR has_column_privilege('campus_api','public.section_codes','salt','SELECT') OR has_column_privilege('campus_api','public.memberships','role','UPDATE') OR has_table_privilege('campus_api','public.memberships','INSERT')")
                if q.fetchone()[0]:
                    raise SetupError('Runtime has unintended secret or membership-write privileges.')
                report['reviewed_privilege_checks_passed']=True
        c.rollback()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--check',action='store_true')
    mode.add_argument('--verify',action='store_true')
    mode.add_argument('--execute',action='store_true')
    parser.add_argument('--approved-project')
    parser.add_argument('--review-sha256')
    args=parser.parse_args()
    attempted=False
    try:
        review=approved_sources()
        execution_permission(args,review)
        values=dotenv_values(INPUT,interpolate=False)
        if validate(values,database=True)!=TARGET:
            raise SetupError('Approved new-project reference mismatch.')
        report=inspect_target(values,after=args.verify)
        report['review_sha256']=review['review_sha256']
        report['database_writes']=False
        if args.execute:
            before_counts=report['managed_user_data_counts']
            # Private process environment override only; never alter the original
            # SQLite DATABASE_URL in .env and never use it as a migration target.
            url=validate_database(values['MIGRATION_DATABASE_URL'],TARGET).set(drivername='postgresql+psycopg')
            url=url.update_query_dict(dict(sslmode=url.query.get('sslmode','require'),connect_timeout='5',
                options='-c lock_timeout=5000 -c statement_timeout=120000',application_name='campusos_kt_approved_migration'))
            os.environ['DATABASE_URL']=url.render_as_string(hide_password=False)
            from backend.app.config import settings
            settings.cache_clear()
            from alembic.config import Config
            from alembic import command
            attempted=True
            command.upgrade(Config(str(ROOT/'alembic.ini')),'0008')
            report=inspect_target(values,after=True)
            if report['managed_user_data_counts']!=before_counts:
                raise SetupError('Managed data counts changed during execution. The migration may have committed; investigate before startup.')
            report.update(database_writes=True,migration_completed=True,review_sha256=review['review_sha256'])
            (ROOT/'artifacts/campusos-kt-approved-migration.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
        return 0
    except SetupError as exc:
        print(str(exc))
    except Exception as exc:
        print(type(exc).__name__+': details withheld. '+
              ('Execution was attempted; verify database revision before retry.' if attempted else 'No migration was attempted.'))
    return 2


if __name__=='__main__':
    raise SystemExit(main())
