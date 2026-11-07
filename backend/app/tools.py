"""Approved typed tools. REST, agent and MCP share this exact dispatcher."""

from datetime import date
from time import perf_counter
from typing import Literal
from pydantic import Field
from backend.app.schemas import Strict
from backend.app.retrieval import Retrieval


class Empty(Strict):
    pass


class DayInput(Strict):
    on: date | None = None
    at: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class FacultyInput(Strict):
    query: str = Field(default="", max_length=200)
    code: str | None = Field(default=None, max_length=60)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    subject: str | None = Field(default=None, min_length=1, max_length=120)


class NextClassInput(Strict):
    faculty_code: str | None = Field(default=None, min_length=1, max_length=60)


class SearchInput(Strict):
    query: str = Field(default="", max_length=200)


class MenuInput(SearchInput):
    on: date | None = None
    meal: Literal["breakfast", "lunch", "dinner"] | None = None
    budget: float = Field(default=100, ge=0, le=10000)
    vegetarian: bool | None = None
    min_protein: float = Field(default=0, ge=0, le=500)
    sort: Literal["price", "protein"] = "price"
    category: Literal["sweet"] | None = None
    strict: bool = False


class MealInput(Strict):
    budget: float = Field(default=100, ge=0, le=10000)
    vegetarian: bool | None = None
    count: Literal[2, 3] = 2
    category: Literal["sweet"] | None = None
    strict: bool = False


class DocumentInput(Strict):
    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)
    include_outdated: bool = False
    document_id: str | None = Field(default=None, min_length=1, max_length=36)
    event_id: str | None = Field(default=None,min_length=1,max_length=36)


class EventInput(SearchInput):
    start: date | None = None
    days: int = Field(default=14, ge=1, le=90)
    kind: Literal["event", "workshop", "exam", "deadline", "personal"] | None = None


class AcademicInput(Strict):
    start: date | None = None
    days: int = Field(default=30, ge=1, le=366)


class CampusEventInput(EventInput):
    category:str|None=Field(default=None,max_length=80)
    status:Literal['all','upcoming','ongoing','past','undated']='upcoming'
    include_undated:bool=False


class ConflictInput(Strict):
    start: date | None = None
    days: int = Field(default=7, ge=1, le=31)


class FreeInput(Strict):
    on: date | None = None
    min_minutes: int = Field(default=30, ge=1, le=480)
    start_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class EmailInput(SearchInput):
    unread_only: bool = False
    days: int = Field(default=90, ge=1, le=365)


class NoticeInput(SearchInput):
    include_outdated: bool = False


TOOLS = {
    "get_academic_calendar": (AcademicInput, "Read authorized holidays, class closures and academic dates."),
    "get_my_timetable": (DayInput, "Get your effective section timetable for a campus-local ISO date."),
    "get_next_class": (NextClassInput, "Get your next class, optionally for a resolved faculty code."),
    "get_faculty_profile": (FacultyInput, "Search authorized faculty directory records."),
    "find_classroom": (SearchInput, "Find classroom usage in accessible institutional timetables."),
    "search_canteen_items": (MenuInput, "Filter available food by price, diet and estimated protein."),
    "optimize_meal_budget": (MealInput, "Calculate two- or three-item meal combinations within budget."),
    "search_documents": (DocumentInput, "Retrieve accessible passages, source pages and conflicting claims."),
    "get_latest_notices": (
        NoticeInput,
        "List current accessible notices, optionally including expired records.",
    ),
    "get_examination_schedule": (EventInput, "List examinations from the authorized calendar."),
    "list_campus_events": (CampusEventInput, "Find authorized published campus events by date, category or status. Undated events require explicit inclusion."),
    "detect_schedule_conflicts": (
        ConflictInput,
        "Compute overlaps among your classes and authorized events.",
    ),
    "find_free_time_slots": (FreeInput, "Compute free campus-day intervals after merging busy periods."),
    "search_authorized_emails": (EmailInput, "Search only your authorized imported messages."),
    "get_calendar_events": (EventInput, "List your accessible calendar events."),
}


class ToolKit:
    def __init__(self, service, max_calls=8, deadline_seconds=20):
        self.c = service
        self.trace = []
        self.max_calls = max_calls
        self.started = perf_counter()
        self.deadline = deadline_seconds

    def call(self, tool_name, **kwargs):
        name = tool_name
        if name not in TOOLS:
            raise ValueError("This tool is not available.")
        if len(self.trace) >= self.max_calls or perf_counter() - self.started > self.deadline:
            raise ValueError("This question reached the tool budget. Please narrow the question.")
        started = perf_counter()
        success = False
        try:
            data = TOOLS[name][0].model_validate(kwargs).model_dump()
            if name == "get_my_timetable":
                result = self.c.timetable(**data)
            elif name == "get_academic_calendar":
                result = self.c.academic_dates(**data)
            elif name == "get_next_class":
                result = self.c.next_class(**data)
            elif name == "get_faculty_profile":
                result = self.c.faculty(**data)
            elif name == "find_classroom":
                result = self.c.classroom(**data)
            elif name == "search_canteen_items":
                result = self.c.menu(**data)
            elif name == "optimize_meal_budget":
                result = self.c.meals(**data)
            elif name == "search_documents":
                result = Retrieval(self.c).search(**data)
            elif name == "get_latest_notices":
                result = self.c.notices(**data)
            elif name == "get_examination_schedule":
                data["kind"] = "exam"
                result = self.c.events(**data)
            elif name=='list_campus_events':
                result=self.c.event_listing(**data)
            elif name == "get_calendar_events":
                result = self.c.events(**data)
            elif name == "detect_schedule_conflicts":
                result = self.c.conflicts(**data)
            elif name == "find_free_time_slots":
                result = self.c.free_slots(**data)
            elif name == "search_authorized_emails":
                result = self.c.emails(**data)
            success = True
            return result
        finally:
            self.trace.append(
                dict(tool=name, latency_ms=round((perf_counter() - started) * 1000, 2), success=success)
            )
