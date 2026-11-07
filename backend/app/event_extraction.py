"""Local event text extraction: evidence-preserving suggestions, never invented facts."""
import re
from datetime import datetime
from backend.app.event_data import OfficialEventData

ALIASES={
 'event name':'title','event title':'title','name':'title','event':'title',
 'event category':'category','event type':'category','type':'category',
 'date':'start_date','event date':'start_date','start date':'start_date','end date':'end_date',
 'start time':'start_time','end time':'end_time','venue':'location','room':'building',
 'registration link':'registration_url','registration url':'registration_url',
 'registration deadline':'registration_deadline','last date of registration':'registration_deadline',
 'last day of registration':'registration_deadline','registration closes':'registration_deadline',
 'application deadline':'submission_deadline','submission deadline':'submission_deadline',
 'fee':'registration_fee','registration fee':'registration_fee','team size':'team_size',
 'eligibility criteria':'eligibility','organized by':'organizer','organised by':'organizer',
 'organizers':'organizer','organisers':'organizer','hosted by':'organizer','club':'organizing_club',
 'department':'hosting_department','faculty coordinator':'faculty_coordinators','student coordinator':'student_coordinators',
 'contact':'contacts','contact information':'contacts','prize pool':'prizes','prizes and rewards':'prizes',
 'certificate':'certificates','required materials':'materials','instructions':'additional_instructions',
 'event description':'description','full event description':'full_description',
 'कार्यक्रम':'title','दिनांक':'start_date','स्थान':'location','आयोजक':'organizer',
 'ಕಾರ್ಯಕ್ರಮ':'title','ದಿನಾಂಕ':'start_date','ಸ್ಥಳ':'location','ಆಯೋಜಕರು':'organizer',
}


def header(value):
    value=re.sub(r'([a-z])([A-Z])',r'\1 \2',str(value)).lower().replace('_',' ')
    return ' '.join(value.split()).strip(' :')


def field(value):
    value=header(value)
    return ALIASES.get(value,value.replace(' ','_'))


def date_value(text):
    text=str(text).strip()
    text=re.sub(r'\s*([./-])\s*',r'\1',text)
    if re.match(r'^20\d{2}-\d{2}-\d{2}(?:\b|T)',text):
        return text[:10]
    for pattern,formats in [
        (r'\b\d{1,2}[./-]\d{1,2}[./-]20\d{2}\b',['%d.%m.%Y','%d/%m/%Y','%d-%m-%Y']),
        (r'\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+20\d{2}\b',['%d %B %Y','%d %b %Y']),
        (r'\b[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s+20\d{2}\b',['%B %d %Y','%b %d %Y'])]:
        match=re.search(pattern,text,re.I)
        if match:
            value=re.sub(r'(?<=\d)(?:st|nd|rd|th)\b','',match[0],flags=re.I).replace(',','')
            for fmt in formats:
                try:
                    return datetime.strptime(value,fmt).date().isoformat()
                except ValueError:
                    pass
    return None


def deadline_value(text):
    if re.match(r'^20\d{2}-\d{2}-\d{2}T\d',str(text)):
        return text
    on=date_value(text)
    match=re.search(r'\b(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?))\b',str(text),re.I)
    return on+'T'+clock(match[1]) if on and match else on


def clock(value):
    value=str(value).lower().strip().replace('a.m.','am').replace('p.m.','pm')
    for fmt in ('%H:%M:%S','%H:%M','%I:%M %p','%I %p','%I:%M%p','%I%p'):
        try:
            return datetime.strptime(value,fmt).time().isoformat()
        except ValueError:
            pass
    return value


