"""Reproducible real-image OCR evaluation using generated, non-institutional menus."""
import io
import json
from pathlib import Path
import tempfile
import time
import unicodedata
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import pymupdf
from backend.app.parsing import parse_file

ROOT = Path(__file__).resolve().parents[1]
ENGLISH = [("Idli", 30), ("Plain Dosa", 45), ("Tea", 12)]


def printed_menu(currency="Rs", columns=False):
    canvas = Image.new("RGB", (1500, 1000), "white")
    draw = ImageDraw.Draw(canvas)
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    if not font_path.exists():
        font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    font = ImageFont.truetype(str(font_path), 48)
    draw.text((60, 45), "Campus Canteen Menu", font=font, fill="black")
    draw.text((60, 135), "Breakfast", font=font, fill="black")
    for index, (name, price) in enumerate(ENGLISH):
        x, y = (60, 260+index*110) if not columns else (60+(index%2)*750, 260+(index//2)*110)
        draw.text((x, y), f"{name}  {currency} {price}", font=font, fill="black")
    return canvas


def indic_menu(language, rows):
    document = pymupdf.open()
    page = document.new_page(width=600, height=500)
    font = "Hindi.ttf" if language == "hin" else "Kannada.ttf"
    css = f"@font-face{{font-family:fixture;src:url({font});}} body{{font-family:fixture;font-size:30px}} td{{padding:15px}}"
    html = "<table>" + "".join(f"<tr><td>{name}</td><td>{price}</td></tr>" for name, price in rows) + "</table>"
    page.insert_htmlbox(pymupdf.Rect(20,20,580,480), html, css=css, archive=pymupdf.Archive(str(ROOT / ".scratch/fonts")))
    payload = page.get_pixmap(dpi=160).tobytes("png")
    document.close()
    return payload


def normalized(value):
    return "".join(c.casefold() for c in value if c.isalnum() or unicodedata.category(c).startswith("M"))


def evaluate():
    work = Path(tempfile.mkdtemp(prefix="real-ocr-", dir=ROOT / ".scratch"))
    cases = []
    for name, image in [("clear", printed_menu()), ("rupee", printed_menu("₹")),
                        ("columns", printed_menu(columns=True)),
                        ("blurred", printed_menu().resize((450,300)).resize((1500,1000)).filter(ImageFilter.GaussianBlur(1.5)))]:
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        cases.append((name+".png", stream.getvalue(), "eng", ENGLISH))
    for suffix, format_name in (("jpg", "JPEG"), ("webp", "WEBP")):
        stream = io.BytesIO()
        printed_menu().save(stream, format=format_name)
        cases.append(("clear."+suffix, stream.getvalue(), "eng", ENGLISH))
    for language, gold in [("hin", [("इडली",30),("दोसा",45)]), ("kan", [("ಇಡ್ಲಿ",30),("ದೋಸೆ",45)])]:
        cases.append((language+".png", indic_menu(language, gold), "eng+"+language, gold))
    image_bytes = cases[0][1]
    for mixed in (False, True):
        document = pymupdf.open()
        if mixed:
            document.new_page().insert_text((50,50), "Selectable campus notice. Verify menu items against the original image.")
        page = document.new_page(width=750,height=500)
        page.insert_image(page.rect, stream=image_bytes)
        cases.append(("mixed.pdf" if mixed else "scanned.pdf", document.tobytes(), "eng", ENGLISH))
        document.close()
    results = []
    for filename, content, languages, gold in cases:
        (work / filename).write_bytes(content)
        started = time.perf_counter()
        parsed = parse_file(filename, content, force_ocr=True, languages=languages)
        predicted = {normalized(row["name"]): row for row in parsed["rows"]}
        matches = sum(normalized(name) in predicted for name, _ in gold)
        prices = sum(normalized(name) in predicted and str(predicted[normalized(name)]["price"]) == str(price) for name, price in gold)
        results.append(dict(file=str(work/filename), language=languages, expected=gold, extracted=parsed["rows"],
            item_recall=matches/len(gold), item_precision=matches/max(1,len(predicted)), price_accuracy=prices/len(gold),
            ocr_pages=parsed["ocr_pages"], needs_review=parsed["needs_review"],
            seconds=round(time.perf_counter()-started,3)))
    report = dict(scope="Real raster files and scanned/mixed PDFs generated from synthetic printed menus; not cafeteria photographs",
                  engine="MuPDF integrated Tesseract, tessdata_fast 4.1.0", cases=results,
                  mean_item_recall=sum(c["item_recall"] for c in results)/len(results),
                  mean_price_accuracy=sum(c["price_accuracy"] for c in results)/len(results),
                  confidence_calibrated=False, manual_review_required=True)
    (ROOT / "artifacts/ocr-real-evaluation.json").write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    return report


if __name__ == "__main__":
    result = evaluate()
    print(json.dumps({"cases":len(result["cases"]),"item_recall":result["mean_item_recall"],"price_accuracy":result["mean_price_accuracy"]}))
