"""Reviewed official events. Legacy examination/calendar payloads stay unchanged."""
import json
import re
from datetime import date,datetime,time
from typing import Any,Literal
from urllib.parse import urlparse

from pydantic import Field,field_validator,model_validator
from backend.app.schemas import Strict,DocumentPage


def https_url(value):
    if value is None or value=='':
        return None
    parsed=urlparse(str(value))
    if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
            or any(c.isspace() for c in str(value))):
        raise ValueError('Use a complete HTTPS URL without credentials or spaces.')
    return str(value)


class EventMilestone(Strict):
    label:str=Field(min_length=1,max_length=120)
    on:date|datetime


class OfficialEventData(Strict):
    event_schema:Literal['official-v1']='official-v1'
    title:str=Field(min_length=4,max_length=300)
    category:str=Field(default='Other',min_length=1,max_length=80)
    description:str|None=Field(default=None,max_length=3000)
    full_description:str|None=Field(default=None,max_length=100000)
    department:str=Field(default='All',min_length=1,max_length=60)
    semester:int|None=Field(default=None,ge=1,le=12)
    section:str=Field(default='All',min_length=1,max_length=20)
    kind:Literal['event','workshop','exam','deadline']='event'
    tags:str|None=Field(default=None,max_length=500)
    start:datetime|None=None
    end:datetime|None=None
    start_date:date|None=None
    end_date:date|None=None
    start_time:time|None=None
    end_time:time|None=None
    registration_deadline:date|datetime|None=None
    submission_deadline:date|datetime|None=None
    important_dates:list[EventMilestone]|None=Field(default=None,max_length=30)
    location:str|None=Field(default=None,max_length=500)
    building:str|None=Field(default=None,max_length=200)
    campus:str|None=Field(default=None,max_length=200)
    mode:Literal['online','offline','hybrid']|None=None
    meeting_url:str|None=Field(default=None,max_length=2000)
    registration_url:str|None=Field(default=None,max_length=2000)
    qr_url:str|None=Field(default=None,max_length=2000)
    registration_fee:float|None=Field(default=None,ge=0,le=10000000)
    fee_currency:str|None=Field(default=None,max_length=12)
    registration_required:bool|None=None
    eligibility:str|None=Field(default=None,max_length=5000)
    team_size:str|None=Field(default=None,max_length=1000)
    registration_instructions:str|None=Field(default=None,max_length=5000)
    organizer:str|None=Field(default=None,max_length=1000)
    hosting_department:str|None=Field(default=None,max_length=500)
    organizing_club:str|None=Field(default=None,max_length=500)
    faculty_coordinators:str|None=Field(default=None,max_length=2000)
    student_coordinators:str|None=Field(default=None,max_length=2000)
    contacts:str|None=Field(default=None,max_length=3000)
    prizes:str|None=Field(default=None,max_length=3000)
    certificates:str|None=Field(default=None,max_length=3000)
    agenda:str|None=Field(default=None,max_length=10000)
    rules:str|None=Field(default=None,max_length=15000)
    materials:str|None=Field(default=None,max_length=5000)
    external_links:list[str]|None=Field(default=None,max_length=30)
    additional_instructions:str|None=Field(default=None,max_length=10000)

    @model_validator(mode='before')
    @classmethod
    def missing_optional_values(cls,value):
        if isinstance(value,dict):
            value=dict(value)
            for prefix in ('start','end'):
                if prefix+'_date' in value or prefix+'_time' in value:
                    value[prefix]=None
            for field,raw in value.items():
                if field not in {'title','department','section','category'} and isinstance(raw,str) and raw.strip().casefold() in {'','na','n/a','null','none','not available','-','—'}:
                    value[field]=None
            if not value.get('category'):
                value['category']='Other'
        return value

    @field_validator('meeting_url','registration_url','qr_url')
    @classmethod
    def safe_url(cls,value):
        return https_url(value)

    @field_validator('external_links')
    @classmethod
    def safe_links(cls,value):
        return [https_url(link) for link in value] if value else None

    @model_validator(mode='after')
    def valid_event(self):
        if re.fullmatch(r'(?:official\s+)?(?:event|notice|announcement|untitled|poster)',self.title,re.I):
            raise ValueError('Enter a meaningful event title, not a generic document label.')
        content=[self.description,self.full_description,self.location,self.organizer,self.eligibility,self.agenda,self.rules,self.registration_url,self.start_date,self.start]
        if not any(v and str(v).strip()!=self.title for v in content):
            raise ValueError('Add a description or other usable event information.')
        for field in ('start','end','registration_deadline','submission_deadline'):
            value=getattr(self,field)
            if isinstance(value,datetime) and value.tzinfo is not None:
                raise ValueError('Use the institution-local date/time without a timezone suffix.')
        if self.start:
            self.start_date=self.start_date or self.start.date()
            self.start_time=self.start_time or self.start.time()
        elif self.start_date and self.start_time:
            self.start=datetime.combine(self.start_date,self.start_time)
        if self.end:
            self.end_date=self.end_date or self.end.date()
            self.end_time=self.end_time or self.end.time()
        elif self.end_date and self.end_time:
            self.end=datetime.combine(self.end_date,self.end_time)
        if self.start and self.end and self.end<=self.start:
            raise ValueError('End date/time must follow start date/time.')
        if self.start_date and self.end_date and self.end_date<self.start_date:
            raise ValueError('End date must not precede start date.')
        if self.start_time and self.end_time and (not self.end_date or self.end_date==self.start_date) and self.end_time<=self.start_time:
            raise ValueError('End time must follow start time; supply an explicit later end date for an overnight event.')
        return self


class EventReviewRequest(Strict):
    events:list[dict[str,Any]]=Field(min_length=1,max_length=100)
    pages:list[DocumentPage]=Field(min_length=1,max_length=200)
    ocr_reviewed:bool=False

    @model_validator(mode='after')
    def bounded(self):
        if len(json.dumps(self.model_dump(mode='json')))>4_000_000:
            raise ValueError('Reviewed event text exceeds 4 MB.')
        if any(set(row)-set(OfficialEventData.model_fields) for row in self.events):
            raise ValueError('Unsupported event field.')
        return self
