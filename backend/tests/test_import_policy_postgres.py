"""Only an explicitly disposable, loopback PostgreSQL database may run this test."""
import json
import os

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from backend.app.auth import resolve_actor
from backend.app.files import Files
from backend.app.ingestion import Ingestion, work_once
from backend.app.models import Institution, Membership, Source
from backend.app.publication import Publication
from backend.app.schemas import ImportOptions
from backend.app.services import CampusService
from backend.tests.conftest import MemoryStore


def test_scoped_self_review_and_draft_cleanup_on_postgres():
    url=os.environ.get('TEST_IMPORT_POSTGRES_URL')
    if not url:
        pytest.skip('Requires the owned disposable import-policy rehearsal PostgreSQL database.')
    parsed=make_url(url)
    assert parsed.host=='127.0.0.1' and parsed.database=='campus_import_rehearsal'
    owner=create_engine(url)
    api=create_engine(url)
    @event.listens_for(api,'connect')
    def restricted(connection,_):
        connection.cursor().execute('SET ROLE campus_api')
    factory=sessionmaker(owner,expire_on_commit=False)
    api_factory=sessionmaker(api,expire_on_commit=False)
    tenant='00000000-0000-4000-8000-000000000901'
    admin='00000000-0000-4000-8000-000000000902'
    student='00000000-0000-4000-8000-000000000903'
    @event.listens_for(api_factory,'after_begin')
    def request_identity(session,transaction,connection):
        connection.execute(text("SELECT set_config('app.user_id',:user,true),set_config('app.tenant_id',:tenant,true)"),
                           {'user':session.info.get('actor_user',admin),'tenant':tenant})
    with factory() as db:
        db.add(Institution(id=tenant,name='Disposable import policy test'))
        db.flush()
        db.add_all([Membership(tenant_id=tenant,user_id=admin,role='institution_admin',department='AIML',semester=3,section='A'),
                    Membership(tenant_id=tenant,user_id=student,role='student',department='AIML',semester=3,section='A')])
        db.commit()
    store=MemoryStore()
    with api_factory() as db:
        service=CampusService(db,resolve_actor(db,admin,tenant))
        pipeline=Ingestion(service,store)
        job=pipeline.receive('recurring.json',json.dumps([dict(department='AIML',semester=3,section='A',
            subject='Actual scoped test',day=4,start_time='09:00',end_time='10:00')]).encode(),
            ImportOptions(kind='timetable',department='AIML',semester=3,section='A'))
        assert not job.preview['error_count']
        pipeline.confirm(job)
        published_id=job.source_id
        db.commit()
        assert work_once(factory,store)
        db.expire_all()
        service.actor=resolve_actor(db,admin,tenant)
        publication=Publication(service)
        publication.transition(published_id,'approve')
        publication.transition(published_id,'publish')
        db.commit()
        assert db.get(Source,published_id).publication_status=='published'
        draft=pipeline.receive('delete-me.json',b'[{"name":"Delete test","price":20}]',ImportOptions(kind='menu'))
        db.commit()
        draft_id=draft.source_id
        kept=pipeline.receive('keep-me.json',b'[{"name":"Keep test","price":30}]',ImportOptions(kind='menu'))
        db.commit()
        kept_id=kept.source_id
        Files(service,store).change(draft_id,'delete_draft','DELETE '+draft_id)
        db.commit()
        assert db.get(Source,draft_id).file_state=='purged'
        assert db.get(Source,kept_id).file_state=='active'
        assert db.get(Source,published_id).publication_status=='published'
        with pytest.raises(HTTPException):
            Files(service,store).change(published_id,'delete_draft','DELETE '+published_id)
        db.rollback()
    with api_factory(info={'actor_user':student}) as db:
        service=CampusService(db,resolve_actor(db,student,tenant))
        assert len(service.timetable('2026-11-06'))==1
        assert not service.menu()
        with pytest.raises(HTTPException):
            Files(service,store).change(kept_id,'delete_draft','DELETE '+kept_id)
    owner.dispose()
    api.dispose()
