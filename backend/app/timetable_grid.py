"""Interpret day-by-time grids while retaining uncertain source text for review."""
import re
from statistics import median

DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']


def clusters(values, distance=5):
    groups = []
    for value in sorted(set(values)):
        if groups and value-groups[-1][-1] <= distance:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [median(group) for group in groups]


def cell_text(words, left, top, right, bottom):
    selected = [w for w in words if left < (w[0]+w[2])/2 < right and top < (w[1]+w[3])/2 < bottom]
    lines = []
    for word in sorted(selected, key=lambda w: ((w[1]+w[3])/2, w[0])):
        center = (word[1]+word[3])/2
        match = next((line for line in reversed(lines[-5:]) if abs(line[0]-center) < max(4, (word[3]-word[1])*.4)), None)
        if match is None:
            match = [center, []]
            lines.append(match)
        match[1].append(word)
    return '\n'.join(' '.join(w[4] for w in sorted(line[1], key=lambda w:w[0])) for line in lines)


def time_range(text):
    values = re.findall(r'(?<!\d)(\d{1,2})\s*[:;.]\s*(\d{2})(?!\d)', text)
    if len(values) != 2:
        return None
    minutes = []
    for hour, minute in values:
        hour, minute = int(hour), int(minute)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            minutes.append(None)
            continue
        # A campus daytime grid starting at nine uses 1..7 for afternoon.
        if 1 <= hour <= 7:
            hour += 12
        minutes.append(hour*60+minute)
    if all(value is not None for value in minutes) and not minutes[0] < minutes[1]:
        return None
    return tuple(f'{value//60:02}:{value%60:02}' if value is not None else '' for value in minutes)


def code_key(value):
    value = re.sub(r'[^A-Za-z0-9]', '', value).replace('l','I').upper()
    if re.match(r'^[A-Z]1\d', value):
        value = value[0] + 'I' + value[2:]
    match = re.fullmatch(r'([A-Z]{2,3}?)([\dILGO]{2,4})([A-Z1]{1,3})', value)
    if not match:
        return value
    prefix, number, suffix = match.groups()
    return prefix + number.translate(str.maketrans('ILGO', '1160')) + suffix.replace('1','I')


