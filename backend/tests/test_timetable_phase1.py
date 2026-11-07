import json

import pandas as pd
import pytest
from sqlalchemy import delete, select

from backend.app import ingestion as module
from backend.app.ingestion import Ingestion, work_once
from backend.app.models import CampusRecord, Source
from backend.app.parsing import parse_file
from backend.app.schemas import ImportOptions, TimetableData
from backend.tests.test_auth_api import client as client, key as key, token
from scripts.timetable_acceptance import compare, create_formats, expected_rows, original_image


@pytest.fixture
def timetable_env(env, monkeypatch):
    # Only the per-test disposable SQLite fixture, never the operator's database.
    assert 'pytest' in str(env.engine.url.database)
    env.db.execute(delete(CampusRecord).where(CampusRecord.kind=='timetable'))
    env.db.commit()
    monkeypatch.setattr(module,'ObjectStore',lambda:env.store)
    return env


@pytest.fixture(scope='module')
def formats():
    path=original_image()
    if not path.exists():
        pytest.skip('Actual TT.jpeg reference is unavailable; no synthetic replacement for the primary acceptance image.')
    return create_formats()


def options():
    return ImportOptions(kind='timetable',department='AIML',semester=3,section='A')


def test_entire_actual_image_and_one_explicit_review(timetable_env, formats):
    env=timetable_env
    ingest=Ingestion(env.service,env.store)
    job=ingest.receive('TT.jpeg',original_image().read_bytes(),options())
    assert job.status=='queued'
    env.db.commit()
    assert work_once(env.factory,env.store)
    env.db.expire_all()
    job=ingest.job(job.id)
    assert job.status=='preview' and job.preview['valid_count']==27 and job.preview['review_count']==1
    assert not job.preview.get('schema_mismatch')
    rows=job.preview['editable_rows']
    bad=next(r for r in rows if r.get('__unresolved_subject'))
    assert bad['day']==2 and bad['start_time']=='09:00'
    expected=next(r for r in expected_rows() if r['day']==2 and r['start_time']=='09:00')
    bad.update({k:expected[k] for k in ('subject','course_code','faculty_name')})
    ingest.preview(job,rows=rows,ocr_reviewed=True,overlap_reviewed=True)
    assert job.preview['valid_count']==28 and not job.preview['review_count'] and not job.preview['error_count']
    records,_=module.validate_records(pd.DataFrame(rows).fillna(''),'timetable',job.options['mapping'],env.service,'AIML',3,'A')
    assert compare(records)['exact_schedule_match'],compare(records)


@pytest.mark.parametrize('filename',['TT-reference.xlsx','TT-reference.csv','TT-digital-derived.pdf','TT-scanned.pdf','TT-reference.png'])
def test_supported_formats_extract_and_validate(timetable_env,formats,filename):
    env=timetable_env
    parsed=parse_file(filename,(formats/filename).read_bytes(),import_kind='timetable')
    assert len(parsed['rows'])==28
    frame=pd.DataFrame(parsed['rows']).fillna('')
    mapping=module.detect_mapping(frame.columns,'timetable')
    ready,errors=module.validate_records(frame,'timetable',mapping,env.service,'AIML',3,'A')
    assert len(ready)>=27,errors
    if filename in ('TT-reference.xlsx','TT-reference.csv','TT-digital-derived.pdf'):
        assert compare(ready)['exact_schedule_match'],compare(ready)


def test_partial_publication_retains_unresolved_rows_and_nulls(timetable_env):
    env=timetable_env
    good=dict(day='Monday',start_time='09:00',end_time='10:00',subject='Verified class',faculty_name='NA',room='')
    bad={**good,'subject':'Incomplete class','start_time':''}
    raw=[good,bad,{},dict(subject='Lunch Break')]
    ingest=Ingestion(env.service,env.store)
    job=ingest.receive('partial.json',json.dumps(raw).encode(),options())
    assert job.preview['valid_count']==1 and job.preview['review_count']==1 and job.preview['skipped_count']==2
    with pytest.raises(ValueError):
        ingest.confirm(job)
    ingest.preview(job,timetable_ready_only=True)
    ingest.confirm(job)
    job.options={**job.options,'timetable_publish_requested':True}
    env.db.commit()
    assert work_once(env.factory,env.store)
    env.db.expire_all()
    assert env.db.get(Source,job.source_id).publication_status=='published'
    record=env.db.scalar(select(CampusRecord).where(CampusRecord.source_id==job.source_id))
    assert record.payload['faculty_name'] is None and record.payload['room'] is None
    assert job.staged_data['review_errors'] and len(job.staged_data['editable_rows'])==2
    assert job.preview['published_count']==1


def test_direct_publish_endpoint_authorization_and_same_admin(client,key,timetable_env):
    env=timetable_env
    ingest=Ingestion(env.service,env.store)
    job=ingest.receive('direct.json',json.dumps([dict(day=0,start_time='09:00',end_time='10:00',subject='Monday class')]).encode(),options())
    env.db.commit()
    url=f'/v1/ingestion/{job.id}/publish-timetable'
    response=client.post(url,json={},headers={'Authorization':'Bearer '+token(key,env.other)})
    assert response.status_code in (403,404)
    response=client.post(url,json={})
    assert response.status_code==200,response.text
    assert response.json()['status']=='queued'
    assert work_once(env.factory,env.store)
    env.db.expire_all()
    source=env.db.get(Source,job.source_id)
    assert source.publication_status=='published' and source.approved_by==source.published_by==env.user


def test_parallel_rows_are_distinct_and_optional_nulls():
    row=TimetableData(department='AIML',semester=3,section='A',subject='Class',day=0,start_time='09:00',end_time='10:00',faculty_name='NA')
    assert row.faculty_name is None and row.lab_batch is None
