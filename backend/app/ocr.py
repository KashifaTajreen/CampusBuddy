"""Local integrated Tesseract OCR. No downloads or external inference requests."""
import io
from pathlib import Path
import re
import unicodedata
import warnings
from PIL import Image, ImageOps, ImageFilter, ImageStat
import pymupdf

LANGUAGES = {"eng", "hin", "kan"}
Image.MAX_IMAGE_PIXELS = 20_000_000


def readiness(directory, languages="eng"):
    selected = languages.split("+")
    allowed = bool(selected) and set(selected) <= LANGUAGES
    available = [lang for lang in sorted(LANGUAGES) if (Path(directory) / (lang + ".traineddata")).is_file()]
    return dict(ready=allowed and set(selected) <= set(available), languages=available,
                engine="MuPDF integrated Tesseract", quality="Manual verification required")


def image(content, suffix, probe=False):
    try:
        return _image(content, suffix, probe)
    except (OSError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid, damaged or oversized image. Export a supported image and retry.") from exc


def _image(content, suffix, probe=False):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(content)) as original:
            expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}[suffix]
            if original.format != expected or original.width * original.height > 20_000_000:
                raise ValueError("Image contents/dimensions do not match the supported format limits.")
            if probe:
                original.verify()
                return None
            # Real EXIF orientation correction; arbitrary deskew is not inferred.
            gray = ImageOps.exif_transpose(original).convert("L")
    from backend.app.table_vision import rectify_paper
    gray = rectify_paper(gray)
    scale = min(3, max(1, 2400 / gray.width))
    size = (int(gray.width * scale), int(gray.height * scale))
    if size[0] * size[1] > 20_000_000:
        raise ValueError("Resized image exceeds OCR pixel limit.")
    if scale > 1:
        gray = gray.resize(size, Image.Resampling.LANCZOS)
    gray = ImageOps.autocontrast(gray)
    edge_variance = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).var[0]
    return gray.convert("RGB"), round(edge_variance, 2)


