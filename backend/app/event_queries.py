"""Deterministic event facts plus evidence-scoped detail retrieval."""
import re
from datetime import timedelta

EVENT_WORDS=r'\bevents?\b|workshop|hackathon|seminar|cultural|sports|tournament|buildathon|recruitment|\bclub\b|music|dance|conference|webinar|guest lecture|startup|innovation|\bctl\b|certification|training|competition|technical talk'
DETAILS=r'registr|deadline|organis|organiz|\bfee\b|\bprize\b|eligib|participat|team size|\brules?\b|agenda|certificate|materials|where|venue|contact|about|tell me|submission|application|\bwhen\b|\bdate\b|\btime\b'


def translate_event_terms(q):
    for source,target in {'सांस्कृतिक':' cultural ','खेल':' sports ','पंजीकरण':' registration ', 'अंतिम तिथि':' deadline ',
        'शुल्क':' fee ','नियम':' rules ','पात्रता':' eligibility ','आयोजक':' organizer ',
        'ಸಾಂಸ್ಕೃತಿಕ':' cultural ','ಕ್ರೀಡಾ':' sports ','ನೋಂದಣಿ':' registration ','ಕೊನೆಯ ದಿನಾಂಕ':' deadline ',
        'ಶುಲ್ಕ':' fee ','ನಿಯಮ':' rules ','ಅರ್ಹತೆ':' eligibility ','ಆಯೋಜಕರು':' organizer '}.items():
        q=q.replace(source,target)
    return q


def matches(q,context):
    if re.search(r'attend|clash|overlap|conflict|without missing',q):
        return False
    if not re.search(EVENT_WORDS,q) and re.search(r'professor|faculty.*office|timetable|my class|canteen|menu|inbox|my email',q):
        return False
    return bool(re.search(EVENT_WORDS,q) or (context.get('event_ids') and re.search(DETAILS,q)))


def answer(router,question,context,tools,normalized=None):
    from backend.app.router import parse_date,has_explicit_date
    q=normalized or question.lower()
    today=router.c.local_now().date()
    detail=bool(re.search(DETAILS,q))
    anchors=context.get('event_ids',[])
    events=[]
    if detail and anchors and re.search(r'\bthis\b|\bthat\b|\bit\b|registration|team size|prize|rules|eligib|organis|organiz',q):
        for item in anchors[:10]:
            try:
                row=router.c.event_record(item['id'])
                events.append(router.c.serialize(row))
            except Exception as exc:
                from fastapi import HTTPException
                if not isinstance(exc,HTTPException):
                    raise
    if not events:
        term=next((word for word in ['hackathon','workshop','cultural','sports','recruitment','seminar','webinar','conference','startup','training','competition','music'] if word in q),'')
        explicit=has_explicit_date(q)
        start=parse_date(q,today) if explicit else None
        days=7-today.weekday() if 'this week' in q else 14
        if 'this week' in q:
            start=today
        elif 'next week' in q:
            start=today+timedelta(days=7-today.weekday())
            days=7
        if explicit:
            days=1
        future=bool(re.search('upcoming|coming up|this week|next week|tomorrow|today',q))
        status=('past' if 'past' in q else 'ongoing' if 'ongoing' in q else 'undated' if re.search('undated|date not announced',q)
                else 'all' if detail or explicit or not future else 'upcoming')
        events=tools.call('list_campus_events',query=term,start=str(start) if start else None,days=days,status=status,
            include_undated=detail or not future)
        if detail:
            exact=[e for e in events if e['title'].lower() in q]
            if exact:
                events=exact
    context['domain']='events'
    context['event_ids']=[{'id':e['id'],'title':e['title']} for e in events[:10]]
    result=dict(route='Published events SQL',kind='events',rows=events,answer='',synthesis=None)
    if not events:
        result['answer']='No matching published events were found in your authorized scope. Undated events are listed separately under Date not announced.'
        return result
    if not detail:
        result['answer']='Published events matching your date/category filters. Dates not announced are never assumed.'
        return result
    if len(events)>1:
        result['answer']='Which event do you mean? Please use one of these published event titles.'
        return result
    event=events[0]
    fields=None
    missing_detail=None
    if re.search('submission|application',q) and 'deadline' in q:
        fields=['submission_deadline']
    elif 'deadline' in q:
        fields=['registration_deadline']
    elif re.search(r'\bwhen\b|\bdate\b|\btime\b',q):
        fields=['start_date','end_date','start_time','end_time']
    elif re.search(r'\bfee\b|cost|price',q):
        fields=['registration_fee','fee_currency']
    elif re.search('registration.*link|registration.*url|link.*registr',q):
        fields=['registration_url']
    elif re.search('organis|organiz|coordinator',q):
        fields=['organizer','hosting_department','organizing_club','faculty_coordinators','student_coordinators']
    elif re.search(r'where|venue|location',q):
        fields=['location','building','campus','mode','meeting_url']
    elif re.search('team size',q):
        fields=['team_size']
    elif re.search('prize|reward',q):
        fields=['prizes']
    elif re.search('eligib|participat|second.year',q):
        fields=['eligibility']
    elif re.search('rules|requirements',q):
        fields=['rules']
    elif 'agenda' in q:
        fields=['agenda']
    elif 'certificate' in q:
        fields=['certificates']
    elif 'contact' in q or 'email' in q:
        fields=['contacts']
    if fields:
        facts=[f"{f.replace('_',' ').capitalize()}: {event[f]}" for f in fields if event.get(f) is not None]
        if facts:
            result['answer']=event['title']+' — '+'; '.join(facts)+'.'
            return result
        # Specific missing facts must not be guessed by a model, even if another
        # event in the same source happens to contain them.
        missing_detail=fields[0]
        if missing_detail in ('registration_deadline','submission_deadline','registration_fee','registration_url','meeting_url'):
            result['answer']=f"{event['title']}: {missing_detail.replace('_',' ')} is not available in the published event information."
            return result
    evidence=tools.call('search_documents',query=event['title']+' '+question,event_id=event['id'])
    if missing_detail:
        patterns={'eligibility':r'eligib|participat|second.year|2nd.year','rules':r'\brules?\b|requirements',
            'team_size':r'team|members','prizes':r'prize|reward','organizer':r'organiz|organis|hosted|coordinator',
            'location':r'venue|location|building|online','contacts':r'contact|email|phone',
            'agenda':r'agenda|schedule','certificates':r'certificat'}
        pattern=patterns.get(missing_detail,re.escape(missing_detail.replace('_',' ')))
        hits=[hit for hit in evidence['hits'] if not hit.get('suspicious') and re.search(pattern,hit['text'],re.I)]
        if not hits:
            result['answer']=f"{event['title']}: {missing_detail.replace('_',' ')} is not available in the published source."
            return result
        excerpts=[]
        for hit in hits[:2]:
            lines=hit['text'].splitlines()
            index=next(i for i,line in enumerate(lines) if re.search(pattern,line,re.I))
            excerpts.append('\n'.join(lines[index:index+4]))
        return dict(route='Authorized event detail RAG',kind='passages',rows=hits,
                    answer='Published event evidence:\n\n'+'\n\n'.join(excerpts),synthesis=None)
    result.update(route='Event SQL and authorized event RAG',kind='passages',rows=evidence['hits'],answer=evidence['answer'])
    if evidence['hits'] and not evidence['conflicts']:
        generated=router.generate(question,evidence['hits'])
        if generated and not generated.insufficient:
            result.update(answer=generated.answer,synthesis=generated)
    return result
