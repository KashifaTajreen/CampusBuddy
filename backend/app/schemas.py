from datetime import date, datetime, time
from typing import Literal
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
    subject: str = Field(min_length=1, max_length=200)
    faculty_code: str = Field(min_length=1, max_length=60)
    room: str = Field(min_length=1, max_length=100)
    day: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    effective_from: date
    effective_to: date

    @model_validator(mode="after")
    def chronological(self):
        if self.start_time >= self.end_time or self.effective_from > self.effective_to:
            raise ValueError("End time/date must follow start time/date.")
        return self


class MenuData(Strict):
    name: str = Field(min_length=1, max_length=150)
    price: float = Field(ge=0, le=10000)
    vegetarian: bool
    category: str = Field(default="General", max_length=100)
    available: bool = True
    calories: float | None = Field(default=None, ge=0, le=10000)
    protein: float | None = Field(default=None, ge=0, le=500)
    nutrition_verified: bool = False
    location: str = Field(default="Campus Canteen", min_length=1, max_length=200)
    department: str = Field(default="All", max_length=60)


class EventData(Strict):
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
}


class ChatRequest(Strict):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


class ImportOptions(Strict):
    reindex: bool = False
    kind: Literal["timetable", "faculty", "menu", "events", "document", "exceptions", "emails"]
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


class MappingRequest(Strict):
    mapping: dict[str, str] = Field(max_length=30)


class Preferences(Strict):
    name: str = Field(min_length=1, max_length=120)
    interests: str = Field(max_length=500)
    diet: Literal["any", "vegetarian"] = "any"


class MembershipUpdate(Strict):
    user_id: str
    role: Literal["student", "faculty", "department_admin", "institution_admin"]
    department: str = Field(min_length=1, max_length=60)
    semester: int = Field(ge=1, le=12)
    section: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
