"""Actual operator-owned workbook regression; originals remain ignored and unchanged."""
import json
from pathlib import Path

import pandas as pd
import pytest

from backend.app.ingestion import detect_mapping, validate_records
from backend.app.parsing import parse_file

ROOT=Path(__file__).resolve().parents[2]


def workbook(filename,columns):
    manifest=ROOT/'.scratch/real-import-investigation/jobs-latest.json'
    if not manifest.exists():
        pytest.skip('Actual operator-owned workbook manifest is not present; never substitute invented institutional data.')
    job=next(j for j in json.loads(manifest.read_text()) if j['filename']==filename and len(j['preview'].get('columns',[]))==columns)
    path=ROOT/job['local_file']
    return parse_file(filename,path.read_bytes())


def test_actual_28_row_timetable_allows_recurring_dates_and_exact_mapping(env):
    parsed=workbook('campusos_timetable_AIML_sem3_secA.xlsx',11)
    frame=pd.DataFrame(parsed['rows']).fillna('')
    mapping=detect_mapping(frame.columns,'timetable')
    assert mapping['faculty_name']=='faculty' and mapping['course_code']=='course_code' and mapping['subject']=='course_name'
    assert 'effective_from' not in mapping and 'effective_to' not in mapping
    rows,errors=validate_records(frame,'timetable',mapping,env.service,'AIML',3,'A')
    assert len(rows)==28 and not errors
    assert all(r['effective_from'] is None and r['effective_to'] is None for r in rows)
    assert all(r['faculty_code'] is None for r in rows)
    assert rows[0]['faculty_name']=='Dr. Deepamala N'
    overridden,errors=validate_records(frame,'timetable',mapping,env.service,'AIML',3,'A',
        defaults={'effective_from':'2026-09-07','effective_to':'2027-06-07'})
    assert not errors and all(r['effective_to']=='2027-06-07' for r in overridden)


def test_actual_full_menu_127_data_9_notes_and_weekly_availability(env):
    parsed=workbook('campusos_full_college_menu.xlsx',14)
    assert len(parsed['rows'])==127 and len(parsed['skipped_rows'])==9 and parsed['requires_demo']
    frame=pd.DataFrame(parsed['rows']).fillna('')
    mapping=detect_mapping(frame.columns,'menu')
    mapping.pop('department')  # Operator explicitly approved campus-wide scope; location remains intact.
    rows,errors=validate_records(frame,'menu',mapping,env.service,'All')
    assert len(rows)==127 and not errors
    assert all(r['served_on'] is None and r['department']=='All' for r in rows)
    assert {r['location'] for r in rows}=={'Mingos Cafe','Main Canteen','Hostel Mess'}
    assert any(r['served_day']==5 for r in rows) and any(r['served_day'] is None for r in rows)
