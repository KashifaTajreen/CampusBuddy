"""Existing-draft repair checks; only disposable fixtures, never live writes."""
import json
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import select,func,delete

from backend.app import ingestion as module
from backend.app.ingestion import Ingestion,work_once
from backend.app.models import Source,CampusRecord,Document,DocumentChunk
from backend.app.schemas import ImportOptions
from backend.app.parsing import parse_file
from backend.app.duplicates import duplicate_report
from backend.tests.test_auth_api import client as client,key as key,token
from backend.tests.test_events_phase2 import event_env as event_env,e5 as e5,TEXT


def test_entire_real_b_section_pdf_with_tall_course_legend(env):
    original=Path('.scratch/current-import-repair/e808c310-852d-46e9-bc2d-a5d4a1b000e1.pdf')
    if not original.exists():
        pytest.skip('Real uploaded B-section PDF is not present in ignored local evidence storage.')
    parsed=parse_file(original.name,original.read_bytes(),import_kind='timetable')
    frame=pd.DataFrame(parsed['rows']).fillna('')
    mapping=module.detect_mapping(frame.columns,'timetable')
    rows,errors=module.validate_records(frame,'timetable',mapping,env.service,'AIML',3,'B')
    assert not errors,errors
    assert len(rows)==29
    expected=[
      (0,'09:00','11:00','CS233AI'),(0,'09:00','11:00','CI234AI'),(0,'11:30','12:30','CV232TA/ME232TB/BT232TD'),
      (0,'12:30','13:30','CS236AT'),(0,'14:30','15:30','MA231TF'),(0,'15:30','16:30',None),
      (1,'09:00','10:00','CI234AI'),(1,'10:00','11:00','CS233AI'),(1,'11:30','12:30','MA231TF'),(1,'12:30','13:30','CS235AT'),(1,'14:30','16:30','CS238DL'),
      (2,'09:00','10:00','CS233AI'),(2,'10:00','11:00','CI234AI'),(2,'11:30','12:30','MA231TF'),(2,'12:30','13:30','CS235AT'),(2,'14:30','15:30','CS236AT'),(2,'15:30','16:30','CV232TA/ME232TB/BT232TD'),
      (3,'09:00','10:00','CS235AT'),(3,'10:00','11:00','CS233AI'),(3,'11:30','12:30','MA231TF'),(3,'12:30','13:30',None),(3,'14:30','16:30','CS233AI'),(3,'14:30','16:30','CI234AI'),
      (4,'10:00','11:00','CV232TA/ME232TB/BT232TD'),(4,'11:30','12:30','CS236AT'),(4,'12:30','13:30','CI234AI'),(4,'14:30','16:30','MAT149AT'),
      (5,'09:00','11:00','CS238DL'),(5,'11:30','13:30','CS238DL')]
    assert [(r['day'],r['start_time'][:5],r['end_time'][:5],r['course_code']) for r in rows]==expected
    catalog={'CS233AI':('Data Structures and Applications','Prof. Aishwarya'),
             'CI234AI':('Design Principles of Cyber Physical Systems','Dr. Somesh Nandi'),
             'CS235AT':('Operating Systems','Dr. Viswavardhan Reddy'),
             'CS236AT':('Quantum Computing Essentials','Prof. Sanjana'),
             'MA231TF':('Probability and Statistics for Machine Learning and Data Science','Dr. Hemanth Kumar'),
             'MAT149AT':('Bridge Course: Mathematics',None),
             'CS238DL':('Applied Design thinking lab','Prof. Rushikesh Padaki'),
             'CV232TA/ME232TB/BT232TD':('Environment & Sustainability / Material Science for Engineers / Bio-Inspired Engineering',None)}
    for r in rows:
        code=r['course_code']
        if code:
            name,faculty=catalog[code]
            assert r['subject']==name+(' Lab' if 'Batch-' in next(raw['__source_cell'] for raw in parsed['rows'] if raw['day']==r['day'] and raw['start_time']==r['start_time'][:5]) else '')
            assert r['faculty_name']==faculty
        else:
            assert r['subject']=='Counselling' and r['faculty_name'] is None
        assert r['department']=='AIML' and r['section']=='B' and r['semester']==3
        assert r['effective_to'] is None and not r['lab_batch']
        if r['subject'].endswith(' Lab'):
            assert r['room']=={'CS233AI':'CCH Lab 1','CI234AI':'AIML Lab 1'}[code]
        elif code=='CS238DL':
            assert r['room']=='RVU 501'
        else:
            assert r['room']=='RVU A Block-501'
    ingest=Ingestion(env.service,env.store)
    # The generic seed intentionally includes a second section. Remove only
    # its synthetic B rows here so the assertion isolates cross-section A data.
    env.db.execute(delete(CampusRecord).where(CampusRecord.kind=='timetable',CampusRecord.section=='B'))
    job=ingest.receive('b-local.json',json.dumps(rows).encode(),ImportOptions(kind='timetable',department='AIML',semester=3,section='B'))
    assert not duplicate_report(env.service,env.db.get(Source,job.source_id),rows)['blocking']
    assert len(job.preview['overlap_warnings'])==2


