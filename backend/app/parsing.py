"""Bounded, network-free format parsers, executed in a disposable subprocess."""
import base64
from contextlib import redirect_stdout, redirect_stderr
import csv
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile
from xml.etree import ElementTree as ET

FORMATS = {".pdf", ".csv", ".xlsx", ".json", ".docx", ".pptx", ".txt", ".md", ".png", ".jpg", ".jpeg", ".webp"}
MAX_BYTES = 15 * 1024 * 1024


def archive(content, suffix):
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
        entries = z.infolist()
        if len(entries) > 2000 or sum(e.file_size for e in entries) > 80 * 1024 * 1024:
            raise ValueError("Office archive exceeds expansion/member limits.")
        if any(e.flag_bits & 1 or e.file_size > 20 * 1024 * 1024 or ".." in Path(e.filename).parts
               or e.filename.startswith(("/", "\\")) for e in entries):
            raise ValueError("Unsafe or encrypted Office archive.")
        required = {".docx": "word/document.xml", ".pptx": "ppt/presentation.xml", ".xlsx": "xl/workbook.xml"}[suffix]
        if required not in z.namelist() or "[Content_Types].xml" not in z.namelist():
            raise ValueError("Office file contents do not match its extension.")
        if any("vbaproject" in e.filename.lower() for e in entries):
            raise ValueError("Macro-enabled files are not supported.")
        for entry in entries:
            if entry.filename.endswith((".xml", ".rels")):
                raw = z.read(entry)
                if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                    raise ValueError("XML document types/entities are not supported.")
        expected = {".docx": "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document",
                    ".pptx": "{http://schemas.openxmlformats.org/presentationml/2006/main}presentation",
                    ".xlsx": "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}workbook"}[suffix]
        if xml(z, required).tag != expected:
            raise ValueError("Office root XML type does not match its extension.")
        return z
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ValueError("Malformed Office file.") from exc


def xml(z, name):
    raw = z.read(name)
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("XML document types/entities are not supported.")
    return ET.fromstring(raw)


def table_rows(matrix):
    matrix = [[str(v or "").strip() for v in row] for row in matrix if row and any(v for v in row)]
    if len(matrix) < 2:
        return []
    headers = matrix[0]
    if len(headers) > 100 or len(set(headers)) != len(headers) or any(not h for h in headers):
        raise ValueError("Tables require unique, nonempty column headers (at most 100).")
    if any(len(row) != len(headers) for row in matrix[1:]):
        raise ValueError("Malformed table: every row must match its header width.")
    return [dict(zip(headers, row)) for row in matrix[1:]]


def text_tables(text):
    rows = []
    group = []
    for line in text.splitlines() + [""]:
        if "|" in line or "\t" in line:
            values = next(csv.reader([line.strip().strip("|")], delimiter="|" if "|" in line else "\t"))
            if all(re.fullmatch(r"[: -]+", v) for v in values):
                continue
            group.append(values)
        elif group:
            rows.extend(table_rows(group))
            group = []
    return rows


def detect_kind(parsed):
    if parsed.get("detected_kind"):
        return parsed["detected_kind"]
    headers = " ".join(str(k).lower().replace("_", " ") for r in parsed["rows"][:1] for k in r)
    if re.search(r"price|cost", headers) and re.search(r"name|item", headers):
        return "menu"
    if ("subject" in headers and re.search(r"day|start|time", headers)) or ("semester" in headers and "section" in headers and "day" in headers):
        return "timetable"
    if "office" in headers and re.search(r"name|code", headers):
        return "faculty"
    if "title" in headers and "start" in headers and "end" in headers:
        return "events"
    return "document"


def needs_ocr(page):
    text = page.get_text().strip()
    if len(text) < 5:
        return True
    # A selectable header must not hide a scanned body on the same page.
    # Large raster illustrations in otherwise long text do not trigger OCR.
    area = page.rect.width * page.rect.height
    return len(text) < 500 and any(
        (info["bbox"][2]-info["bbox"][0]) * (info["bbox"][3]-info["bbox"][1]) > area * 0.35
        for info in page.get_image_info())