def edit_distance(a, b):
    previous = list(range(len(b)+1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(min(current[-1]+1, previous[j]+1, previous[j-1]+(left != right)))
        previous = current
    return previous[-1]


def resolve_code(value, known):
    key = code_key(value)
    if key in known:
        return key
    matches = [code for code in known if edit_distance(key, code) == 1]
    return matches[0] if len(matches) == 1 else None


def legend_records(result, top, horizontal, vertical, read_cell=None):
    width, height = result['width'], result['height']
    bottom = max((y for y,_,_ in horizontal), default=height)
    xs = clusters([x for x,a,b in vertical if min(b,bottom)-max(a,top) > (bottom-top)*.65])
    if len(xs) < 2:
        return {}, {}, cell_text(result['words'],0,top,width,height)
    ys = clusters([y for y,left,right in horizontal if y >= top-2 and right-left > width*.6])
    if not ys or ys[0] > top+5:
        ys.insert(0,top)
    cells = []
    for a,b in zip(xs,xs[1:]):
        for y,z in zip(ys,ys[1:]):
            text = cell_text(result['words'],a,y,b,z)
            if not text or 'Course Code' in text:
                continue
            candidates = [text]
            if read_cell:
                candidates.append(read_cell(a+3,y+3,b-3,z-3))
            cells.append(candidates)
    # Some PDFs put the entire course legend in a single tall table cell.
    # Split its logical entries before resolving names/faculty; otherwise the
    # first professor accidentally absorbs every subsequent course and person.
    entries=[]
    for candidates in cells:
        split_candidates=[]
        for candidate in candidates:
            current=[]
            venue_started=False
            for line in candidate.splitlines():
                first=re.match(r'^\s*([A-Za-z][A-Za-z0-9]{4,9})\b',line)
                new_course=bool(first and re.fullmatch(r'[A-Z]{2,3}\d{3}[A-Z]{1,3}',code_key(first[1])))
                # A code-only continuation belongs to the preceding combined
                # elective entry, not a new course with an empty description.
                tail=re.sub(r'\b[A-Za-z]{2,3}\d{3}[A-Za-z]{1,3}\b','',line[first.end():]) if first else ''
                new_course=new_course and bool(re.search(r'[A-Za-z]{3,}',tail))
                venue_header=bool(re.search(r'\bVenue\b',line,re.I))
                if ((new_course and not venue_started) or venue_header) and current:
                    split_candidates.append(['\n'.join(current)])
                    current=[]
                venue_started=venue_started or venue_header
                current.append(line)
            if current:
                split_candidates.append(['\n'.join(current)])
        # Retain candidate voting for the original one-entry legend cells.
        entries.extend(split_candidates if len(split_candidates)>len(candidates) else [candidates])
    cells=entries
    tokens = re.findall(r'\b[A-Za-z][A-Za-z0-9]{5,9}\b',result['text'])
    tokens += [t for candidates in cells for text in candidates for t in re.findall(r'\b[A-Za-z][A-Za-z0-9]{5,9}\b',text)]
    known = {code_key(t) for t in tokens if re.fullmatch(r'[A-Z]{2,3}\d{3}[A-Z]{1,3}',code_key(t))}
    records, venues = {}, {}
    legend = []
    for candidates in cells:
        def quality(text):
            first = re.search(r'\b([A-Za-z][A-Za-z0-9]{5,9})\b',text)
            key = resolve_code(first[1],known) if first else None
            return (bool(key), bool(first and key == first[1].upper()), -text.count('='))
        text = max(candidates,key=quality)
        legend.append(text)
        if re.search(r'\bvenue\b',text,re.I):
            # Venue statements are separate evidence, never the default lecture room.
            for line in '\n'.join(candidates).splitlines():
                line=re.sub(r'^.*?\bVenue\b','',line,flags=re.I).strip(' ;|')
                match = re.fullmatch(r'\s*([A-Za-z][A-Za-z0-9]{5,9}|DTL)(?:\s+Lab)?\s*[-–:]\s*(.+)',line,re.I)
                if match:
                    key = resolve_code(match[1],known) or match[1].upper()
                    venues[key] = re.sub(r'\s*[;|]\s*',' ',match[2]).strip()
            continue
        match = re.search(r'\b([A-Za-z][A-Za-z0-9]{5,9})\b',text)
        key = resolve_code(match[1],known) if match else None
        if not key:
            continue
        description = text[match.end():].strip(' /-–|')
        faculty = re.search(r'\b(?:Dr\.?|Prof\.?)\s*',description,re.I)
        name = None
        if faculty:
            name = ' '.join(re.sub(r'[=|]+',' ',description[faculty.start():]).split()).strip(' -')
            description = description[:faculty.start()].strip(' -–')
        codes = [key]
        for other in sorted(known):
            if other in description:
                codes.append(other)
                description = description.replace(other,'')
        description = re.sub(r'\(\s*faculty\s+not\s+listed\s*\)','',description,flags=re.I)
        description = ' '.join(description.split()).strip(' /-–|')
        for code in codes:
            if code not in records or len(description)>len(records[code]['subject']):
                records[code] = dict(subject=description, faculty_name=name)
    return records, venues, '\n\n'.join(legend)


def extract_grid(result, read_cell=None):
    words = result.get('words', [])
    day_words = [(DAYS.index(re.sub(r'[^a-z]', '', w[4].lower())), w) for w in words
                 if re.sub(r'[^a-z]', '', w[4].lower()) in DAYS]
    if len({d for d, _ in day_words}) < 3:
        return None
    width = result['width']
    horizontal, vertical = result.get('horizontal', []), result.get('vertical', [])
    day_x = median((w[0]+w[2])/2 for _,w in day_words)
    ys = clusters([y for y,left,right in horizontal
                   if left < day_x < right and right-left > width*.07])
    issues, rows = [], []
    if not ys:
        return dict(rows=[], issues=[dict(message='Timetable detected but grid borders are unclear; review a clearer image or supply corrected rows.')], detected_kind='timetable')
    first_day_y = min((w[1]+w[3])/2 for _,w in day_words)
    above = [y for y in ys if y < first_day_y]
    if len(above) < 2:
        return dict(rows=[], issues=[dict(message='Timetable time-header row could not be located.')], detected_kind='timetable')
    header_top, header_bottom = above[-2:]
    xs = clusters([x for x,top,bottom in vertical
                   if min(bottom,header_bottom)-max(top,header_top) > (header_bottom-header_top)*.65])
    slots = []
    for left,right in zip(xs, xs[1:]):
        label = cell_text(words,left,header_top,right,header_bottom)
        interval = time_range(label)
        if read_cell and not re.search('break|lunch|short',label,re.I) and left > xs[0]+10 and (not interval or not all(interval)):
            retry = time_range(read_cell(left+3,header_top+3,right-3,header_bottom-3))
            if retry and all(retry):
                interval=retry
        if interval or re.search('break|lunch|short',label,re.I):
            slots.append(dict(left=left,right=right,interval=interval,break_slot=bool(re.search('break|lunch|short',label,re.I)),text=label))
        elif left > xs[0]+10:
            # Retain an uncertain time column: never silently omit its classes.
            slots.append(dict(left=left,right=right,interval=None,break_slot=False,text=label))
    if not slots:
        return dict(rows=[],issues=[dict(message='Timetable detected; verify the time-slot headers before importing.')],detected_kind='timetable')
    all_text = result.get('text','')
    start_match = re.search(r'\b(\d{2})[./](\d{2})[./](20\d{2})\b', all_text)
    effective = f'{start_match[3]}-{start_match[2]}-{start_match[1]}' if start_match else ''
    room_match = re.search(r'Classroom\s+No[.,:]?\s*([^\n|]+)', all_text, re.I)
    room = room_match[1].strip()[:100] if room_match else ''
    for day, word in sorted(day_words, key=lambda item:item[0]):
        center = (word[1]+word[3])/2
        top_candidates, bottom_candidates = [y for y in ys if y<center], [y for y in ys if y>center]
        if not top_candidates or not bottom_candidates:
            issues.append(dict(message=f'{DAYS[day].title()}: row borders are unclear.'))
            continue
        top,bottom = top_candidates[-1],bottom_candidates[0]
        index = 0
        while index < len(slots):
            slot = slots[index]
            if slot['break_slot']:
                index += 1
                continue
            end = index
            while end+1 < len(slots) and not slots[end+1]['break_slot']:
                boundary = slots[end]['right']
                # Internal cell borders can be shifted from the header border.
                tolerance = max(5,(slots[end]['right']-slots[end]['left'])*.3)
                coverage = max((min(b,bottom)-max(a,top) for x,a,b in vertical if abs(x-boundary)<tolerance),default=0)
                if coverage > (bottom-top)*.45:
                    break
                end += 1
            text = cell_text(words,slot['left'],top,slots[end]['right'],bottom).strip(' |')
            if text and not re.fullmatch(r'[-—\s]+',text):
                batches = list(re.finditer(r'Batch\s*[-–.]?\s*([12LlI]|\d+)\s*[-–.]?\s*(.*?)(?=Batch\s*[-–.]?\s*[\dLlI]|$)',text,re.I|re.S))
                portions = [(m[1],m[2]) for m in batches] or [('',text)]
                for batch, subject in portions:
                    subject = ' '.join(subject.split()).strip(' |.-')
                    faculty = re.search(r'\[([^\]]+)\]',subject)
                    base = re.sub(r'\[[^\]]*\]','',subject).strip()
                    row = dict(day=day,start_time=slot['interval'][0] if slot['interval'] else '',
                        end_time=slots[end]['interval'][1] if slots[end]['interval'] else '',
                        subject=base,course_code=base if re.fullmatch(r'[A-Za-z0-9 /]+',base) and re.search(r'\d{3}',base) else '',
                        faculty_code='',faculty_name=faculty[1] if faculty else '',room=room,
                        lab_batch='',effective_from=effective,effective_to='',__source_cell=text,
                        __cell_bounds=[slot['left'],top,slots[end]['right'],bottom])
                    rows.append(row)
                    if not row['start_time'] or not row['end_time']:
                        issues.append(dict(row=len(rows)+1,message=f'{DAYS[day].title()}: unreadable time header for {base}; enter its exact start/end time.'))
            index = end+1
    last_day = max((w[1]+w[3])/2 for _,w in day_words)
    legend_top = next((y for y in ys if y > last_day), last_day)
    catalog,venues,legend = legend_records(result,legend_top,horizontal,vertical,read_cell)
    known = set(catalog)
    for row in rows:
        raw = row['subject']
        tokens = re.findall(r'[A-Za-z][A-Za-z0-9]{4,9}',raw)
        codes = [resolve_code(t,known) for t in tokens if re.search(r'\d',t)]
        codes = list(dict.fromkeys(code for code in codes if code))
        if '/' in raw and read_cell:
            retried=read_cell(*row['__cell_bounds'])
            for token in re.findall(r'[A-Za-z][A-Za-z0-9]{4,9}',retried):
                code=resolve_code(token,known)
                if code and code not in codes:
                    codes.append(code)
        is_lab = bool(re.search(r'\blab\b',raw,re.I))
        if not codes and is_lab:
            abbreviation = re.match(r'([A-Za-z]+)\s+Lab',raw,re.I)
            if abbreviation:
                candidates = [code for code,entry in catalog.items() if code in venues and
                    ''.join(w[0] for w in re.findall('[A-Za-z]+',entry['subject']) if w.lower() not in {'and','of','for','the'}).upper().endswith(abbreviation[1].upper())]
                if len(candidates)==1:
                    codes=candidates
        if not codes and re.search(r'\d',raw) and read_cell:
            retried=read_cell(*row['__cell_bounds'])
            codes=list(dict.fromkeys(resolve_code(t,known) for t in re.findall(r'[A-Za-z][A-Za-z0-9]{4,9}',retried) if re.search(r'\d',t)))
            codes=[c for c in codes if c]
        if codes:
            row['course_code']='/'.join(codes)
            row['subject']=' / '.join(dict.fromkeys(catalog[c]['subject'] for c in codes))[:200]
            if is_lab and not re.search(r'\blab\b',row['subject'],re.I):
                row['subject'] += ' Lab'
            row['faculty_name']=catalog[codes[0]]['faculty_name'] if len(codes)==1 else None
            if is_lab or re.search(r'\blab\b',row['subject'],re.I):
                room_key=codes[0]
                if room_key not in venues:
                    acronym=''.join(w[0] for w in row['subject'].split()).upper()
                    room_key=next((k for k in venues if len(k)<=4 and acronym.endswith(k)),room_key)
                row['room']=venues.get(room_key)
        elif re.search(r'\d',raw):
            row['__unresolved_subject']=raw
            row['__review_message']='Course code could not be resolved against the source legend. Correct course_name and course_code before publication.'
        elif is_lab:
            row['faculty_name']=None
            row['room']=None
            row['__unresolved_subject']=raw
            row['__review_message']='Lab could not be resolved against the source legend. Verify its course and venue before publication.'
        if is_lab and row.get('faculty_name') is None:
            row['faculty_name']=None  # Source abbreviations are retained in __source_cell, not expanded into invented people.
        row.pop('__cell_bounds',None)
        if row.get('room'):
            row['room']=' '.join(re.sub(r'\s*-\s*','-',row['room']).split())
            row['room']=re.sub(r'\bMCA\s+(\d+)\b',r'MCA-\1',row['room'])
    return dict(rows=rows,issues=issues,detected_kind='timetable',course_mapping_text=legend)
