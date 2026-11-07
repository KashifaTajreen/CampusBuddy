"""Prepare/review or explicitly execute only the approved CampusOS-KT 0009 update."""
import argparse
import hashlib
import importlib
import json

from dotenv import dotenv_values
import psycopg
from sqlalchemy import create_engine, text

from scripts.configure_campusos_kt import INPUT, ROOT, SetupError, validate, connection_parameters

TARGET='rgqrkgyzxgiwlbuhyohh'
MANIFEST=ROOT/'artifacts/campusos-kt-0009-review.json'


def digest(content):
    return hashlib.sha256(content).hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--review-sha256')
    args=parser.parse_args()
    values=dotenv_values(INPUT,interpolate=False)
    if validate(values,database=True)!=TARGET:
        raise SetupError('Project mismatch; stopped.')
    params=connection_parameters(values['MIGRATION_DATABASE_URL'],TARGET)
    params['connect_timeout']=15
    engine=create_engine('postgresql+psycopg://',creator=lambda:psycopg.connect(**params))
    module=importlib.import_module('backend.migrations.versions.0009_admin_review')
    with engine.connect() as connection:
        if connection.scalar(text('SELECT version_num FROM public.alembic_version'))!='0008':
            raise SetupError('Expected revision 0008; stopped without migration.')
        before=[connection.scalar(text('SELECT pg_get_functiondef(to_regprocedure(:signature))'),{'signature':s})
                for s in ('campus_source_transition(text,text,text,text)','campus_file_action(text,text,text,text)')]
        statements=module.reviewed_definitions(connection)+[module.DEMO_SQL]
        connection.rollback()
    engine.dispose()  # Release the inspection pool before opening the write connection.
    sql=';\n'.join(statements)+';\n'
    revision_file=ROOT/'backend/migrations/versions/0009_admin_review.py'
    plan=dict(project_ref=TARGET,from_revision='0008',to_revision='0009',
              migration_sha256=digest(revision_file.read_bytes()),sql_sha256=digest(sql.encode()),
              original_function_sha256=[digest(s.encode()) for s in before])
    sha=digest(json.dumps(plan,sort_keys=True).encode())
    if not args.execute:
        (ROOT/'artifacts/campusos-kt-0009-reviewed.sql').write_text(sql,encoding='utf-8')
        MANIFEST.write_text(json.dumps(dict(plan=plan,review_sha256=sha),indent=2)+'\n')
        print(json.dumps(dict(review_sha256=sha,revision='0009',database_modified=False,
              tables_altered=False,grants_to_admins=False,changes=['Same scoped administrator may review and publish completed imports.',
                'Atomic draft-only deletion guard; published sources refused.','Scoped one-way Demo Data labeling for drafts.']),indent=2))
        return
    review=json.loads(MANIFEST.read_text())
    if args.review_sha256!=sha or review['review_sha256']!=sha or review['plan']!=plan:
        raise SetupError('Approved source/function hash changed; stopped.')
    rehearsal=json.loads((ROOT/'artifacts/import-policy-rehearsal.json').read_text())
    if not rehearsal.get('passed') or rehearsal.get('revision')!='0009':
        raise SetupError('A passing disposable 0009 policy rehearsal is required.')
    backup=json.loads((ROOT/'artifacts/campusos-kt-new-backup.json').read_text())
    archive=(ROOT/backup['backup']).resolve()
    if (not archive.is_relative_to((ROOT/'.scratch/campusos-kt').resolve()) or
        digest(archive.read_bytes())!=backup['sha256'] or not backup['archive_list_verified']):
        raise SetupError('Verified new-project backup is required; stopped.')
    params['options']='-c lock_timeout=5000 -c statement_timeout=30000'
    with psycopg.connect(**params) as c:
        c.read_only=False
        c.execute('LOCK TABLE public.alembic_version IN EXCLUSIVE MODE')
        if c.execute('SELECT version_num FROM public.alembic_version').fetchone()!=('0008',):
            raise SetupError('Database revision changed; stopped.')
        signatures=('campus_source_transition(text,text,text,text)','campus_file_action(text,text,text,text)')
        actual=[digest(c.execute('SELECT pg_get_functiondef(to_regprocedure(%s))',(s,)).fetchone()[0].encode()) for s in signatures]
        if actual!=plan['original_function_sha256']:
            raise SetupError('Security function baseline changed; stopped.')
        for statement in statements:
            c.execute(statement)
        c.execute("UPDATE public.alembic_version SET version_num='0009' WHERE version_num='0008'")
        if c.execute('SELECT version_num FROM public.alembic_version').fetchone()!=('0009',):
            raise SetupError('Revision verification failed; rolled back.')
        c.commit()
    report=dict(project_ref=TARGET,revision='0009',review_sha256=sha,migration_completed=True,
                backup=backup['backup'],data_seeded=False,admin_memberships_created=False)
    (ROOT/'artifacts/campusos-kt-0009-execution.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    try:
        main()
    except SetupError as exc:
        print(str(exc))
        raise SystemExit(2)
    except Exception as exc:
        print('Migration preparation/execution stopped: '+type(exc).__name__+'; details withheld.')
        raise SystemExit(2)
