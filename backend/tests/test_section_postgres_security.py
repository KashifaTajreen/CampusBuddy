"""Run only in the explicitly owned, generated local KT rehearsal database."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy.engine import make_url

from backend.app.auth import generate_section_code
from scripts.kt_rehearsal_scenario import T, ADMIN, STUDENT, LEGACY

INACTIVE='10000000-0000-4000-8000-000000000005'
DEPARTMENT='10000000-0000-4000-8000-000000000006'
SECTION='10000000-0000-4000-8000-000000000007'
CANTEEN='10000000-0000-4000-8000-000000000008'


class Target:
    def __init__(self, state):
        self.state=state

    def __repr__(self):
        return '<owned local PostgreSQL target; credentials withheld>'

    def connect(self, kind='api'):
        u=make_url(self.state[kind+'_url'])
        return psycopg.connect(host=u.host,port=u.port,dbname=u.database,user=u.username,password=u.password,
                               options='-c statement_timeout=5000 -c lock_timeout=3000')

    def bootstrap(self):
        return psycopg.connect(host='127.0.0.1',port=self.state['port'],dbname=self.state['database'],
                               user='kt_bootstrap',password=self.state['bootstrap_password'])


@pytest.fixture(scope='module')
def target():
    name=os.getenv('CAMPUS_KT_REHEARSAL_STATE')
    if not name:
        pytest.skip('KT PostgreSQL security checks require the owned disposable local rehearsal.')
    path=Path(name).resolve()
    assert path.is_relative_to((Path(__file__).resolve().parents[2]/'.scratch').resolve())
    assert path.parent.name.startswith('campusos-kt-rehearsal-')
    state=json.loads(path.read_text())
    for kind in ('owner','api'):
        url=make_url(state[kind+'_url'])
        assert url.host=='127.0.0.1' and url.port==state['port']
        assert url.database == 'campus_verify'
    return Target(state)


@pytest.fixture
def api(target):
    c=target.connect()
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def actor(c, user=ADMIN, tenant=T):
    c.execute("SELECT set_config('app.user_id',%s,true),set_config('app.tenant_id',%s,true)",(user,tenant))


def join(c, user=None, department='AIML', section='A', code='AIML3A-ABC123', tenant=T):
    actor(c,user or str(uuid4()))
    return c.execute('SELECT campus_section_join(%s,%s,3,%s,%s)',(tenant,department,section,code)).fetchone()[0]


def create(c, department='AIML', section='A', user=ADMIN):
    actor(c,user)
    raw,digest,salt=generate_section_code(department,3,section)
    result=c.execute('SELECT campus_section_create(%s,%s,3,%s,%s,%s,7)',
                     (T,department,section,digest,salt)).fetchone()[0]
    return raw,result


def test_runtime_is_non_owner_and_cannot_bypass_rls(api):
    assert api.execute('SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb FROM pg_roles WHERE rolname=current_user').fetchone()==(False,False,False,False)
    assert api.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tableowner=current_user").fetchone()[0]==0


def test_legacy_code_joins_only_as_student_and_updates_context(api):
    who=str(uuid4())
    result=join(api,who)
    assert result==dict(success=True,role='student',department='AIML',semester=3,section='A')
    assert api.execute('SELECT role,active FROM memberships WHERE user_id=%s',(who,)).fetchone()==('student',True)


@pytest.mark.parametrize('arguments', [dict(code='WRONG-CODE'),dict(section='B'),dict(department='CSE')])
def test_wrong_code_or_scope_is_denied(api,arguments):
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        join(api,**arguments)


def test_cross_tenant_argument_is_denied(api):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        join(api,tenant='20000000-0000-4000-8000-000000000001')


@pytest.mark.parametrize('user',[ADMIN,INACTIVE])
def test_existing_admin_or_inactive_membership_cannot_be_overwritten(api,user):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        join(api,user)


def test_no_actor_context_is_denied(api):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute('SELECT campus_section_join(%s,%s,3,%s,%s)',(T,'AIML','A','AIML3A-ABC123'))


def test_student_cannot_read_code_metadata(api):
    actor(api,STUDENT)
    assert api.execute('SELECT id FROM section_codes').fetchall()==[]


def test_even_admin_cannot_select_code_hash_or_salt(api):
    actor(api)
    assert api.execute('SELECT id FROM section_codes WHERE id=%s',(LEGACY,)).fetchone()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute('SELECT code_hash,salt FROM section_codes')


@pytest.mark.parametrize('statement', [
    "INSERT INTO memberships(id,tenant_id,user_id,role) VALUES('x','x','x','institution_admin')",
    "UPDATE memberships SET role='institution_admin'",
    "UPDATE section_codes SET revoked=false",
    "DELETE FROM section_codes",
    "CREATE TABLE public.unauthorized_ddl(id integer)",
])
def test_runtime_has_no_direct_privileged_writes(api,statement):
    actor(api)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute(statement)


@pytest.mark.parametrize('user',[STUDENT,CANTEEN])
def test_student_and_canteen_cannot_create_or_revoke_codes(api,user):
    actor(api,user)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        api.execute('SELECT campus_section_revoke(%s,%s)',(T,LEGACY))


def test_department_admin_scope_and_section_admin_scope(api):
    create(api,user=DEPARTMENT)
    create(api,user=SECTION)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        create(api,section='B',user=SECTION)


def test_department_admin_cannot_create_other_department(api):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        create(api,department='CSE',user=DEPARTMENT)


@pytest.mark.parametrize('role',['anon','authenticated'])
@pytest.mark.parametrize('statement',[
    'SELECT * FROM public.memberships','SELECT * FROM public.section_codes',
    'SELECT * FROM public.rate_buckets','SELECT * FROM public.alembic_version',
    "SELECT public.campus_section_join('x','AIML',3,'A','wrong')",
])
def test_public_client_cannot_access_backend_objects_even_with_spoofed_context(target,role,statement):
    with target.bootstrap() as c:
        c.execute('SET LOCAL ROLE '+role)
        actor(c)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(statement)
        c.rollback()


def test_created_code_rotation_and_revocation(api):
    raw,first=create(api)
    newer,second=create(api)
    assert first['id']!=second['id'] and raw!=newer
    assert api.execute('SELECT revoked FROM section_codes WHERE id=%s',(first['id'],)).fetchone()[0]
    result=api.execute('SELECT campus_section_revoke(%s,%s)',(T,second['id'])).fetchone()[0]
    assert result=={'revoked':True}
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        join(api,code=newer)


def test_expired_code_is_denied(target,api):
    with target.connect('owner') as owner:
        original=owner.execute('SELECT expires_at FROM section_codes WHERE id=%s',(LEGACY,)).fetchone()[0]
        owner.execute("UPDATE section_codes SET expires_at=now()-interval '1 day' WHERE id=%s",(LEGACY,))
        owner.commit()
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            join(api)
    finally:
        with target.connect('owner') as owner:
            owner.execute('UPDATE section_codes SET expires_at=%s WHERE id=%s',(original,LEGACY))


def test_revoked_creator_is_denied(target,api):
    with target.connect('owner') as owner:
        owner.execute('UPDATE memberships SET active=false WHERE tenant_id=%s AND user_id=%s',(T,ADMIN))
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            join(api)
    finally:
        with target.connect('owner') as owner:
            owner.execute('UPDATE memberships SET active=true WHERE tenant_id=%s AND user_id=%s',(T,ADMIN))


def test_rate_counter_is_backend_only_and_works_before_membership(api):
    actor(api,str(uuid4()))
    api.execute("INSERT INTO rate_buckets(key,\"window\",count) VALUES('synthetic-new-user',1,1)")
    assert api.execute("SELECT count FROM rate_buckets WHERE key='synthetic-new-user'").fetchone()[0]==1


def test_concurrent_student_joins_create_one_student_row(target):
    who=str(uuid4())
    barrier=threading.Barrier(2)
    def work():
        with target.connect() as c:
            barrier.wait(timeout=5)
            return join(c,who)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:work(),range(2)))
    assert all(r['role']=='student' for r in results)
    with target.connect('owner') as owner:
        assert owner.execute('SELECT count(*) FROM memberships WHERE tenant_id=%s AND user_id=%s',(T,who)).fetchone()[0]==1


def test_concurrent_rotations_leave_only_one_active_code(target):
    department='FIX-'+uuid4().hex[:10]
    barrier=threading.Barrier(2)
    def work():
        with target.connect() as c:
            barrier.wait(timeout=5)
            return create(c,department=department)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:work(),range(2)))
    assert results[0][1]['id']!=results[1][1]['id']
    with target.connect('owner') as owner:
        assert owner.execute('SELECT count(*) FROM section_codes WHERE tenant_id=%s AND department=%s AND NOT revoked',(T,department)).fetchone()[0]==1


def test_join_cannot_race_a_creator_revocation(target):
    owner=target.connect('owner')
    owner.execute('UPDATE memberships SET active=false WHERE tenant_id=%s AND user_id=%s',(T,ADMIN))
    started=threading.Event()
    def work():
        with target.connect() as c:
            started.set()
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                join(c)
            c.rollback()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(work)
            assert started.wait(5)
            time.sleep(.15)
            owner.commit()
            future.result(timeout=10)
    finally:
        owner.rollback()
        owner.execute('UPDATE memberships SET active=true WHERE tenant_id=%s AND user_id=%s',(T,ADMIN))
        owner.commit()
        owner.close()
