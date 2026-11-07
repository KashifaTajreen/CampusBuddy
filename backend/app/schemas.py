from datetime import date, datetime, time
from typing import Literal,Any
import json
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class FacultyData(Strict):
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=150)
    department: str = Field(min_length=1, max_length=60)
    designation: str = Field(default="Faculty", max_length=100)
    subjects: str = Field(max_length=500)
    office: str = Field(max_length=200)
    office_hours: str = Field(max_length=200)
    contact: str = Field(default="", max_length=200)


class TimetableData(Strict):
    department: str = Field(min_length=1, max_length=60)
    semester: int = Field(ge=1, le=12)
    section: str = Field(min_length=1, max_length=20)
    lab_batch: str | None = Field(default=None, max_length=30)
    subject: str = Field(min_length=1, max_length=200)
    course_code: str | None = Field(default=None, max_length=200)
    faculty_code: str | None = Field(default=None, max_length=60)
    faculty_name: str | None = Field(default=None, max_length=150)
    room: str | None = Field(default=None, max_length=100)
    day: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    effective_from: date | None = None
    effective_to: date | None = None

    @field_validator('lab_batch','course_code','faculty_code','faculty_name','room',mode='before')
    @classmethod
    def unknown_optional(cls, value):
        return None if value is None or str(value).strip().casefold() in {'','na','n/a','null','none','nan','-','—'} else value

    @model_validator(mode="after")
    def chronological(self):
        if self.start_time >= self.end_time or (self.effective_from and self.effective_to and self.effective_from > self.effective_to):
            raise ValueError("End time/date must follow start time/date.")
        return self


class MenuData(Strict):
    served_on: date | None = None
    served_day: int | None = Field(default=None,ge=0,le=6)
    meal: str = Field(default="", max_length=60)
    name: str = Field(min_length=1, max_length=150)
    price: float = Field(ge=0, le=10000)
    vegetarian: bool | None = None
    category: str = Field(default="", max_length=100)
    available: bool = True
    calories: float | None = Field(default=None, ge=0, le=10000)
    protein: float | None = Field(default=None, ge=0, le=500)
    nutrition_verified: bool = False
    location: str = Field(default="", max_length=200)
    department: str = Field(default="All", max_length=60)


class EventData(Strict):
    subject: str = Field(default="", max_length=200)
    semester: int | None = Field(default=None, ge=1, le=12)
    section: str = Field(default="All", min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=300)
    start: datetime
    end: datetime
    location: str = Field(max_length=200)
    kind: Literal["event", "workshop", "exam", "deadline", "personal"] = "event"
    tags: str = Field(default="", max_length=500)
    department: str = Field(default="All", max_length=60)
    registration_url: str = Field(default="", max_length=1000)

    @field_validator("start", "end")
    @classmethod
    def local_time(cls, v):
        if v.tzinfo is not None:
            raise ValueError("Use campus-local times without a timezone offset.")
        return v

    @model_validator(mode="after")
    def chronological(self):
        if self.start >= self.end:
            raise ValueError("Event end must follow start.")
        if self.registration_url and not self.registration_url.startswith("https://"):
            raise ValueError("Use HTTPS registration links.")
        return self


class EmailData(Strict):
    sender: str = Field(min_length=1, max_length=300)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=100000)
    received: datetime
    unread: bool = True
    extracted_dates: list[str] = Field(default_factory=list, max_length=100)
    department: str = Field(default="All", max_length=60)


class CalendarData(Strict):
    title: str = Field(min_length=1, max_length=300)
    start_on: date
    end_on: date | None = None
    entry_kind: Literal["holiday", "academic", "important_date"] = "academic"
    classes_cancelled: bool | None = None
    department: str = Field(default="All", min_length=1, max_length=60)
    semester: int | None = Field(default=None, ge=1, le=12)
    section: str = Field(default="All", min_length=1, max_length=20)

    @model_validator(mode="after")
    def interval(self):
        self.end_on = self.end_on or self.start_on
        if self.classes_cancelled is None:
            self.classes_cancelled = self.entry_kind == "holiday"
        if self.end_on < self.start_on:
            raise ValueError("Calendar end must not precede start.")
        return self


class ExceptionData(Strict):
    timetable_id: str
    date: date
    cancelled: bool = True
    room: str = Field(default="", max_length=100)
    department: str = Field(min_length=1, max_length=60)


DOMAIN_SCHEMAS = {
    "faculty": FacultyData,
    "timetable": TimetableData,
    "menu": MenuData,
    "events": EventData,
    "emails": EmailData,
    "exceptions": ExceptionData,
    "calendar": CalendarData,
}


class ChatRequest(Strict):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None
    language: Literal["auto", "en", "hi", "kn"] | None = None
    # Omitted retains the conversation selection; explicit null clears it.
    document_id: str | None = Field(default=None, min_length=1, max_length=36)


class DocumentSelectionUpdate(Strict):
    document_id: str | None = Field(min_length=1, max_length=36)


class DocumentPage(Strict):
    page: int = Field(ge=1, le=200)
    text: str = Field(max_length=200000)


