"""Independent image-transcribed reference and derived format fixtures. No DB."""
import json
import os
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REFERENCE=json.loads((ROOT/'backend/tests/fixtures/rvce_timetable_reference.json').read_text())


def original_image():
    return Path(os.environ.get('CAMPUS_TIMETABLE_REFERENCE', str(ROOT/'.scratch/private-fixtures/TT.jpeg')))


def expected_rows():
    rows=[]
    for day,start,end,code,room,lab in REFERENCE['classes']:
        subject,faculty=REFERENCE['catalog'][code]
        rows.append(dict(department='AIML',semester=3,section='A',day=day,start_time=start,end_time=end,
            course_code=code or None,subject=subject+(' Lab' if lab else ''),faculty_name=faculty,room=room,
            effective_from='2026-09-07',effective_to=None))
    return rows


def canonical(value):
    return re.sub(r'[^a-z0-9]','',str(value or '').casefold())


def compare(rows):
    fields=['day','start_time','end_time','course_code','subject','faculty_name','room']
    def key(row):
        return row['day'],str(row['start_time'])[:5],str(row['end_time'])[:5],canonical(row.get('course_code'))
    actual={key(r):r for r in rows}
    differences=[]
    for expected in expected_rows():
        row=actual.get(key(expected))
        if row is None:
            differences.append(dict(expected=expected,problem='Missing or mismatched class key'))
            continue
        for field in fields:
            value=str(row.get(field,''))[:5] if field.endswith('_time') else row.get(field)
            if canonical(value)!=canonical(expected.get(field)):
                differences.append(dict(day=row['day'],start=row['start_time'],field=field,
                    expected=expected.get(field),actual=value))
    expected_keys={key(r) for r in expected_rows()}
    for row in rows:
        if key(row) not in expected_keys:
            differences.append(dict(actual=row,problem='Unexpected class'))
    return dict(expected_count=28,actual_count=len(rows),differences=differences,
                exact_schedule_match=len(rows)==28 and not differences and len(actual)==28)


def create_formats():
    import pandas as pd
    import pymupdf as fitz
    from PIL import Image
    directory=ROOT/'.scratch/timetable-phase1'
    directory.mkdir(parents=True,exist_ok=True)
    rows=expected_rows()
    for row in rows:
        row['course_name']=row.pop('subject')
        row['faculty']=row.pop('faculty_name')
    pd.DataFrame(rows).to_excel(directory/'TT-reference.xlsx',index=False)
    pd.DataFrame(rows).to_csv(directory/'TT-reference.csv',index=False)
    original=original_image()
    with Image.open(original) as image:
        image.save(directory/'TT-reference.png')
    document=fitz.open()
    image=fitz.Pixmap(original.read_bytes())
    page=document.new_page(width=image.width,height=image.height)
    page.insert_image(page.rect,stream=original.read_bytes())
    document.save(directory/'TT-scanned.pdf')
    document.close()
    # A labelled digital table derived from the image transcription, not an
    # original digital institutional PDF. Exercises native PDF text/table parsing.
    document=fitz.open()
    for offset in (0,14):
        page=document.new_page(width=1600,height=850)
        headers=['day','start_time','end_time','course_code','course_name','faculty','room']
        widths=[65,90,90,230,650,300,150]
        positions=[15]
        for width in widths:
            positions.append(positions[-1]+width)
        values=[headers]+[[str(row[h]) if row.get(h) is not None else '' for h in headers] for row in rows[offset:offset+14]]
        for index,line in enumerate(values):
            top=25+index*48
            for col,value in enumerate(line):
                page.draw_rect(fitz.Rect(positions[col],top,positions[col+1],top+48))
                page.insert_textbox(fitz.Rect(positions[col]+3,top+3,positions[col+1]-3,top+45),value,fontsize=8)
    document.save(directory/'TT-digital-derived.pdf')
    document.close()
    return directory


if __name__=='__main__':
    create_formats()
    print('Created local format fixtures derived from the visually checked TT.jpeg reference. No database accessed.')
