"""Real parsers with synthetic files; OCR contract alone is mocked explicitly."""
import io
import json
import zipfile
import pytest
import pymupdf
from backend.app.parsing import parse_inline, parse_file

MENU = {"name": "Test idli", "price": "25", "vegetarian": "true", "category": "Breakfast", "location": "Main"}


def office(suffix):
    out = io.BytesIO()
    content = "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'/>"
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", content)
        if suffix == ".docx":
            def cells(values):
                return "<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{v}</w:t></w:r></w:p></w:tc>" for v in values) + "</w:tr>"
            z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl>' + cells(MENU.keys()) + cells(MENU.values()) + "</w:tbl></w:body></w:document>")
        else:
            def cells(values):
                return "<a:tr>" + "".join(f"<a:tc><a:txBody><a:p><a:r><a:t>{v}</a:t></a:r></a:p></a:txBody></a:tc>" for v in values) + "</a:tr>"
            z.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>')
            z.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:tbl>' + cells(MENU.keys()) + cells(MENU.values()) + "</a:tbl></p:sld>")
    return out.getvalue()


def pdf_table(headers=None, values=None):
    headers, values = headers or list(MENU), values or list(MENU.values())
    doc = pymupdf.open()
    page = doc.new_page(width=max(700, 40+len(headers)*140), height=400)
    for y in (40, 80, 120):
        page.draw_line((20, y), (20+len(headers)*140, y))
    for i in range(len(headers)+1):
        page.draw_line((20+i*140, 40), (20+i*140, 120))
    for i, (header, value) in enumerate(zip(headers, values)):
        page.insert_text((25+i*140, 65), header, fontsize=8)
        page.insert_text((25+i*140, 105), str(value), fontsize=8)
    content = doc.tobytes()
    doc.close()
    return content


@pytest.mark.parametrize("suffix", [".csv", ".json", ".txt", ".md", ".docx", ".pptx", ".xlsx", ".pdf"])
def test_real_format_extraction(suffix):
    if suffix in (".docx", ".pptx"):
        data = office(suffix)
    elif suffix == ".json":
        data = json.dumps([MENU]).encode()
    elif suffix == ".xlsx":
        import pandas as pd
        stream = io.BytesIO()
        pd.DataFrame([MENU]).to_excel(stream, index=False)
        data = stream.getvalue()
    elif suffix == ".pdf":
        data = pdf_table()
    else:
        delimiter = "," if suffix == ".csv" else "|"
        data = (delimiter.join(MENU) + "\n" + delimiter.join(MENU.values())).encode()
    result = parse_file("campus"+suffix, data)
    assert result["rows"][0]["name"] == "Test idli" and result["pages"][0][0] == 1


@pytest.mark.parametrize("filename,content", [("fake.pdf", b"not a PDF"), ("fake.png", b"text"), ("fake.docx", b"PKbroken"),
    ("fake.csv", b"%PDF-1.4 binary"), ("bad.json", b'{"name":"not a list"}'), ("bad.txt", b"\xff"),
    ("wrong.csv", b"name,price\nFood,20,extra"), ("empty.txt", b""), ("large.txt", b"x"*(15*1024*1024+1))])
def test_invalid_and_mislabeled_inputs(filename, content):
    with pytest.raises(ValueError):
        parse_inline(filename, content)


def test_office_xml_entities_and_archive_members_rejected():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY x "secret">]><x>&x;</x>')
    with pytest.raises(ValueError, match="entities"):
        parse_inline("bad.docx", out.getvalue())


def test_workbook_preserves_all_sheets_and_their_locations():
    import pandas as pd
    stream = io.BytesIO()
    with pd.ExcelWriter(stream, engine="openpyxl") as writer:
        pd.DataFrame([MENU]).to_excel(writer, sheet_name="Main", index=False)
        pd.DataFrame([{**MENU, "name": "Second canteen"}]).to_excel(writer, sheet_name="Annex", index=False)
    result = parse_file("two.xlsx", stream.getvalue())
    assert len(result["rows"]) == 2 and result["rows"][1]["name"] == "Second canteen"
    assert result["pages"][1][0] == 2 and "Annex" in result["pages"][1][1]


def test_scan_and_images_report_ocr_requirement():
    doc = pymupdf.open()
    doc.new_page()
    pdf = doc.tobytes()
    pix = doc[0].get_pixmap()
    for name, data in [("scan.pdf", pdf), ("scan.png", pix.tobytes("png")), ("scan.jpg", pix.tobytes("jpeg"))]:
        with pytest.raises(ValueError, match="OCR"):
            parse_inline(name, data)
    doc.close()


def test_mocked_ocr_contract(monkeypatch):
    # Does not prove real Tesseract availability/recognition quality.
    doc = pymupdf.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    from backend.app import ocr
    monkeypatch.setattr(ocr, "recognize", lambda *_args, **_kwargs: dict(text="Recognized campus notice", aligned_text="Recognized campus notice"))
    result = parse_inline("scan.pdf", data, ocr=True)
    assert "Recognized" in result["pages"][0][1] and result["warnings"]