def recognize(content, suffix, directory, languages, timetable=False):
    if not readiness(directory, languages)["ready"]:
        raise ValueError("Local OCR language data is unavailable. Configure OCR_TESSDATA_PATH and installed languages, then retry.")
    prepared, sharpness = image(content, suffix)
    from backend.app.table_vision import ruling_segments, erase_rulings
    gray = prepared.convert("L")
    horizontal, vertical = ruling_segments(gray)
    prepared = erase_rulings(gray, horizontal, vertical).convert("RGB")
    result = recognize_prepared(prepared, directory, languages)
    orientation=0
    if timetable:
        def day_count(output):
            return len({re.sub(r'[^a-z]','',w[4].lower()) for w in output['words']
                        if re.sub(r'[^a-z]','',w[4].lower()) in {'monday','tuesday','wednesday','thursday','friday','saturday','sunday'}})
        if day_count(result)<3:
            # Scanned timetables can have rotated pixels but no EXIF rotation.
            # Accept an orientation only when actual weekday labels support it.
            probe=gray.copy()
            probe.thumbnail((1600,1600))
            for angle in (90,180,270):
                alternative=recognize_prepared(probe.rotate(angle,expand=True).convert('RGB'),directory,languages,allow_empty=True)
                if day_count(alternative)>=3:
                    orientation=angle
                    gray=gray.rotate(angle,expand=True)
                    horizontal,vertical=ruling_segments(gray)
                    prepared=erase_rulings(gray,horizontal,vertical).convert('RGB')
                    result=recognize_prepared(prepared,directory,languages)
                    break
    price_conflicts = []
    if (len(re.findall(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday)\b", result["text"], re.I)) < 3
            and len(re.findall(r"\brs[.,]?|₹", result["text"], re.I)) >= 3):
        from backend.app.table_vision import flatten_menu_rows
        flattened = flatten_menu_rows(gray, horizontal)
        if flattened is not None:
            alternative = recognize_prepared(flattened.point(lambda v:0 if v<130 else 255).convert('RGB'),directory,languages)
            candidates = [result,alternative]
            prices, votes = {}, {}
            for candidate_index,candidate in enumerate(candidates):
                for row in menu_rows(candidate['aligned_text'])[0]:
                    key = re.sub(r'[^a-z0-9]','',row['name'].casefold())
                    if row['price']:
                        prices.setdefault(key,set()).add(str(row['price']))
                        votes.setdefault(key,set()).add(candidate_index)
            price_conflicts = [name for name,values in prices.items() if len(values)>1 or len(votes[name])<2]
            # A layout-quality heuristic, never a confidence or correctness claim.
            result = max(candidates,key=lambda candidate:sum(bool(row['price']) for row in menu_rows(candidate['aligned_text'])[0]))
    output = dict(**result, price_conflicts=price_conflicts, orientation_degrees=orientation, width=gray.width, height=gray.height, horizontal=horizontal, vertical=vertical,
                  sharpness_proxy=sharpness, confidence_available=False, needs_review=True)
    if len(re.findall(r'\b(?:monday|tuesday|wednesday|thursday|friday|saturday)\b',result['text'],re.I)) >= 3:
        from backend.app.timetable_grid import extract_grid
        cache = {}

        def read_cell(left,top,right,bottom):
            bounds=tuple(round(v) for v in (left,top,right,bottom))
            if bounds not in cache and len(cache)<24 and right>left and bottom>top:
                crop=prepared.crop(bounds)
                if crop.width*crop.height*4 <= 4_000_000:
                    crop=crop.resize((crop.width*2,crop.height*2),Image.Resampling.LANCZOS)
                cache[bounds]=recognize_prepared(crop,directory,languages,allow_empty=True)['text']
            return cache.get(bounds,'')

        output['timetable_grid']=extract_grid(output,read_cell)
    return output


def recognize_prepared(prepared, directory, languages, allow_empty=False):
    stream = io.BytesIO()
    prepared.save(stream, format="PNG")
    pixmap = pymupdf.Pixmap(stream.getvalue())
    pdf = pixmap.pdfocr_tobytes(language=languages, tessdata=directory)
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        text = doc[0].get_text(sort=True)
        factor = prepared.width / doc[0].rect.width
        words = [[*(coordinate*factor for coordinate in word[:4]), word[4]] for word in doc[0].get_text("words")]
    if (not text.strip() and not allow_empty) or len(text) > 2_000_000:
        raise ValueError("No usable OCR text found, or output exceeds safe limits. Supply a clearer image.")
    # Preserve horizontal associations for multi-column price lists.
    lines = []
    for word in sorted(words, key=lambda w: ((w[1]+w[3])/2, w[0])):
        center = (word[1]+word[3])/2
        match = next((line for line in reversed(lines[-8:]) if abs(line[0]-center) < max(5, (word[3]-word[1])*0.45)), None)
        if match is None:
            match = [center, []]
            lines.append(match)
        match[1].append(word)
    aligned = "\n".join(" ".join(w[4] for w in sorted(line[1], key=lambda w:w[0])) for line in lines)
    return dict(text=text, aligned_text=aligned, words=words)


def menu_rows(text):
    """Item numbers and volume headings are never prices; ambiguity stays editable."""
    text = ''.join(str(unicodedata.digit(c)) if c.isdecimal() else c for c in text)
    rows, issues, seen = [], [], set()
    meal, served_on = '', ''
    for number, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r'[|_~=\\]+',' ',raw).strip(' |[]()_~;\'"�')
        if not line:
            continue
        date = re.fullmatch(r'20\d\d-\d\d-\d\d',line)
        if date:
            served_on = line
            continue
        if re.fullmatch(r'breakfast|lunch|dinner|snacks|beverages',line,re.I):
            meal = line
            continue
        if re.search(r'part\s*iculars|\bcup\b|\bml\b|menu|price list',line,re.I):
            continue
        numbered = bool(re.match(r'\d{1,3}(?:[sS]\s|[.)\s|_-])',line))
        line = re.sub(r'^\d{1,3}(?:[sS](?=\s))?\s*[.)|_=-]*\s*','',line)
        money = list(re.finditer(r'(?:₹|\brs\.?|\binr|रु\.?)\s*(\d+(?:[.,]\d{1,2})?(?:\s*[-–]\s*\d+(?:[.,]\d{1,2})?)?)',line,re.I))
        tail = re.search(r'\s+(\d+(?:\.\d{1,2})?(?:\s*[-–]\s*\d+)?)(?:\s*/-)?\s*[|_]*$',line)
        if money:
            match = money[0]
            name, price = line[:match.start()],match[1].replace(',','.')
            if len(money)>1 or re.search(r'[-–]',price) or re.search(r'\d',line[match.end():].strip(' /-|_')):
                price = ''
        elif tail:
            name, price = line[:tail.start()],tail[1]
            if re.search(r'\d',name) or re.search(r'[-–]',price):
                price = ''
        else:
            name, price = line, ''
        name = re.sub(r'[|_=~]+',' ',name).strip(' .:-/[]()')
        name = ' '.join(name.split())
        if len(name)<3 or not any(c.isalpha() for c in name):
            continue
        if not price and not numbered and (not meal or '/' in name):
            continue
        vegetarian = False if re.search(r'non[ -]?veg',name,re.I) else True if re.search(r'\bveg\b|शाकाहारी|ಸಸ್ಯಾಹಾರಿ',name,re.I) else None
        row = dict(name=name[:150],price=price,vegetarian=vegetarian,location='',category=meal or 'Uncategorized',meal=meal,served_on=served_on)
        key = (name.casefold(),str(price),meal,served_on)
        if key in seen:
            issues.append(dict(line=number,message='Identical repeated menu entry retained once; verify the duplicate against the original.'))
            continue
        seen.add(key)
        if not price:
            issues.append(dict(line=number,row=len(rows)+2,message='price: no unambiguous price; enter the amount from the original.'))
        rows.append(row)
    return rows, issues
