"""Child of the owned local rehearsal. Only generated loopback credentials."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
T = '10000000-0000-4000-8000-000000000001'
ADMIN = '10000000-0000-4000-8000-000000000002'
STUDENT = '10000000-0000-4000-8000-000000000003'
LEGACY = '10000000-0000-4000-8000-000000000010'


def scenario(work):
    state = json.loads((work/'private-state.json').read_text())
    port, database = state['port'],state['database']
    assert database == 'campus_verify' and 1024<port<65536
    owner_url = f"postgresql+psycopg://campus_verify:{state['owner_password']}@127.0.0.1:{port}/{database}"
    api_url = f"postgresql+psycopg://kt_api_test:{state['api_password']}@127.0.0.1:{port}/{database}"
    os.environ.update(DATABASE_URL=owner_url, SUPABASE_URL='',SUPABASE_PUBLISHABLE_KEY='',
                      SUPABASE_SERVICE_ROLE_KEY='',GOOGLE_CLIENT_ID='',GOOGLE_CLIENT_SECRET='',
                      LLM_ENABLED='false',LLM_PROVIDER='none',STORAGE_BACKEND='local',REDIS_ENABLED='false')
    state.update(owner_url=owner_url, api_url=api_url)
    (work/'private-state.json').write_text(json.dumps(state))
    report = dict(status='running',stage='local_bootstrap',supabase_accessed=False)
    def save():
        (work/'scenario.json').write_text(json.dumps(report,indent=2)+'\n')
    save()
    try:
        import psycopg
        from psycopg import sql
        import sqlalchemy as sa
        from sqlalchemy.orm import Session
        from alembic.config import Config
        from alembic import command
        from backend.app.config import settings
        settings.cache_clear()
        from backend.app.models import Institution, Membership, SectionCode
        from backend.app.auth import hash_section_code
        with psycopg.connect(host='127.0.0.1',port=port,dbname=database,user='kt_bootstrap',
                             password=state['bootstrap_password'],autocommit=True) as c:
            # Supabase-like migrator: NOT superuser; can manage roles and bypass RLS.
            c.execute(sql.SQL('CREATE ROLE campus_verify LOGIN NOSUPERUSER BYPASSRLS CREATEROLE NOCREATEDB PASSWORD {}').format(sql.Literal(state['owner_password'])))
            c.execute(sql.SQL('ALTER DATABASE {} OWNER TO campus_verify').format(sql.Identifier(database)))
            for role in ('anon','authenticated','service_role'):
                c.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
            # A stock vector extension requires bootstrap privileges, unlike the
            # managed Supabase extension installer. This limitation is reported.
            c.execute('CREATE EXTENSION vector')
        engine = sa.create_engine(owner_url)
        with engine.begin() as c:
            for kind in ('TABLES','FUNCTIONS','SEQUENCES'):
                c.execute(sa.text(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON {kind} TO anon,authenticated,service_role'))
        trace = []
        real_create = sa.create_engine
        def observe(statement, params):
            trace.append(dict(sql=statement,parameters=params))
        def instrumented(*args,**kwargs):
            e = real_create(*args,**kwargs)
            sa.event.listen(e,'before_cursor_execute',lambda conn,cursor,stmt,params,ctx,many:observe(stmt,params))
            return e
        cfg = Config(str(ROOT/'alembic.ini'))
        report['stage']='local_0001_through_0007'
        save()
        with patch.object(sa,'create_engine',instrumented):
            command.upgrade(cfg,'0007')
        with Session(engine) as db:
            db.add(Institution(id=T,name='Local synthetic security fixture',demo=True))
            db.flush()
            members=[(ADMIN,'institution_admin','AIML',3,'A',True),
                     (STUDENT,'student','AIML',3,'A',True),
                     ('10000000-0000-4000-8000-000000000005','student','AIML',3,'A',False),
                     ('10000000-0000-4000-8000-000000000006','department_admin','AIML',3,'A',True),
                     ('10000000-0000-4000-8000-000000000007','section_admin','AIML',3,'A',True),
                     ('10000000-0000-4000-8000-000000000008','canteen_manager','All',3,'A',True)]
            for user,role,dept,sem,section,active in members:
                db.add(Membership(tenant_id=T,user_id=user,role=role,department=dept,
                                  semester=sem,section=section,active=active,name='Synthetic fixture'))
            salt='1'*32
            db.add(SectionCode(id=LEGACY,tenant_id=T,department='AIML',semester=3,section='A',
                               code_hash=hash_section_code('AIML3A-ABC123',salt),salt=salt,created_by=ADMIN,
                               expires_at=datetime.now(timezone.utc)+timedelta(days=1)))
            db.commit()
        def fingerprint():
            from backend.app.models import Base
            with engine.connect() as c:
                values={t.name:[dict(r) for r in c.execute(sa.select(t).order_by(*t.primary_key.columns)).mappings()]
                        for t in Base.metadata.sorted_tables}
                return hashlib.sha256(json.dumps(values,sort_keys=True,default=str).encode()).hexdigest()
        before=fingerprint()
        report['stage']='local_0008_upgrade_preservation'
        save()
        with patch.object(sa,'create_engine',instrumented):
            command.upgrade(cfg,'0008')
        report['preexisting_rows_preserved']=fingerprint()==before
        assert report['preexisting_rows_preserved']
        with psycopg.connect(host='127.0.0.1',port=port,dbname=database,user='kt_bootstrap',
                             password=state['bootstrap_password'],autocommit=True) as c:
            c.execute(sql.SQL('CREATE ROLE kt_api_test LOGIN INHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Literal(state['api_password'])))
            c.execute('GRANT campus_api TO kt_api_test')
            # Legacy RLS regressions explicitly SET ROLE; PostgreSQL 17 does not
            # give a role creator SET automatically. Local test operator only.
            c.execute('GRANT campus_api TO campus_verify WITH SET TRUE')
        (ROOT/'artifacts/campusos-kt-local-migration-sql-trace.json').write_text(json.dumps(trace,indent=2,default=str)+'\n')
        report['migration_statements_recorded']=len(trace)
        report['migration_version']='0008'
        report['stock_local_vector_extension_preinstalled_by_bootstrap']=True
        report['legacy_test_operator_set_role_enabled_locally']=True
        report['stage']='real_local_postgres_security_tests'
        save()
        env={**os.environ,'TEST_POSTGRES_URL':owner_url,'CAMPUS_KT_REHEARSAL_STATE':str(work/'private-state.json')}
        tests=subprocess.run([sys.executable,'-m','pytest','backend/tests/test_section_postgres_security.py',
                              'backend/tests/test_postgres.py','-q','--junitxml=artifacts/campusos-kt-postgres-rehearsal-tests.xml'],
                             cwd=ROOT,env=env,capture_output=True,text=True,timeout=300)
        (work/'pytest.log').write_text(tests.stdout+'\n'+tests.stderr,encoding='utf-8')
        import xml.etree.ElementTree as ET
        suites=list(ET.parse(ROOT/'artifacts/campusos-kt-postgres-rehearsal-tests.xml').getroot().iter('testsuite'))
        report['tests']={k:sum(int(s.attrib[k]) for s in suites) for k in ('tests','failures','errors','skipped')}
        report['status']='passed' if tests.returncode==0 else 'failed'
        report['stage']='completed'
        engine.dispose()
    except Exception as exc:
        report.update(status='failed',error_class=type(exc).__name__,
                      error_frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name)
                                    for f in traceback.extract_tb(exc.__traceback__)[-4:]])
        # Private local diagnostics only; never print connection strings.
        (work/'private-failure.txt').write_text(str(exc),encoding='utf-8')
    finally:
        save()
    return 0 if report['status']=='passed' else 2


if __name__=='__main__':
    target=Path(sys.argv[1]).resolve()
    assert target.is_relative_to((ROOT/'.scratch').resolve()) and target.name.startswith('campusos-kt-rehearsal-')
    raise SystemExit(scenario(target))
