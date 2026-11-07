import json
import os

import pytest
from sqlalchemy import create_engine,event,select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from backend.app.auth import resolve_actor
from backend.app.ingestion import Ingestion,work_once
from backend.app.models import Institution,Membership,Source,IngestionJob
from backend.app.schemas import ImportOptions
from backend.app.services import CampusService
from backend.tests.conftest import MemoryStore


def test_timetable_self_publication_partial_replacement_and_revocation():
    url=os.environ.get('TEST_TIMETABLE_POSTGRES_URL')
    if not url:
        pytest.skip('Requires the disposable loopback timetable PostgreSQL rehearsal database.')
    parsed=make_url(url)
    assert parsed.host=='127.0.0.1' and parsed.database=='campus_timetable_rehearsal'
    owner=create_engine(url)
    api=create_engine(url)
    @event.listens_for(api,'connect')
    def restricted(connection,_):
        connection.cursor().execute('SET ROLE campus_api')
    factory=sessionmaker(owner,expire_on_commit=False)
    api_factory=sessionmaker(api,expire_on_commit=False)
    tenant='00000000-0000-4000-8000-000000001101'
    admin='00000000-0000-4000-8000-000000001102'
    student='00000000-0000-4000-8000-000000001103'
    from sqlalchemy import text
    @event.listens_for(api_factory,'after_begin')
    def identity(session,transaction,connection):
        connection.execute(text("SELECT set_config('app.user_id',:user,true),set_config('app.tenant_id',:tenant,true)"),
                           dict(user=session.info.get('user',admin),tenant=tenant))
    with factory() as db:
        db.add(Institution(id=tenant,name='Disposable timetable test'))
        db.flush()
        db.add_all([Membership(tenant_id=tenant,user_id=admin,role='institution_admin',department='AIML',semester=3,section='A'),
                    Membership(tenant_id=tenant,user_id=student,role='student',department='AIML',semester=3,section='A')])
        db.commit()
    store=MemoryStore()
    row=dict(day=0,start_time='09:00',end_time='10:00',subject='Verified timetable class')
    def queue(name,rows,replacement=None,partial=False):
        with api_factory() as db:
            c=CampusService(db,resolve_actor(db,admin,tenant))
            pipeline=Ingestion(c,store)
            job=pipeline.receive(name,json.dumps(rows).encode(),ImportOptions(kind='timetable',department='AIML',semester=3,section='A',replaces_source_id=replacement))
            pipeline.preview(job,timetable_ready_only=partial)
            pipeline.confirm(job)
            job.options={**job.options,'timetable_publish_requested':True}
            db.commit()
            return job.id,job.source_id
    first_job,first=queue('partial.json',[row,{**row,'subject':'Held class','start_time':''}],partial=True)
    assert work_once(factory,store)
    with factory() as db:
        assert db.get(Source,first).publication_status=='published'
        assert db.get(Source,first).approved_by==db.get(Source,first).published_by==admin
        assert db.get(IngestionJob,first_job).preview['published_count']==1
    with api_factory(info={'user':student}) as db:
        c=CampusService(db,resolve_actor(db,student,tenant))
        assert [r['subject'] for r in c.timetable('2026-11-09')]==['Verified timetable class']
    second_job,second=queue('replacement.json',[{**row,'subject':'Reviewed replacement'}],first)
    with api_factory(info={'user':student}) as db:
        c=CampusService(db,resolve_actor(db,student,tenant))
        assert c.timetable('2026-11-09')[0]['source_id']==first
    assert work_once(factory,store)
    with api_factory(info={'user':student}) as db:
        c=CampusService(db,resolve_actor(db,student,tenant))
        assert [r['source_id'] for r in c.timetable('2026-11-09')]==[second]
    third_job,third=queue('revoked.json',[{**row,'subject':'Must not publish'}],second)
    with factory() as db:
        membership=db.scalar(select(Membership).where(Membership.tenant_id==tenant,Membership.user_id==admin))
        membership.active=False
        db.commit()
    assert work_once(factory,store)
    with factory() as db:
        assert db.get(IngestionJob,third_job).status=='failed'
        assert db.get(Source,third).publication_status=='draft'
        assert db.get(Source,second).publication_status=='published'
    owner.dispose()
    api.dispose()