def parse_inline(filename, content, ocr=False, tessdata="", languages="eng", probe=False, preview_only=False, import_kind=None):
    suffix = Path(filename).suffix.lower()
    if suffix not in FORMATS or not content or len(content) > MAX_BYTES:
        raise ValueError("Use a supported nonempty file up to 15 MB.")
    pages, rows, warnings, sheets, images, blocks = [], [], [], [], [], []
    ocr_pages, ocr_issues = [], []
    detected_kind, course_mapping_text = None, ''
    skipped_rows=[]
    page_count=0
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        from backend.app.ocr import image, recognize, menu_rows
        image(content, suffix, probe=True)
        if preview_only:
            import pymupdf as fitz
            with fitz.open(stream=content, filetype=suffix.lstrip(".")) as doc:
                pix = doc[0].get_pixmap(matrix=fitz.Matrix(min(1, 900/doc[0].rect.width), min(1, 900/doc[0].rect.width)), alpha=False)
                return dict(format=suffix.lstrip("."), images=[dict(page=1, data="data:image/png;base64,"+base64.b64encode(pix.tobytes("png")).decode())],
                    tables=[], text=[], blocks=[], warnings=["Original image preview; OCR is a separate reviewed extraction step."], truncated=False)
        if probe:
            return dict(needs_ocr=True, format=suffix.lstrip("."))
        if import_kind=='events':
            from backend.app.event_extraction import poster_ocr
            try:
                result=poster_ocr(content,suffix,tessdata,languages) if ocr else dict(text='',qr_url=None,qr_supported=False)
                message='Event OCR text requires administrator review.' if result['text'].strip() else 'No usable OCR text was extracted. Enter a manual transcription before validation.'
            except (ValueError,RuntimeError):
                result=dict(text='',qr_url=None,qr_supported=False)
                message='Event OCR is unavailable or unreadable. Enter a manual transcription; the original is preserved.'
            return dict(pages=[(1,result['text'])],rows=[],format=suffix.lstrip('.'),detected_kind='events',
                warnings=[message],ocr_pages=[1],ocr_issues=[],needs_review=True,confidence_available=False,
                qr_url=result['qr_url'],qr_supported=result['qr_supported'])
        if not ocr:
            raise ValueError("Image OCR is disabled. Enable local OCR and install language data.")
        result = recognize(content, suffix, tessdata, languages,timetable=import_kind=='timetable')
        from backend.app.timetable_grid import extract_grid
        grid = result.get('timetable_grid') or extract_grid(result)
        rows, ocr_issues = ((grid["rows"], grid["issues"]) if grid else
            ([],[dict(message='No readable timetable grid was reconstructed. Use a clearer timetable scan or enter corrected class rows.')]) if import_kind=='timetable'
            else menu_rows(result["aligned_text"]))
        for number,row in enumerate(rows,2):
            if not grid and re.sub(r'[^a-z0-9]','',row['name'].casefold()) in result.get('price_conflicts',[]):
                row['price']=''
                ocr_issues.append(dict(row=number,message='price: not consistently read across OCR layouts. Enter the exact amount from the original.'))
        if len(rows) > 10000 or len(json.dumps(rows)) > 4_000_000:
            raise ValueError("Extracted image rows exceed safe limits; split the image.")
        return dict(pages=[(1, result["text"])], rows=rows, format=suffix.lstrip("."),
            detected_kind="timetable" if grid else "menu" if rows else "document",
            course_mapping_text=grid.get("course_mapping_text", "") if grid else "",
            warnings=["OCR output requires manual review; prices, diet and location must be checked."],
            ocr_pages=[1], ocr_issues=ocr_issues, needs_review=True, confidence_available=False)
    if suffix in (".docx", ".pptx", ".xlsx"):
        with archive(content, suffix) as z:
            if suffix == ".xlsx":
                import pandas as pd
                with pd.ExcelFile(io.BytesIO(content), engine_kwargs={"keep_links": False}) as workbook:
                    if len(workbook.sheet_names) > 200:
                        raise ValueError("Workbook has more than 200 worksheets.")
                    for index, sheet in enumerate(workbook.sheet_names, 1):
                        frame = workbook.parse(sheet, keep_default_na=False, nrows=10001)
                        values = frame.astype(str).to_dict(orient="records")
                        if len(frame.columns)>100 or any(str(k).startswith("Unnamed:") for k in frame.columns):
                            warnings.append(f"Worksheet {sheet}: some headers are blank; review the original worksheet layout.")
                        sheets.append(dict(name=sheet, page=index, columns=list(map(str, frame.columns)), rows=values))
                        normalized={str(column).strip().casefold() for column in frame.columns}
                        metadata_sheet=(normalized <= {'topic','note'} or {'issue_id','suggested_action','issue'} <= normalized)
                        for row_number,row in enumerate(values,2):
                            reason = 'Reference/notes worksheet; retained with the original.' if metadata_sheet else (
                                'Completely blank row.' if not any(str(value or '').strip() for value in row.values()) else
                                'Repeated column-header row.' if all(not str(value or '').strip() or str(value).strip().casefold()==str(column).strip().casefold() for column,value in row.items()) else '')
                            if reason:
                                skipped_rows.append(dict(sheet=sheet,row=row_number,reason=reason))
                            else:
                                rows.append(dict(row,__source_row=row_number,__source_sheet=sheet))
                        if len(rows) > 10000:
                            raise ValueError("Workbook exceeds 10,000 total rows.")
                        pages.append((index, "Worksheet: " + sheet + "\n" + "\n".join(" | ".join(f"{k}: {v}" for k, v in row.items()) for row in values)))
            else:
                names = ["word/document.xml"] if suffix == ".docx" else sorted(
                    [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)],
                    key=lambda n: int(re.search(r"slide(\d+)", n)[1]))
                if not 1 <= len(names) <= 200:
                    raise ValueError("Office document must contain 1–200 logical pages/slides.")
                for index, name in enumerate(names, 1):
                    root = xml(z, name)
                    paragraphs = [" ".join(e.itertext()) for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "t"]
                    pages.append((index, "\n".join(paragraphs)))
                    if suffix == ".docx":
                        for paragraph in root.iter():
                            if paragraph.tag.rsplit("}",1)[-1] == "p":
                                text = " ".join(e.text or "" for e in paragraph.iter() if e.tag.rsplit("}",1)[-1] == "t")
                                heading = any(e.tag.rsplit("}",1)[-1] == "pStyle" and any("heading" in v.lower() for v in e.attrib.values()) for e in paragraph.iter())
                                if text.strip():
                                    blocks.append(dict(text=text[:2000], heading=heading))
                    for table in root.iter():
                        if table.tag.rsplit("}", 1)[-1] != "tbl":
                            continue
                        matrix = []
                        for tr in table:
                            if tr.tag.rsplit("}", 1)[-1] != "tr":
                                continue
                            matrix.append([" ".join(e.text or "" for e in cell.iter() if e.tag.rsplit("}", 1)[-1] == "t")
                                           for cell in tr if cell.tag.rsplit("}", 1)[-1] == "tc"])
                        rows.extend(table_rows(matrix))
                if suffix == ".docx":
                    warnings.append("DOCX page 1 is a logical document location; Word pagination is not available.")
    elif suffix in (".csv", ".json", ".txt", ".md"):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("Use UTF-8 text with the matching file extension.") from exc
        if "\x00" in text or text.startswith(("%PDF", "PK\x03\x04")):
            raise ValueError("Binary/mislabeled text file.")
        pages = [(1, text)]
        if suffix == ".json":
            rows = json.loads(text)
            if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
                raise ValueError("JSON must contain a list of row objects.")
            pages = [(1, "\n".join(" | ".join(f"{k}: {v}" for k, v in r.items()) for r in rows))]
            if rows:
                sheets.append(dict(name="JSON records",page=1,columns=list(rows[0]),rows=rows))
        elif suffix == ".csv":
            rows = table_rows(list(csv.reader(io.StringIO(text))))
            sheets.append(dict(name="CSV",page=1,columns=list(rows[0]) if rows else [],rows=rows))
            pages = [(1, "\n".join(" | ".join(f"{k}: {v}" for k, v in r.items()) for r in rows))]
        else:
            rows = text_tables(text)
    else:
        import pymupdf as fitz
        if suffix == ".pdf" and not content.startswith(b"%PDF-"):
            raise ValueError("File content is not a PDF.")
        if suffix == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("File content is not PNG.")
        if suffix in (".jpg", ".jpeg") and not content.startswith(b"\xff\xd8\xff"):
            raise ValueError("File content is not JPEG.")
        with fitz.open(stream=content, filetype=suffix.lstrip(".")) as original:
            if original.needs_pass:
                raise ValueError("Unlock the document before uploading.")
            if suffix != ".pdf" and original[0].rect.width * original[0].rect.height > 20_000_000:
                raise ValueError("Image dimensions exceed the safe OCR limit.")
            pdf_bytes = original.convert_to_pdf() if suffix != ".pdf" else content
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            page_count=len(doc)
            if not 1 <= len(doc) <= 200:
                raise ValueError("PDF must contain 1–200 pages.")
            if probe:
                return dict(needs_ocr=any(needs_ocr(p) for p in doc), format="pdf")
            for index, page in enumerate(doc, 1):
                if preview_only and index>3:
                    break
                if page.rect.width * page.rect.height > 20_000_000:
                    raise ValueError("Page/image dimensions exceed the safe OCR limit.")
                text = page.get_text()
                if preview_only:
                    scale = min(1, 850/page.rect.width)
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale,scale), alpha=False)
                    images.append(dict(page=index, data="data:image/png;base64,"+base64.b64encode(pix.tobytes("png")).decode()))
                if needs_ocr(page) and not preview_only:
                    if not ocr:
                        raise ValueError(f"Page {index} is scanned/empty. OCR is disabled or unavailable; supply OCR text.")
                    try:
                        from backend.app.ocr import recognize, menu_rows
                        if len(ocr_pages) >= 20:
                            raise ValueError("Split OCR documents larger than 20 scanned pages.")
                        if page.rect.width * page.rect.height * (150/72)**2 > 20_000_000:
                            raise ValueError("Rasterized PDF page exceeds the 20 megapixel OCR limit.")
                        raster = None
                        raster_suffix = '.png'
                        # A full-page scan often embeds the original JPEG. Reuse
                        # its pixels instead of resampling and losing small codes.
                        if import_kind == 'timetable' and page.rotation == 0:
                            embedded=page.get_image_info(xrefs=True)
                            if len(embedded)==1:
                                info=embedded[0]
                                box=fitz.Rect(info['bbox'])
                                transform=info['transform']
                                if (info.get('xref') and abs(box.width-page.rect.width)<2 and abs(box.height-page.rect.height)<2
                                        and abs(transform[1])<.001 and abs(transform[2])<.001 and transform[0]>0 and transform[3]>0):
                                    original=doc.extract_image(info['xref'])
                                    if original['ext'] in ('jpeg','jpg','png'):
                                        raster,raster_suffix=original['image'],'.'+original['ext']
                        if raster is None:
                            raster = page.get_pixmap(dpi=150, alpha=False).tobytes("png")
                        if import_kind=='events':
                            from backend.app.event_extraction import poster_ocr
                            result=poster_ocr(raster,raster_suffix,tessdata,languages)
                            text=result['text']
                            pages.append((index,text))
                            ocr_pages.append(index)
                            warnings.append(f'Page {index}: review event OCR text or supply a manual transcription.')
                            continue
                        recognized = recognize(raster, raster_suffix, tessdata, languages,timetable=import_kind=='timetable')
                        text = recognized["text"]
                        from backend.app.timetable_grid import extract_grid
                        grid = recognized.get('timetable_grid') or extract_grid(recognized)
                        if grid:
                            detected_kind,course_mapping_text = 'timetable',grid.get('course_mapping_text','')
                        extracted, issues = ((grid["rows"],grid["issues"]) if grid else
                            ([],[dict(message='No readable timetable grid was reconstructed on this page.')]) if import_kind=='timetable'
                            else menu_rows(recognized["aligned_text"]))
                        rows.extend(extracted)
                        ocr_issues.extend(dict(issue, page=index) for issue in issues)
                        ocr_pages.append(index)
                        warnings.append(f"Page {index}: OCR output requires manual review.")
                        pages.append((index,text))
                        continue
                    except ValueError as exc:
                        raise ValueError(f"Page {index}: {str(exc)[:350]}") from exc
                    except Exception as exc:
                        raise ValueError(f"Page {index}: OCR engine/language data unavailable. Supply OCR text.") from exc
                from backend.app.timetable_grid import extract_grid
                drawing_h, drawing_v = [], []
                for drawing in page.get_drawings():
                    for item in drawing['items']:
                        edges = [(item[1],item[2])] if item[0]=='l' else []
                        if item[0]=='re':
                            r=item[1]
                            edges=[(r.tl,r.tr),(r.tr,r.br),(r.br,r.bl),(r.bl,r.tl)]
                        for a,b in edges:
                            if abs(a.y-b.y)<2:
                                drawing_h.append((a.y,min(a.x,b.x),max(a.x,b.x)))
                            if abs(a.x-b.x)<2:
                                drawing_v.append((a.x,min(a.y,b.y),max(a.y,b.y)))
                grid = extract_grid(dict(words=page.get_text('words'),text=text,width=page.rect.width,
                    height=page.rect.height,horizontal=drawing_h,vertical=drawing_v)) if not needs_ocr(page) and import_kind!='events' else None
                if grid:
                    detected_kind,course_mapping_text = 'timetable',grid.get('course_mapping_text','')
                    rows.extend(grid['rows'])
                    ocr_issues.extend(dict(issue,page=index) for issue in grid['issues'])
                    pages.append((index,text))
                    continue
                finder = page.find_tables()
                tables = finder.tables if finder is not None else []
                for table in tables:
                    extracted = table_rows(table.extract())
                    rows.extend(extracted)
                    text += "\n\n" + "\n".join(" | ".join(f"{k}: {v}" for k, v in r.items()) for r in extracted)
                if not tables:
                    rows.extend(text_tables(text))
                pages.append((index, text))
    if len(rows) > 10000 or any(len(r) > 100 for r in rows):
        raise ValueError("Import at most 10,000 rows and 100 columns.")
    if not pages and rows:
        pages = [(1, "\n".join(" | ".join(f"{k}: {v}" for k, v in r.items()) for r in rows))]
    if sum(len(t) for _, t in pages) > 2_000_000 or len(json.dumps(rows)) > 4_000_000:
        raise ValueError("Extracted content exceeds the safe limit; split the file.")
    if not any(t.strip() for _, t in pages) and import_kind!='events':
        if not preview_only or not images:
            raise ValueError("No readable content found.")
    if preview_only:
        tables = [dict(name=s["name"], page=s["page"], columns=s["columns"], rows=s["rows"][:20], total_rows=len(s["rows"])) for s in sheets[:10]]
        if not tables and rows:
            tables = [dict(name="Extracted table",page=1,columns=list(rows[0]),rows=rows[:20],total_rows=len(rows))]
        result = dict(format=suffix.lstrip("."), images=images, tables=tables,
            text=[dict(page=p,text=t[:8000]) for p,t in pages[:5]] if not tables else [], blocks=blocks[:50],
            warnings=warnings+(["Slide text and tables are supported; rendered slide thumbnails are unavailable."] if suffix==".pptx" else []),
            truncated=page_count>3 or len(pages)>5 or len(sheets)>10 or any(len(s["rows"])>20 for s in sheets))
        if len(json.dumps(result))>2_000_000:
            raise ValueError("Preview exceeds its 2 MB limit. Export a smaller file.")
        return result
    from backend.app.document_rows import units
    document_units = units(sheets) if sheets else []
    return dict(pages=pages, rows=rows, warnings=warnings, format=suffix.lstrip("."), ocr_pages=ocr_pages,
                detected_kind=detected_kind,course_mapping_text=course_mapping_text,
                skipped_rows=skipped_rows,
                requires_demo=any(re.search(r'\bSAMPLE\s*(?:/|AND|OR)?\s*TEST DATA\b|everything else is generated',t,re.I) for _,t in pages),
                ocr_issues=ocr_issues, needs_review=bool(ocr_pages), confidence_available=False,
                document_units=document_units)


