"""Grid/unknown-value regressions plus optional operator-owned original uploads.

Real uploads remain ignored local evidence, never copied into public fixtures.
"""
import json
from pathlib import Path

import pandas as pd
import pymupdf
import pytest

from backend.app.ingestion import Ingestion, validate_records
from backend.app.parsing import parse_file, detect_kind
from backend.app.schemas import ImportOptions, MenuData

ROOT = Path(__file__).resolve().parents[2]


def actual_upload(kind):
    manifest = ROOT / '.scratch/real-import-investigation/jobs.json'
    if not manifest.exists():
        pytest.skip('Operator-owned original uploads are not checked into Git; local investigation manifest is unavailable.')
    job = next(row for row in json.loads(manifest.read_text()) if row['kind'] == kind)
    path = ROOT / job['local_file']
    if not path.is_file():
        pytest.skip('The original operator-owned upload is not available locally.')
    return parse_file(job['filename'],path.read_bytes())


def test_actual_timetable_detects_grid_and_keeps_parallel_sessions_without_assigning_batches():
    parsed = actual_upload('timetable')
    assert detect_kind(parsed) == 'timetable'
    rows = parsed['rows']
    assert len(rows) == 28
    assert {r['day'] for r in rows} == set(range(6))
    assert all(r['effective_from']=='2026-09-07' and r['effective_to']=='' for r in rows)
    assert all(r['start_time'] and r['end_time'] for r in rows)
    assert not any(r['start_time'] in ('11:00','13:30') for r in rows)
    labs = [r for r in rows if 'Batch' in r.get('__source_cell', '')]
    assert len(labs) == 4
    assert all(not r['lab_batch'] for r in rows)
    assert [(r['day'],r['start_time'],r['end_time']) for r in labs] == [
        (0,'14:30','16:30'),(0,'14:30','16:30'),
        (4,'11:30','13:30'),(4,'11:30','13:30')]
    assert all(not r['faculty_code'] for r in rows)
    assert 'Course' in parsed['course_mapping_text']


def test_actual_menu_uses_prices_not_item_numbers_or_cup_volumes_and_requires_review(env):
    parsed = actual_upload('menu')
    assert detect_kind(parsed) == 'menu'
    assert len(parsed['rows']) == 34  # 35 printed entries; identical papaya entry occurs twice.
    assert all(r['vegetarian'] is None and r['location']=='' for r in parsed['rows'])
    assert not any(r['price'] in ['90','150','250'] or 'cup' in r['name'].lower() for r in parsed['rows'])
    juice = next(r for r in parsed['rows'] if r['name'].strip().casefold()=='lemon juice')
    assert juice['price']=='20'
    badam = next(r for r in parsed['rows'] if r['name'].casefold()=='badam milk')
    # Both OCR layouts can agree on an incorrect digit (this image prints 15).
    # The preview is unverified, regardless of layout agreement: never auto-import it.
    assert parsed['needs_review'] and parsed['ocr_issues']
    manifest = json.loads((ROOT / '.scratch/real-import-investigation/jobs.json').read_text())
    original = next(row for row in manifest if row['kind'] == 'menu')
    pipe = Ingestion(env.service, env.store)
    job = pipe.receive(original['filename'], (ROOT / original['local_file']).read_bytes(), ImportOptions(kind='menu'))
    from backend.app.ingestion import work_once
    env.db.commit()
    work_once(env.factory, env.store)
    env.db.expire_all()
    # Supply one independently reviewed row, isolating the OCR acknowledgment
    # check from other incomplete menu rows. No source is confirmed or published.
    reviewed = {**badam, 'price': '15'}
    pipe.preview(job, rows=[reviewed])
    assert job.preview['valid_count'] == 1 and not job.preview['error_count']
    with pytest.raises(ValueError, match='verify the OCR'):
        pipe.confirm(job)


def test_native_pdf_grid_handles_merged_periods_and_labs():
    document=pymupdf.open()
    page=document.new_page(width=1050,height=450)
    page.insert_text((25,30),'Class Time Table Classroom No. R1 w.e.f. 07.09.2026',fontsize=10)
    xs=[20+i*110 for i in range(10)]
    ys=[80,140,210,280,350]
    for y in ys:
        page.draw_line((xs[0],y),(xs[-1],y))
    for i,x in enumerate(xs):
        if i==8:
            page.draw_line((x,ys[0]),(x,ys[1]))
            page.draw_line((x,ys[2]),(x,ys[-1]))
        else:
            page.draw_line((x,ys[0]),(x,ys[-1]))
    headers=['DAY','9:00-10:00','10:00-11:00','11:00-11:30\nBreak','11:30-12:30',
             '12:30-1:30','1:30-2:15\nLunch Break','2:30-3:30','3:30-4:30']
    for x,label in zip(xs,headers):
        page.insert_text((x+5,102),label,fontsize=8)
    for day_index,day in enumerate(['MONDAY','TUESDAY','WEDNESDAY']):
        top=ys[day_index+1]
        page.insert_text((xs[0]+5,top+22),day,fontsize=8)
        for column in [1,2,4,5]:
            page.insert_text((xs[column]+5,top+22),'CS123AB',fontsize=8)
        page.insert_text((xs[7]+5,top+22),'Batch-1 - DSA Lab [RM]\nBatch-2 - CPS Lab [NS]' if not day_index else 'Design Lab',fontsize=8)
    parsed=parse_file('native-grid.pdf',document.tobytes())
    document.close()
    assert detect_kind(parsed)=='timetable'
    labs=[row for row in parsed['rows'] if row['day']==0 and 'Lab' in row['subject']]
    assert len(labs)==2 and all(row['start_time']=='14:30' and row['end_time']=='16:30' for row in labs)
    assert all(not row['lab_batch'] and 'Batch' in row['__source_cell'] for row in labs)


def test_unknown_menu_values_are_explicit_and_price_errors_identify_field(env):
    item=MenuData(name='Source item',price=30,category='Source category')
    assert item.vegetarian is None and item.location==''
    rows,errors=validate_records(pd.DataFrame([dict(name='Source item',category='Drinks')]),'menu',
        {'name':'name','category':'category'},env.service,'All')
    assert not rows and errors[0]['row']==2 and errors[0]['fields']==['price']


def test_reextract_cannot_change_published_source(env):
    ingestion=Ingestion(env.service,env.store)
    job=ingestion.receive('unknown-menu.json',json.dumps([dict(name='Drink',price=20,category='Drink')]).encode(),
                          ImportOptions(kind='menu'))
    source=env.service.management_source(job.source_id)
    source.publication_status='published'
    with pytest.raises(ValueError,match='draft'):
        ingestion.preview(job,reextract=True)
    assert source.publication_status=='published' and not job.options['ocr_extract_pending']
