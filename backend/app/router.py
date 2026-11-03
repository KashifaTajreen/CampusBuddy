import re
from datetime import date, timedelta
from time import perf_counter
from backend.app.tools import ToolKit
from backend.app.models import QueryTrace
from backend.app.agent import Agent
from backend.app.ai import get_provider
from backend.app.config import settings

AGENT_MIN_WORDS = 5
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_date(q, today):
    match = re.search(r"\b20\d{2}-\d{2}-\d{2}\b", q)
    if match:
        return date.fromisoformat(match[0])
    if "tomorrow" in q:
        return today + timedelta(days=1)
    if "yesterday" in q:
        return today - timedelta(days=1)
    for i, name in enumerate(DAYS):
        if name in q:
            delta = (i - today.weekday()) % 7
            if "next " + name in q:
                delta = delta or 7
            return today + timedelta(days=delta)
    return today


def parse_time(q):
    m = re.search(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)\b", q)
    if m:
        return f"{int(m[1]) % 12 + (12 if m[3] == 'pm' else 0):02}:{int(m[2] or 0):02}"
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", q)
    return f"{int(m[1]):02}:{m[2]}" if m else None


class Router:
    def __init__(self, service, provider=None):
        self.c = service
        self.provider = provider or get_provider()

    def _agent_turn(self, question, context):
        """Ask the tool-calling agent, with its own tool budget so a failed turn can fall back."""
        if settings().routing_mode != "hybrid" or not hasattr(self.provider, "chat"):
            return None, []
        tools = ToolKit(self.c, max_calls=6, deadline_seconds=settings().llm_timeout_seconds * 3)
        return Agent(self.c, self.provider, tools).run(question, context), tools.trace

    def ask(self, question, context=None):
        if not question.strip() or len(question) > 2000:
            raise ValueError("Ask a question using 1–2,000 characters.")
        c = self.c
        tools = ToolKit(c)
        started = perf_counter()
        context = dict(context or {})
        q = question.lower().replace("’", "'")
        today = c.clock().date()
        on = parse_date(q, today)
        at = parse_time(q)
        route = "Unsupported request"
        kind = "text"
        rows = []
        answer = ""
        extra_sources = []
        evidence = []
        # Short questions are usually exact requests that the fast rules answer directly.
        long_question = len(question.split()) > AGENT_MIN_WORDS
        agent_turn, agent_trace = self._agent_turn(question, context) if long_question else (None, [])
        if agent_turn:
            route = "Agent"
            answer, kind, rows, evidence = agent_turn
            if kind == "faculty" and rows:
                context["faculty_code"] = rows[0].get("code")
        elif re.search(r"email|inbox|department send|unread|\bmail\b", q):
            route = "Keyword search"
            kind = "emails"
            query = "internal assessment" if "assessment" in q else "faculty" if "faculty" in q else ""
            rows = tools.call("search_authorized_emails", query=query, unread_only="unread" in q)
            answer = (
                f"Found {len(rows)} authorized message(s). Extracted dates need confirmation with the sender."
            )
        elif re.search(r"food|eat|canteen|meal|vegetarian|protein|₹|rupees|\bbudget\b", q) or (
            context.get("domain") == "food" and re.search(r"under|two|three|cheaper|veg", q)
        ):
            route = "Filtered database search"
            context["domain"] = "food"
            m = re.search(r"(?:₹\s*|rs\.?\s*|under\s*|below\s*|budget\s*(?:of\s*)?|for\s*)(\d+(?:\.\d+)?)", q)
            budget = float(m[1]) if m else context.get("budget", 100)
            veg = (
                False
                if re.search(r"non[ -]?veg", q)
                else True
                if re.search(r"vegetarian|\bveg\b", q)
                else True
                if c.actor.diet == "vegetarian"
                else None
            )
            context.update(budget=budget, vegetarian=veg)
            if re.search(r"\btwo\b|\bthree\b|combination|combo|\b2 items", q):
                kind = "meals"
                rows = tools.call(
                    "optimize_meal_budget", budget=budget, vegetarian=veg, count=3 if "three" in q else 2
                )
                answer = f"{len(rows)} combinations fit within ₹{budget:g}, ranked by estimated protein."
            else:
                kind = "food"
                rows = tools.call(
                    "search_canteen_items",
                    budget=budget,
                    vegetarian=veg,
                    sort="protein" if "protein" in q else "price",
                )
                if "highest" in q:
                    rows = rows[:1]
                answer = f"{len(rows)} available option(s) within ₹{budget:g}. Nutrition is estimated unless marked verified."
        elif re.search(r"event|workshop|hackathon|seminar", q) and re.search(
            r"attend|clash|overlap|missing|time for|without", q
        ):
            route = "Cross-source reasoning"
            kind = "event_fit"
            term = "AI" if re.search(r"\bai\b", q) else "workshop" if "workshop" in q else ""
            events = tools.call("list_campus_events", query=term, days=14)
            if any(day in q for day in DAYS):
                events = [e for e in events if e["start"][:10] == str(on)]
            # Bound tools and process candidate events rather than open-ended autonomous calls.
            events = events[:3]
            for event in events:
                classes = tools.call("get_my_timetable", on=event["start"][:10])
                clashes = [r for r in classes if c.overlaps(r, event)]
                rows.append(
                    dict(
                        event=event,
                        clashes=clashes,
                        can_attend=not clashes,
                        source_ids=list({event["source_id"]} | {r["source_id"] for r in clashes}),
                    )
                )
            answer = "Here is how the activity schedule fits your classes."
        elif re.search(r"office|meet (?:her|him|that|the)|that professor|her contact|his contact", q):
            route = "Exact structured lookup"
            kind = "faculty"
            if context.get("faculty_code"):
                rows = tools.call("get_faculty_profile", code=context["faculty_code"])
            else:
                directory = tools.call("get_faculty_profile")
                rows = [
                    r for r in directory if any(w.lower() in q for w in r["name"].split()[1:] if len(w) > 3)
                ]
            answer = (
                "Office details from the supplied faculty directory."
                if rows
                else "Which professor do you mean? Ask who teaches a subject first."
            )
        elif re.search(r"who teaches|faculty|professor|coordinator", q) and not re.search(
            r"notice|policy|next class", q
        ):
            route = "Exact structured lookup"
            kind = "faculty"
            term = re.sub(r"^.*?(?:who teaches|coordinator for|faculty for|professor for)\s*", "", q).strip(
                " ?"
            )
            if term in ("ml", "machine learning"):
                term = "Machine Learning"
            rows = tools.call("get_faculty_profile", query=term if term != q else c.actor.department)
            answer = (
                "Matching teaching faculty. Coordinator assignments are not recorded."
                if "coordinator" in q
                else "Matching faculty from the directory."
            )
            if rows:
                context["faculty_code"] = rows[0]["code"]
        elif re.search(r"free (?:slot|period|hour)|free time|when am i free", q):
            route = "Calendar computation"
            kind = "slots"
            rows = tools.call("find_free_time_slots", on=str(on), min_minutes=60 if "hour" in q else 30)
            answer = (
                f"Free periods on {on:%A, %d %B}, between 09:00 and 17:00, after your classes and events."
            )
        elif re.search(r"clash|overlap|conflict", q) and not re.search(r"notice|circular|document", q):
            route = "Calendar computation"
            kind = "conflicts"
            rows = tools.call("detect_schedule_conflicts", start=str(on))
            answer = f"{len(rows)} schedule overlap(s) in the next seven days."
        elif re.search(
            r"upcoming exam|next (?:internal|exam|assessment)|exam schedule|when.*internal assessment", q
        ):
            route = "Cross-source reasoning"
            kind = "events"
            rows = tools.call("get_examination_schedule", days=90)
            docs = tools.call("search_documents", query="internal assessment examination circular")
            extra_sources.extend(h["source_id"] for h in docs["hits"])
            answer = "Upcoming examinations from the calendar."
            if docs["conflicts"]:
                answer += (
                    " Conflicting examination circulars exist. Confirm the date with the examination office."
                )
        elif re.search(
            r"notice|circular|policy|policies|document|assessment|library|scholarship|announcement", q
        ):
            route = "Semantic document retrieval"
            if re.search(r"announcement|latest notices|today.s notices", q) and not re.search(
                r"exam|conflict", q
            ):
                kind = "notices"
                rows = tools.call("get_latest_notices")
                answer = "Recent notices relevant to your department."
            else:
                kind = "passages"
                result = tools.call(
                    "search_documents",
                    query=question,
                    include_outdated=bool(re.search(r"archiv|outdated|previous", q)),
                )
                rows = result["hits"]
                answer = result["answer"]
                if not result["conflicts"] and not result["missing"]:
                    synthesis = self.provider.synthesize(question, rows)
                    if synthesis:
                        answer = synthesis.answer
        elif re.search(r"event|workshop|hackathon|seminar|deadline|activit", q):
            route = "Filtered database search"
            kind = "events"
            start = today
            days = 14
            if "this week" in q:
                days = 7 - today.weekday()
            if any(day in q for day in DAYS):
                start = on
                days = 1
            term = (
                "AI"
                if re.search(r"\bai\b", q)
                else "workshop"
                if "workshop" in q
                else "technical"
                if "technical" in q
                else ""
            )
            rows = tools.call(
                "list_campus_events",
                start=str(start),
                days=days,
                query=term,
                kind="deadline" if "deadline" in q else None,
            )
            answer = "Upcoming activities matching your question."
        elif re.search(
            r"class|timetable|schedule|who.s teaching|who is teaching|teaching it|tomorrow|today|friday", q
        ):
            route = "Exact structured lookup"
            kind = "classes"
            if "teaching it" in q or "who's teaching" in q:
                kind = "faculty"
                rows = (
                    tools.call("get_faculty_profile", code=context.get("faculty_code"))
                    if context.get("faculty_code")
                    else []
                )
                answer = "Teaching faculty for the class we discussed."
            elif "next" in q and "week" not in q:
                row = tools.call("get_next_class")
                rows = [row] if row else []
                answer = "Your next class."
            elif "week" in q:
                first = on - timedelta(days=on.weekday())
                if "next week" in q:
                    first += timedelta(days=7)
                for i in range(7):
                    rows.extend(tools.call("get_my_timetable", on=str(first + timedelta(days=i))))
                answer = "Your weekly timetable."
            else:
                rows = tools.call("get_my_timetable", on=str(on), at=at)
                answer = f"Your classes on {on:%A, %d %B}."
            if kind == "classes" and rows:
                context.update(faculty_code=rows[0]["faculty_code"], date=rows[0]["date"], domain="timetable")
        elif not long_question and (turn := self._agent_turn(question, context))[0]:
            route = "Agent"
            answer, kind, rows, evidence = turn[0]
            agent_trace = turn[1]
        else:
            answer = "I can find campus classes, food, faculty, notices, events and your authorized emails. Try a subject name or “My next class”. I do not have supporting information for that request."
        if not rows and kind != "text" and not ("could not find" in answer or "Which professor" in answer):
            answer += " No matching records were found. Try a different date, broader filters, or ask an administrator to import the source."
        source_ids = set(extra_sources)

        def gather(value):
            if isinstance(value, dict):
                if value.get("source_id"):
                    source_ids.add(value["source_id"])
                if value.get("faculty_source_id"):
                    source_ids.add(value["faculty_source_id"])
                source_ids.update(value.get("source_ids", []))
                for item in value.values():
                    if isinstance(item, (list, dict)):
                        gather(item)
            elif isinstance(value, list):
                for item in value:
                    gather(item)

        gather(rows)
        gather(evidence)
        sources = []
        for sid in source_ids:
            source = c.source(sid)
            sources.append(
                dict(
                    id=sid,
                    filename=source.filename,
                    demo=source.demo,
                    created_at=source.created_at.isoformat(),
                    url=f"/v1/sources/{sid}",
                )
            )
        elapsed = round((perf_counter() - started) * 1000, 2)
        c.db.add(
            QueryTrace(
                tenant_id=c.actor.tenant_id,
                user_id=c.actor.user_id,
                route=route,
                tools=agent_trace + tools.trace,
                latency_ms=elapsed,
                success=True,
                result_count=len(rows),
            )
        )
        followups = (
            ["Where is that professor’s office?", "Show my timetable for Friday"]
            if kind in ("classes", "faculty")
            else ["Can I buy two items under ₹100?", "Highest-protein food under ₹120"]
            if kind in ("food", "meals")
            else ["Latest notices", "Events this week"]
        )
        return dict(
            answer=answer,
            kind=kind,
            records=rows,
            sources=sources,
            followups=followups,
            context=context,
            route=route,
            trace=agent_trace + tools.trace,
            latency_ms=elapsed,
        )