def normalize_row(raw,category=None):
    output={}
    for key,value in raw.items():
        name=field(key)
        if name not in OfficialEventData.model_fields:
            continue
        if value is None or str(value).strip().casefold() in {'','na','n/a','none','null','-','—'}:
            output[name]=None
        elif name in ('start_date','end_date'):
            output[name]=date_value(value) or str(value).strip()
        elif name in ('registration_deadline','submission_deadline'):
            output[name]=deadline_value(value) or str(value).strip()
        elif name in ('start_time','end_time'):
            output[name]=clock(value)
        elif name=='registration_fee':
            match=re.match(r'^\s*(₹|rs\.?|inr|\$|usd)?\s*(\d+(?:\.\d+)?)\s*(?:/-)?(?:\s*[^\d]*)?$',str(value),re.I)
            output[name]=float(match[2]) if match else value
            if match and match[1]:
                output['fee_currency']='USD' if match[1].lower() in ('$','usd') else 'INR'
        else:
            output[name]=value
    output['category']=output.get('category') or category or 'Other'
    return output


def suggest(pages,structured,category=None):
    text='\n\n'.join(t for _,t in pages).strip()
    selected=[row for row in structured if any(field(k)=='title' for k in row) and any(
        value is not None and str(value).strip().casefold() not in {'','na','n/a','none','null'} for key,value in row.items() if not str(key).startswith('__'))]
    rows=[normalize_row(row,category) for row in selected]
    if rows:
        for row,raw in zip(rows,selected):
            row.setdefault('full_description','\n'.join(f'{k}: {v}' for k,v in raw.items() if not str(k).startswith('__') and v is not None))
        return rows,[]
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    data={}
    for line in lines:
        pair=re.match(r'^([^:]{2,60})\s*:\s*(.+)$',line)
        if pair and field(pair[1]) in OfficialEventData.model_fields:
            values=normalize_row({pair[1]:pair[2]})
            if field(pair[1])!='category':
                values.pop('category',None)
            data.update(values)
    if not data.get('title'):
        data['title']=next((line for line in lines if 4<=len(line)<=180 and len(re.findall(r'[A-Za-z\u0900-\u097f\u0c80-\u0cff]',line))>=4
            and not re.match(r'^(?:official notice|campusos|date|time|venue|department|registration|organized|organised|fee|contact)\b',line,re.I)),None)
    data['full_description']=text or None
    data.setdefault('description','\n'.join(line for line in lines if line!=data.get('title'))[:3000] or None)
    data['category']=category or (data.get('category') if data.get('category')!='Other' else None) or 'Other'
    links=[]
    for line in lines:
        for match in re.finditer(r'https://[^\s<>]+',line):
            url=match[0].rstrip('.,;)')
            links.append(url)
            if not data.get('registration_url') and re.search(r'regist|\bapply\b',line,re.I):
                data['registration_url']=url
    if links:
        data.setdefault('external_links',list(dict.fromkeys(links)))
    warnings=[]
    if data.get('title') and re.search(r'college|university|autonomous|accredited|institute',data['title'],re.I) and re.search(r'\bPRESENTS\b',text):
        data['title']=None
        warnings.append('The decorative event title was not reliably read. Enter its exact title from the original poster; the institution heading was not used as the event title.')
    for number,line in enumerate(lines):
        nearby=' '.join(lines[max(0,number-1):number+2])
        if re.search(r'\b(?:event date|date|दिनांक|ದಿನಾಂಕ)\b',nearby,re.I) and not re.search('deadline|registration|submission',nearby,re.I):
            on=date_value(nearby)
            if on:
                data.setdefault('start_date',on)
            else:
                warnings.append('A date could not be read with an explicit year. Review it; no date was assumed.')
        if re.search(r'\btime\b|\d:\d',nearby,re.I):
            cleaned=re.sub(r'(?<=\d)(TIME)(?=AM|PM)','',line,flags=re.I)
            match=re.search(r'(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?))\s*[-–—]\s*(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?))',cleaned,re.I)
            if match:
                data['start_time'],data['end_time']=clock(match[1]),clock(match[2])
                if data.get('start_date'):
                    data['end_date']=data['start_date']  # One explicitly dated time range.
        if not data.get('team_size') and re.search(r'team|members?',nearby,re.I):
            team=re.search(r'\b\d+\s*[-–]\s*\d+\s*members?\b',line,re.I)
            if team:
                data['team_size']=team[0]
        if not data.get('location'):
            venue=None
            if re.search(r'\bVENUE\b',nearby,re.I) and not re.search(r'\bTIME\b|TEAM\s*SIZE',line,re.I):
                venue=re.search(r'\bVENUE\s*[:\-]?\s*([A-Za-z][A-Za-z ,]{3,80}?)(?=\s+(?:TEAM|SIZE)|\s*\d|$)',line,re.I)
                if not venue:
                    venue=re.search(r'\b(?:AM|PM)\s+([A-Za-z][A-Za-z ,]{3,80}?)(?=\s+\d+\s*[-–]\s*\d+|$)',line,re.I)
            if venue:
                data['location']=venue[1].strip()
        if not data.get('organizer') and re.search(r'\bPRESENTS\b',nearby,re.I):
            organizer=re.search(r'\b([A-Z]{2,10}\s*[x×]\s*[A-Z]{2,10})\b',line)
            if organizer:
                data['organizer']=organizer[1]
    for key in ('start_date','end_date','registration_deadline','submission_deadline'):
        if data.get(key) and isinstance(data[key],str):
            data[key]=(deadline_value(data[key]) if 'deadline' in key else date_value(data[key])) or data[key]
    if not data.get('start_date'):
        warnings.append('No event date was reliably identified. Leaving it blank is allowed and displays Date not announced.')
    else:
        warnings=[w for w in warnings if not w.startswith('A date could not')]
    return [data],warnings