def parse_file(filename, content, probe=False, force_ocr=None, languages=None, preview_only=False, import_kind=None):
    from backend.app.config import settings
    if not content or len(content) > MAX_BYTES:
        raise ValueError("Choose a nonempty file up to 15 MB.")
    # No application configuration or provider credentials are passed to the parser.
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TESSDATA_PREFIX", "LANG", "LC_ALL"}}
    env["PYTHONIOENCODING"] = "utf-8"
    config = settings()
    payload = dict(filename=filename, content=base64.b64encode(content).decode(),
        ocr=config.ocr_enabled if force_ocr is None else force_ocr, tessdata=config.ocr_tessdata_path,
        languages=languages or config.ocr_languages, probe=probe, preview_only=preview_only, import_kind=import_kind)
    try:
        result = subprocess.run([sys.executable, "-m", "backend.app.parsing"], input=json.dumps(payload),
            text=True, encoding="utf-8", capture_output=True, timeout=30, env=env,
            cwd=Path(__file__).resolve().parents[2])
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Parser exceeded its 30-second limit. Split the file or supply a simpler export.") from exc
    if result.returncode or len(result.stdout) > 12_000_000:
        raise ValueError("Parser failed safely. Check file format and resource limits.")
    output = json.loads(result.stdout)
    if "error" in output:
        raise ValueError(output["error"])
    return output


if __name__ == "__main__":
    try:
        from backend.app.process_limits import apply_limits
        apply_limits(memory_mb=1536, cpu_seconds=25)
        data = json.loads(sys.stdin.read(22_000_000))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            parsed = parse_inline(data["filename"], base64.b64decode(data["content"], validate=True), data["ocr"],
                                  data["tessdata"], data["languages"], data["probe"], data.get("preview_only",False), data.get('import_kind'))
        print(json.dumps(parsed, ensure_ascii=False))
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Malformed file or parser resource limit. Export and retry."
        print(json.dumps({"error": message[:500]}))