def test_legacy_event_draft_reextracts_in_place_without_indexing_or_publication(client,key,event_env):
    env=event_env
    ingest=Ingestion(env.service,env.store)
    job=ingest.receive('legacy-poster.txt',TEXT.encode(),ImportOptions(kind='events'))
    sid,jid=job.source_id,job.id
    assert not job.preview.get('event_workflow')
    env.db.commit()
    headers={'Authorization':'Bearer '+token(key,env.user)}
    response=client.post(f'/v1/ingestion/{jid}/extract-event',headers=headers)
    assert response.status_code==200,response.text
    assert work_once(env.factory,env.store)
    env.db.expire_all()
    assert job.id==jid and job.source_id==sid and job.status=='preview'
    assert job.preview['event_rows'] and job.preview['event_pages'][0]['text']
    assert not job.preview['event_reviewed'] and not job.preview['event_index_ready']
    assert env.db.get(Source,sid).publication_status=='draft'
    assert env.db.scalar(select(func.count()).select_from(Document).where(Document.source_id==sid))==0
    assert env.db.scalar(select(func.count()).select_from(CampusRecord).where(CampusRecord.source_id==sid))==0


def test_validate_event_indexes_draft_only_and_publish_requires_indexing(client,key,event_env):
    env=event_env
    ingest=Ingestion(env.service,env.store)
    job=ingest.receive('indexed-draft.txt',TEXT.encode(),ImportOptions(kind='events',event_workflow=True))
    env.db.commit()
    headers={'Authorization':'Bearer '+token(key,env.user)}
    assert client.post(f'/v1/ingestion/{job.id}/publish-event',headers=headers).status_code==409
    draft=client.post(f'/v1/ingestion/{job.id}/event-draft',headers=headers,json={'events':job.preview['event_rows'],'pages':job.preview['event_pages']})
    assert draft.status_code==200 and not draft.json()['preview']['event_reviewed'] and draft.json()['status']=='preview'
    response=client.post(f'/v1/ingestion/{job.id}/event-review',headers=headers,json={'events':job.preview['event_rows'],'pages':job.preview['event_pages']})
    assert response.status_code==200,response.text
    assert response.json()['status']=='queued'
    assert work_once(env.factory,env.store)
    env.db.expire_all()
    assert job.status=='preview' and job.preview['event_index_ready']
    source=env.db.get(Source,job.source_id)
    assert source.publication_status=='draft' and source.approved_by is None and source.published_by is None
    document=env.db.scalar(select(Document).where(Document.source_id==source.id))
    assert document and env.db.scalar(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id==document.id))>0
    assert env.db.scalar(select(func.count()).select_from(CampusRecord).where(CampusRecord.source_id==source.id))==0
    assert not job.options['event_publish_requested']


def test_duplicate_legacy_upload_upgrades_draft_without_a_new_source(event_env):
    env=event_env
    ingest=Ingestion(env.service,env.store)
    job=ingest.receive('old.txt',TEXT.encode(),ImportOptions(kind='events'))
    sid,jid=job.source_id,job.id
    same=ingest.receive('old.txt',TEXT.encode(),ImportOptions(kind='events',event_workflow=True))
    assert same.id==jid and same.source_id==sid and same.preview['event_rows']
    assert not same.preview['event_reviewed'] and env.db.get(Source,sid).publication_status=='draft'