def poster_ocr(content,suffix,directory,languages):
    from PIL import ImageOps,ImageStat
    from backend.app.ocr import image,recognize_prepared
    prepared,_=image(content,suffix)
    candidates=[prepared]
    gray=prepared.convert('L')
    if ImageStat.Stat(gray).mean[0]<130:
        candidates.insert(0,ImageOps.invert(gray).convert('RGB'))
    results=[recognize_prepared(candidate,directory,languages,allow_empty=True) for candidate in candidates]
    result=max(results,key=lambda r:len(re.findall(r'[A-Za-z\u0900-\u097f\u0c80-\u0cff]{3,}',r['text'])))
    # Decorative posters are not one uniform page of prose. Read overlapping
    # horizontal panels as well, without using hardcoded coordinates or content.
    # Bound work so OCR remains inside the parser's existing time/pixel limits.
    if prepared.height>prepared.width*1.2:
        panel_height=max(300,round(prepared.width*.38))
        stride=round(panel_height*.85)
        strips=[]
        canvas=ImageOps.invert(gray) if ImageStat.Stat(gray).mean[0]<130 else gray
        for top in list(range(0,prepared.height,stride))[:16]:
            crop=canvas.crop((0,top,prepared.width,min(prepared.height,top+panel_height)))
            if crop.height<80:
                continue
            crop=ImageOps.expand(crop,border=20,fill=255)
            recognized=recognize_prepared(crop.convert('RGB'),directory,languages,allow_empty=True)
            strips.append(recognized['aligned_text'])
        seen=set()
        lines=[]
        for line in ('\n'.join(strips)+'\n'+result['aligned_text']).splitlines():
            line=' '.join(line.split())
            if line and line.casefold() not in seen:
                seen.add(line.casefold())
                lines.append(line)
        result={**result,'text':'\n'.join(lines)}
    qr_url=None
    qr_supported=False
    try:
        import cv2
        import numpy as np
        qr_supported=True
        value,_,_=cv2.QRCodeDetector().detectAndDecode(np.asarray(prepared))
        if value:
            from backend.app.event_data import https_url
            try:
                qr_url=https_url(value)
            except ValueError:
                pass
    except ImportError:
        pass
    return dict(text=result['text'],qr_url=qr_url,qr_supported=qr_supported)