class ImportOptions(Strict):
    event_workflow: bool = False
    event_publish_requested: bool = False
    event_reviewed: bool = False
    event_index_preview: bool = False
    event_category: str | None = Field(default=None,max_length=80)
    event_rows: list[dict[str,Any]] | None = Field(default=None,max_length=100)
    event_pages: list[DocumentPage] | None = Field(default=None,max_length=200)
    timetable_ready_only: bool = False
    timetable_publish_requested: bool = False
    corrected_pages: list[DocumentPage] | None = Field(default=None, min_length=1, max_length=200)
    ocr_extract_pending: bool = False
    ocr_reviewed: bool = False
    ocr_languages: str = Field(default="eng", pattern=r"^(eng|hin|kan)(\+(eng|hin|kan)){0,2}$")
    menu_location: str = Field(default="", max_length=200)
    menu_vegetarian: bool | None = None
    menu_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    overlap_reviewed: bool = False
    corrected_rows: list[dict[str, str | int | float | bool | None]] | None = Field(default=None, max_length=10000)
    reindex: bool = False
    kind: Literal["timetable", "faculty", "menu", "events", "calendar", "document", "exceptions", "emails"]
    semester: int | None = Field(default=None, ge=1, le=12)
    section: str = Field(default="All", min_length=1, max_length=20)
    event_kind: Literal["event", "workshop", "exam", "deadline"] | None = None
    logical_key: str = Field(default="", max_length=100)
    mapping: dict[str, str] = Field(default_factory=dict, max_length=30)
    department: str = Field(default="All", min_length=1, max_length=60)
    title: str = Field(default="", max_length=300)
    category: str = Field(default="Notice", max_length=60)
    published: date | None = None
    expires: date | None = None
    topic: str = Field(default="", max_length=100)
    claim: str = Field(default="", max_length=300)
    supersedes_id: str | None = None
    demo: bool = False
    visibility: Literal["shared", "private"] = "shared"
    replaces_source_id: str | None = None

    @model_validator(mode="after")
    def private_scope(self):
        if self.event_workflow or self.event_rows is not None or self.event_pages is not None:
            if self.kind!='events':
                raise ValueError('Event review fields are only valid for Events.')
            if len(json.dumps(self.event_rows or []))+sum(len(p.text) for p in self.event_pages or [])>4_000_000:
                raise ValueError('Reviewed events exceed 4 MB.')
        if self.corrected_pages is not None:
            if self.kind != "document" or sum(len(p.text) for p in self.corrected_pages) > 4_000_000:
                raise ValueError("Text corrections require a document and at most 4 MB of text.")
            if len({p.page for p in self.corrected_pages}) != len(self.corrected_pages):
                raise ValueError("Text corrections must retain unique page identifiers.")
        if (self.semester is None) != (self.section == "All"):
            raise ValueError("Select both a semester and section, or neither.")
        if self.semester is not None and self.department == "All":
            raise ValueError("A section scope requires a specific department.")
        if self.corrected_rows is not None and (not self.corrected_rows or len(json.dumps(self.corrected_rows)) > 4_000_000 or any(len(r) > 100 for r in self.corrected_rows)):
            raise ValueError("Corrected rows exceed row/column/content limits.")
        if self.kind == "emails":
            self.visibility = "private"
        if self.visibility == "private" and self.kind not in ("emails", "document"):
            raise ValueError("Only personal documents and emails can use private imports.")
        if self.visibility == "private" and (self.replaces_source_id or self.supersedes_id):
            raise ValueError("Shared replacement is not available for private imports.")
        return self


class SourceAction(Strict):
    reason: str = Field(default="", max_length=500)


class MappingRequest(Strict):
    timetable_ready_only: bool = False
    pages: list[DocumentPage] | None = Field(default=None, min_length=1, max_length=200)
    mapping: dict[str, str] = Field(max_length=30)
    rows: list[dict[str, str | int | float | bool | None]] | None = Field(default=None, max_length=10000)
    ocr_reviewed: bool = False
    reextract: bool = False
    defaults: dict[str,str | bool | None] | None = Field(default=None,max_length=6)
    overlap_reviewed: bool = False
    reset_mapping: bool = False


class TimetablePublishRequest(Strict):
    ocr_reviewed: bool = False
    overlap_reviewed: bool = False
    ready_only: bool = False


class FileAction(Strict):
    action: Literal["delete", "delete_draft", "restore", "purge", "rollback"]
    confirmation: str = Field(default="", max_length=100)
    replaces_source_id: str | None = Field(default=None, max_length=36)


class Preferences(Strict):
    name: str = Field(min_length=1, max_length=120)
    interests: str = Field(max_length=500)
    diet: Literal["any", "vegetarian"] = "any"


class MembershipUpdate(Strict):
    user_id: str
    role: Literal["student", "department_admin", "section_admin", "canteen_manager", "institution_admin"]
    department: str = Field(min_length=1, max_length=60)
    semester: int = Field(ge=1, le=12)
    section: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    student_number: str | None = Field(default=None, pattern=r"^[1-9][A-Z]{2}\d{2}[A-Z]{2}\d{3}$")

    @field_validator("student_number", mode="before")
    @classmethod
    def normalize_student_number(cls, value):
        return str(value).strip().upper() or None if value is not None else None

    @model_validator(mode="after")
    def bounded_admin_scope(self):
        if self.role in {"department_admin","section_admin"} and self.department == "All":
            raise ValueError("Department and Section Admins require a specific department.")
        if self.role == "section_admin" and self.section == "All":
            raise ValueError("Section Admins require a specific section.")
        return self


class SectionScope(Strict):
    department: str = Field(min_length=1, max_length=60)
    semester: int = Field(ge=1, le=12)
    section: str = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def specific_scope(self):
        self.department = self.department.strip()
        self.section = self.section.strip()
        if not self.department or not self.section or self.department.lower() == "all" or self.section.lower() == "all":
            raise ValueError("Section enrollment requires a specific department and section.")
        return self


class JoinSectionRequest(SectionScope):
    code: str = Field(min_length=4, max_length=64)


class GenerateSectionCodeRequest(SectionScope):
    expiry_days: int = Field(default=7, ge=1, le=90)


class RevokeSectionCodeRequest(Strict):
    code_id: str = Field(min_length=1, max_length=36)
