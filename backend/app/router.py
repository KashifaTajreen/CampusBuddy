import re
from datetime import date, timedelta
from time import perf_counter
from backend.app.tools import ToolKit
from backend.app.models import QueryTrace
from backend.app.ai import get_provider, validate_grounded
from backend.app.language import normalize, localize
from backend.app.event_queries import matches as event_matches,answer as answer_event,translate_event_terms

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_date(q, today):
    match = re.search(r"\b20\d{2}-\d{2}-\d{2}\b", q)
    if match:
        return date.fromisoformat(match[0])
    months = "january february march april may june july august september october november december".split()
    names="|".join(months)
    match=re.search(r"\b("+names+r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b",q)
    if match:
        return date(int(match[3] or today.year),months.index(match[1])+1,int(match[2]))
    match=re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+("+names+r")(?:\s+(20\d{2}))?\b",q)
    if match:
        return date(int(match[3] or today.year),months.index(match[2])+1,int(match[1]))
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


def has_explicit_date(q):
    q = q.lower()
    if re.search(r"\b20\d{2}-\d{2}-\d{2}\b", q):
        return True
    months = "january february march april may june july august september october november december".split()
    names = "|".join(months)
    if re.search(r"\b(" + names + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?\b", q):
        return True
    if re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + names + r")(?:\s+(20\d{2}))?\b", q):
        return True
    if "tomorrow" in q or "yesterday" in q or "today" in q:
        return True
    if any(name in q for name in DAYS):
        return True
    return False


def split_question(q):
    chunks = re.split(r"(?:\s+(?:and|then)\s+|[,;]?\s*\balso\b[,\s]*|[;\n+]+|,\s*(?=(?:what|who|when|is there|am i)\b))",q)
    parts=[]
    intent=r"class|timetable|schedule|office|who|faculty|professor|food|canteen|menu|meal|lunch|breakfast|dinner|notice|circular|event|policy|library|free|conflict|exam|holiday|academic|mentor|skill lab|allot|email|\bbuy\b|\beat\b|sweet|₹"
    for chunk in chunks:
        part=chunk.strip(" ,?")
        if not part:
            continue
        if parts and not re.search(intent,part):
            parts[-1]+=" and "+part
        else:
            parts.append(part)
    if len(parts)>1 and re.match(r"i have\s*(?:₹|rs\.?|\d)",parts[0]) and not re.search(r"food|class|exam|menu",parts[0]):
        parts[1]=parts[0]+" "+parts[1]
        parts=parts[1:]
    return parts or [q]


def parse_time(q):
    m = re.search(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*([ap])\.?\s*m\b\.?", q, re.I)
    if m:
        return f"{int(m[1]) % 12 + (12 if m[3].lower() == 'p' else 0):02}:{int(m[2] or 0):02}"
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", q)
    return f"{int(m[1]):02}:{m[2]}" if m else None


class Router:
    def __init__(self, service, provider=None):
        self.c = service
        self.provider = provider or get_provider()

    def ask(self, question, context=None, document_id=None, language=None):
        if not question.strip() or len(question) > 2000:
            raise ValueError("Ask a question using 1–2,000 characters.")
        context = dict(context or {})
        preference = language or context.get("language_preference", "auto")
        q, response_language = normalize(question, preference)
        q=translate_event_terms(q)
        context["language_preference"] = preference
        self.language = response_language
        self.provider_calls = 0
        self.provider.status = "disabled" if self.provider.name == "none" else "not_needed"
        tools = ToolKit(self.c)
        # Selection is validated even for unsupported questions and before any provider request.
        selected = self.c.selected_document(document_id) if document_id else None
        self.sensitivity = "private" if (selected and selected.owner_id) or re.search(r"email|inbox|private|personal", q) else "shared"
        parts = split_question(q) if not document_id else [q]
        if len(parts) > 4:
            raise ValueError("Please ask at most four campus questions at once.")
        # Only uncertain campus requests use advisory planning. Never accept SQL, IDs or tool names.
        clear = r"class|timetable|schedule|office|who|faculty|professor|food|menu|canteen|meal|event|notice|policy|library|scholarship|exam|email|free|conflict|workshop"
        complex_query = len(parts) == 1 and len(q.split()) > 24
        if not document_id and not event_matches(q,context) and (not re.search(clear, q) or complex_query) and re.search(r"campus|college|university", q):
            self.provider_calls += 1
            # Raw questions can contain private facts even if their intent is public.
            plan = self.provider.plan(question, response_language, str(self.c.local_now().date()), "private")
            if plan and plan.steps:
                triggers = {"menu": "canteen food", "meals": "two food items", "documents": "document", "office": "office",
                            "next_class": "next class", "current_class": "current class", "faculty_next_class": "their next class",
                            "faculty_notices": "faculty notices", "free_slots": "free slots", "conflicts": "conflict",
                            "exams": "exam schedule", "timetable": "timetable", "faculty": "professor", "events": "events", "notices": "notices"}
                parts = [triggers[s.intent] + " " + s.query for s in plan.steps if s.intent in triggers]
        today = self.c.local_now().date()
        if has_explicit_date(q) and "date" not in context:
            context["date"] = str(parse_date(q, today))
        sections = []
        for part in parts or [q]:
            result = self._ask_single(part, context, document_id, tools)
            result["question"]=part
            context = result["context"]
            sections.append(localize(result, response_language))
        result = sections[0]
        if len(sections) > 1:
            summary = {"en": "Results for each part of your question.", "hi": "आपके प्रश्न के प्रत्येक भाग के परिणाम।", "kn": "ನಿಮ್ಮ ಪ್ರಶ್ನೆಯ ಪ್ರತಿ ಭಾಗದ ಫಲಿತಾಂಶಗಳು."}[response_language]
            all_records = [r for s in sections for r in s.get("records", [])]
            if response_language == "en":
                summary = "\n\n".join(s["answer"] for s in sections)
            all_claims = [c for s in sections for c in s.get("generated_claims", [])]
            result = {**result, "kind": "multi", "answer": summary, "answer_mode": "structured",
                      "generated_claims": all_claims,
                      "sections": sections, "records": all_records,
                      "sources": list({s["id"]: s for r in sections for s in r.get("sources", [])}.values()), "context": context}
        return {**result, "language": response_language, "language_preference": preference,
                "provider_status": self.provider.status}

    def generate(self, question, rows):
        if self.provider_calls >= 2:
            return None
        self.provider_calls += 1
        sensitivity = "private"  # The raw student question accompanies evidence.
        output = self.provider.synthesize(question, rows, language=self.language, sensitivity=sensitivity)
        if output is None:
            return None
        try:
            return validate_grounded(output, rows)
        except ValueError:
            self.provider.status = "invalid_output"
            return None

    def _ask_single(self, question, context=None, document_id=None, tools=None):
        if not question.strip() or len(question) > 2000:
            raise ValueError("Ask a question using 1–2,000 characters.")
        c = self.c
        tools = tools or ToolKit(c)
        started = perf_counter()
        context = dict(context or {})
        q = question.lower().replace("’", "'")
        today = c.local_now().date()
        if document_id is not None:
            on = today
        elif has_explicit_date(q):
            on = parse_date(q, today)
        elif context.get("date"):
            try:
                on = date.fromisoformat(str(context["date"]))
            except ValueError:
                on = today
        else:
            on = today
        at = parse_time(q)
        route = "Unsupported request"
        kind = "text"
        rows = []
        answer = ""
        extra_sources = []
        evidence_status = None
        synthesis = None
        reason = None
        if "ambiguous_relative_date" in q:
            reason="ambiguous_date"
            answer="Does 'kal' mean yesterday or tomorrow? Please give the date so I can check your schedule accurately."
        elif document_id is not None:
            route, kind = "Selected document retrieval", "passages"
            anchor = context.get("document_question", "")
            followup = bool(anchor and len(q.split()) <= 16 and re.search(
                r"\b(it|that|this|those|these|more|when|where|why)\b", q))
            query = (question + "\n" + anchor)[:2000] if followup else question
            if not followup:
                context["document_question"] = question
            result = tools.call("search_documents", query=query, document_id=document_id)
            rows, answer = result["hits"], result["answer"]
            evidence_status = "insufficient_evidence" if result["missing"] else "retrieved_evidence"
            if not result["missing"] and not result["conflicts"]:
                synthesis = self.generate(question, rows)
                if synthesis and not synthesis.insufficient:
                    answer = synthesis.answer
        elif event_matches(q,context):
            event=answer_event(self,question,context,tools,normalized=q)
            route,kind,rows,answer,synthesis=(event[k] for k in ('route','kind','rows','answer','synthesis'))
        elif re.search(r"mentor|skill\s*lab|project\s*team|allot|\b[1-9][a-z]{2}\d{2}[a-z]{2}\d{3}\b",q):
            route, kind = "Exact student document lookup", "passages"
            result = tools.call("search_documents",query=question)
            rows, answer = result["hits"], result["answer"]
        elif re.search(r"free (?:slot|period|hour)|free time|when (?:am i|do i have) free", q):
            route = "Calendar computation"
            kind = "slots"
            tm = re.search(r"between\s+([01]?\d|2[0-3]):([0-5]\d)\s+and\s+([01]?\d|2[0-3]):([0-5]\d)", q)
            start_time = f"{int(tm[1]):02}:{tm[2]}" if tm else None
            end_time = f"{int(tm[3]):02}:{tm[4]}" if tm else None
            call_kwargs = dict(on=str(on), min_minutes=60 if "hour" in q else 30)
            if start_time:
                call_kwargs["start_time"] = start_time
            if end_time:
                call_kwargs["end_time"] = end_time
            rows = tools.call("find_free_time_slots", **call_kwargs)
            st_str = start_time or "09:00"
            et_str = end_time or "17:00"
            answer = (
                f"Free periods on {on:%A, %d %B}, between {st_str} and {et_str}, after your classes and events."
            )
            if not c.timetable_coverage(on):
                reason="schedule_unknown"
                answer="No published timetable covers that date. I cannot determine free time from missing information."
        elif at and re.search(r"\bfree\b|available|availability",q):
            route,kind="Schedule availability SQL","availability"
            classes=tools.call("get_my_timetable",on=str(on))
            events=tools.call("get_calendar_events",start=str(on),days=1)
            calendar=tools.call("get_academic_calendar",start=str(on),days=1)
            coverage=c.timetable_coverage(on)
            instant=f"{on}T{at}:00"
            busy=[r for r in classes+events if r["start"]<=instant<r["end"] and (r in classes or r.get("kind") in {"exam","personal"})]
            reason="busy" if busy else "no_recorded_conflict" if coverage else "schedule_unknown"
            rows=[dict(date=str(on),time=at,status=reason,commitments=busy,
                source_ids=list(set(coverage)|{r["source_id"] for r in busy+calendar}))]
            answer=(f"You have a recorded class or exam at {at} on {on}." if busy else
                f"No recorded class or exam conflicts with {at} on {on} inside your published timetable coverage. Check other personal commitments."
                if coverage else "No published timetable covers that date. I cannot conclude that you are free.")
        elif re.search(r"holiday|academic calendar|college closed|college working|important dates", q):
            route, kind = "Academic calendar SQL", "calendar"
            rows = tools.call("get_academic_calendar", start=str(on),
                days=1 if re.search(r"today|tomorrow|holiday|closed|working", q) else 30)
            closures = [r for r in rows if r["classes_cancelled"] or r["entry_kind"] == "holiday"]
            if re.search(r"holiday|closed|working", q):
                rows = closures
                answer = (f"Published holiday/calendar information for {on}: "+"; ".join(r["title"] for r in closures)
                    if closures else "No published holiday evidence was found for that date. This does not establish that the college is open.")
            else:
                answer = "Published academic calendar and important dates."
        elif re.search(r"email|inbox|department send|unread|\bmail\b", q):
            route = "Keyword search"
            kind = "emails"
            query = "internal assessment" if "assessment" in q else "faculty" if "faculty" in q else ""
            rows = tools.call("search_authorized_emails", query=query, unread_only="unread" in q)
            answer = (
                f"Found {len(rows)} authorized message(s). Extracted dates need confirmation with the sender."
            )
        elif re.search(r"food|eat|canteen|menu|meal|lunch|breakfast|dinner|vegetarian|protein|₹|rupees|\bbudget\b", q) or (
            context.get("domain") == "food" and re.search(r"under|two|three|cheaper|veg", q)
        ):
            route = "Filtered database search"
            context["domain"] = "food"
            strict = bool(re.search(r"\b(?:strictly\s+under|under|below|less\s+than)\b", q))
            m = re.search(
                r"(?:₹\s*|rs\.?\s*|strictly\s+under\s*|under\s*|below\s*|less\s+than\s*|within\s*|up\s+to\s*|budget\s*(?:of\s*)?|for\s*)(\d+(?:\.\d+)?)",
                q,
            )
            if not m:
                m = re.search(r"(\d+(?:\.\d+)?)\s*(?:rupees|rs\.?|\b₹\b)", q)
            budget = float(m[1]) if m else context.get("budget", 100)
            if not m and "strict" in context:
                strict = context["strict"]
            veg = (
                False
                if re.search(r"non[ -]?veg", q)
                else True
                if re.search(r"vegetarian|\bveg\b", q)
                else True
                if c.actor.diet == "vegetarian"
                else None
            )
            context.update(budget=budget, vegetarian=veg, strict=strict)
            if re.search(r"\btwo\b|\bthree\b|combination|combo|\b2 items", q):
                kind = "meals"
                rows = tools.call(
                    "optimize_meal_budget", budget=budget, vegetarian=veg, count=3 if "three" in q else 2, strict=strict
                )
                answer = f"{len(rows)} combinations fit {'strictly under' if strict else 'within'} ₹{budget:g}, ranked by estimated protein."
            else:
                kind = "food"
                meal_match = re.search(r"\b(breakfast|lunch|dinner)\b", q)
                meal = meal_match[1] if meal_match else None
                rows = tools.call(
                    "search_canteen_items",
                    budget=budget,
                    vegetarian=veg,
                    sort="protein" if "protein" in q else "price",
                    category="sweet" if re.search(r"sweet|dessert",q) else None,
                    strict=strict,
                    on=str(on),
                    meal=meal,
                )
                if "highest" in q:
                    rows = rows[:1]
                answer = f"{len(rows)} available option(s) {'strictly under' if strict else 'within'} ₹{budget:g}. Nutrition is estimated unless marked verified."
                if meal:
                    choices = "; ".join(f"{r['name']} ₹{r['price']:g}" + (f" ({r['location']})" if r.get('location') else "") for r in rows[:4])
                    answer = (f"{meal.capitalize()} on {on:%A, %d %B}: " +
                              (choices + f" — all strictly under ₹{budget:g}." if strict and rows else
                               choices + f" — all within ₹{budget:g}." if rows else "No matching published meal availability."))
                    if any(r.get("demo") for r in rows):
                        answer += " Demo Data: sample menu, not verified campus availability."
                if re.search(r"sweet|dessert",q):
                    answer=f"{len(rows)} available menu item(s) labelled sweet or dessert {'strictly under' if strict else 'within'} ₹{budget:g}. I have not inferred ingredients or suitability for a birthday."
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
        elif re.search(r"research|biography|publications|(?:faculty|professor).*notice|notice.*(?:faculty|professor)|\bwho is\s+(?:dr\.?|professor|prof\.?)\s", q):
            route, kind = "Semantic document retrieval", "passages"
            code = context.get("faculty_code")
            named = re.search(r"(?:professor|prof\.?|dr\.?)\s+([a-z][a-z .'-]*?)(?:'s| research| biography| publications| notice|\?|$)", q)
            faculty = (tools.call("get_faculty_profile", name=named[1].strip()) if named
                       else tools.call("get_faculty_profile", code=code) if code else [])
            query = (faculty[0]["name"] + " " + question) if faculty else question
            # A readable faculty profile need not have a separate directory row.
            result = tools.call("search_documents", query=query)
            rows = result["hits"]
            if named:
                name = " ".join(re.findall(r"[a-z0-9]+", named[1].lower()))
                rows = [r for r in rows if " " + name + " " in " " + " ".join(re.findall(r"[a-z0-9]+", r["text"].lower())) + " "]
            answer = "Retrieved faculty evidence; review the quoted source." if rows else "No published evidence matching that faculty question was found."
            if rows and not result["conflicts"]:
                synthesis = self.generate(question, rows)
                if synthesis and not synthesis.insufficient:
                    answer = synthesis.answer
                elif not synthesis:
                    answer = "Published faculty profile evidence:\n\n" + "\n\n".join(r["text"] for r in rows[:2])
        elif re.search(r"office|meet (?:her|him|that|the)|that professor|her contact|his contact", q):
            route = "Exact structured lookup"
            kind = "faculty"
            named = re.search(r"(?:professor|prof\.?|dr\.?)\s+([a-z][a-z .'-]*?)(?:'s| office|\?|$)", q)
            if named and not re.search(r"that professor|this professor", q):
                rows = tools.call("get_faculty_profile", name=named[1].strip())
            elif context.get("faculty_code"):
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
        elif re.search(r"who\s+(?:\w+\s+)?teaches|faculty|professor|coordinator", q) and not re.search(
            r"notice|policy|next class", q
        ):
            route = "Exact structured lookup"
            kind = "faculty"
            term = re.sub(r"^.*?(?:who teaches|coordinator for|faculty for|professor for)\s*", "", q).strip(
                " ?"
            )
            if term in ("ml", "machine learning"):
                term = "Machine Learning"
            subject = re.split(r"\s+(?:to|for)\s+(?:my|our|the)\b", term)[0].strip(" ?")
            subject = {"os": "Operating Systems", "ml": "Machine Learning", "dbms": "Database"}.get(subject, subject)
            named = re.search(r"(?:professor|prof\.?|dr\.?)\s+([a-z][a-z .'-]*?)(?:\?|$)", q)
            rows = (tools.call("get_faculty_profile", subject=subject) if "who teaches" in q
                    else tools.call("get_faculty_profile", name=named[1].strip()) if named
                    else tools.call("get_faculty_profile", query=term if term != q else c.actor.department))
            answer = (
                "Matching teaching faculty. Coordinator assignments are not recorded."
                if "coordinator" in q
                else "Matching faculty from the directory."
            )
            context.pop("faculty_code", None)
            if rows:
                context["faculty_code"] = rows[0]["code"]
        elif re.search(r"clash|overlap|conflict", q) and not re.search(r"notice|circular|document", q):
            route = "Calendar computation"
            kind = "conflicts"
            rows = tools.call("detect_schedule_conflicts", start=str(on))
            answer = f"{len(rows)} schedule overlap(s) in the next seven days."
        elif re.search(
            r"\bexam(?:ination)?s?\b|next (?:internal|assessment)|when.*internal assessment", q
        ) and not re.search(r"circular|notice|document|what.*say", q):
            route = "Cross-source reasoning"
            kind = "events"
            dated = bool(re.search(r"today|tomorrow|\b20\d\d-|monday|tuesday|wednesday|thursday|friday|saturday|sunday", q))
            rows = tools.call("get_examination_schedule", start=str(on) if dated else str(today), days=1 if dated else 90)
            if "next" in q and rows:
                rows = [r for r in rows if r["start"] > c.local_now().replace(tzinfo=None).isoformat()][:1]
            docs = tools.call("search_documents", query="internal assessment examination circular")
            extra_sources.extend(h["source_id"] for h in docs["hits"])
            answer = "Upcoming examinations from the calendar."
            if docs["conflicts"]:
                answer += (
                    " Conflicting examination circulars exist. Confirm the date with the examination office."
                )
        elif "latest" in q and re.search(r"exam(?:ination)?", q) and re.search(r"circular|notice", q):
            route, kind = "Latest circular metadata and retrieval", "passages"
            candidates = tools.call("get_latest_notices",query="exam")
            if candidates:
                result = tools.call("search_documents",query=question,document_id=candidates[0]["id"])
                rows, answer = result["hits"], result["answer"]
                if rows and not result["conflicts"]:
                    synthesis = self.generate(question,rows)
                    if synthesis and not synthesis.insufficient:
                        answer = synthesis.answer
            else:
                answer = "No current authorized examination circular was found."
        elif re.search(
            r"notice|circular|policy|policies|document|assessment|library|scholarship|announcement", q
        ):
            route = "Semantic document retrieval"
            if re.search(r"announcement|latest notices|today.s.*notices", q) and not re.search(
                r"exam|conflict", q
            ):
                kind = "notices"
                rows = tools.call("get_latest_notices")
                if re.search(r"today.s|today", q):
                    rows = [r for r in rows if r["published"] == str(today)]
                answer = "Recent notices relevant to your department."
                if re.search(r"summari[sz]e|summary|important", q):
                    for notice in rows[:4]:
                        evidence = tools.call("search_documents", query=notice["title"], document_id=notice["id"], limit=1)
                        notice["summary"] = evidence["hits"][0]["text"][:350] if evidence["hits"] else "No supporting passage is available."
                    answer = "Extractive summaries of current authorized notices; verify the cited originals."
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
                    synthesis = self.generate(question, rows)
                    if synthesis and not synthesis.insufficient:
                        answer = synthesis.answer
        elif re.search(r"event|workshop|hackathon|seminar|deadline|activit", q):
            route = "Filtered database search"
            kind = "events"
            start = today
            days = 14
            if "this week" in q:
                days = 7 - today.weekday()
            elif "today" in q or (has_explicit_date(q) and on == today):
                start = today
                days = 1
            elif "tomorrow" in q or on == today + timedelta(days=1):
                start = on
                days = 1
            elif any(day in q for day in DAYS) or re.search(r"\b20\d{2}-\d{2}-\d{2}\b", q):
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
                reference = bool(re.search(r"their|his|her|professor", q))
                if reference and not context.get("faculty_code"):
                    row = None
                elif "tomorrow" in q or any(day in q for day in DAYS) or re.search(r"20\d\d-\d\d-\d\d", q):
                    choices = tools.call("get_my_timetable", on=str(on))
                    row = next((r for r in choices if (not reference or r["faculty_code"] == context["faculty_code"])
                                and r["start"] > c.local_now().replace(tzinfo=None).isoformat()), None)
                else:
                    row = tools.call("get_next_class", faculty_code=context.get("faculty_code") if reference else None)
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
                rows = tools.call("get_my_timetable", on=str(on), at=c.local_now().strftime("%H:%M") if re.search(r"current|right now", q) else at)
                answer = f"Your classes on {on:%A, %d %B}."
                if at and rows:
                    answer += " " + "; ".join(
                        f"{r['subject']}, {r['start_time'][:5]}–{r['end_time'][:5]}" +
                        (f", {r['room']}" if r.get('room') else "") +
                        (f", {r['faculty']}" if r.get('faculty') else "") for r in rows
                    ) + "."
            if kind == "classes" and rows:
                context.update(faculty_code=rows[0]["faculty_code"], date=rows[0]["date"], domain="timetable")
        else:
            answer = "I can find campus classes, food, faculty, notices, events and your authorized emails. Try a subject name or “My next class”. I do not have supporting information for that request."
        if not document_id and kind == "classes" and not rows:
            closures = tools.call("get_academic_calendar", start=str(on), days=1)
            closures = [r for r in closures if r["classes_cancelled"]]
            if closures:
                extra_sources.extend(r["source_id"] for r in closures)
                answer = "Classes are cancelled by the published calendar: "+"; ".join(r["title"] for r in closures)
            elif at:
                scheduled = tools.call("get_my_timetable", on=str(on))
                if scheduled:
                    reason = "no_class_at_time"
                    answer = f"You have no scheduled class at {at} on {on:%A, %d %B}."
                    following = next((r for r in scheduled if r["start_time"] > at + ":00"), None)
                    if following:
                        answer += f" Your next class starts at {following['start_time'][:5]}: {following['subject']}."
                        if following.get("room"):
                            answer += f" Room: {following['room']}."
                        extra_sources.append(following["source_id"])
                    else:
                        answer += " There are no later classes in your published timetable for that day."
                        extra_sources.extend(r["source_id"] for r in scheduled)
        if not document_id and not rows and kind not in ("text", "calendar") and reason != "no_class_at_time" and not ("could not find" in answer or "Which professor" in answer or "Classes are cancelled" in answer):
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
        if kind == "passages":
            evidence_status = "retrieved_evidence" if rows else "insufficient_evidence"
            if synthesis and synthesis.insufficient:
                answer = "The retrieved evidence does not support an answer to this question."
                evidence_status = "insufficient_evidence"
        sources = []
        for sid in source_ids:
            source = c.source(sid)
            passages = [r for r in rows if r.get("source_id") == sid and r.get("page")]
            sources.append(
                dict(
                    id=sid,
                    filename=source.filename,
                    demo=source.demo,
                    created_at=source.created_at.isoformat(),
                    url=f"/v1/sources/{sid}",
                    download_allowed=c.original_download_allowed(source),
                    **(dict(title=passages[0]["title"], document_id=passages[0]["document_id"],
                            pages=sorted({r["page"] for r in passages}),
                            chunk_ids=list(dict.fromkeys(r["id"] for r in passages))) if passages else {}),
                )
            )
        elapsed = round((perf_counter() - started) * 1000, 2)
        c.db.add(
            QueryTrace(
                tenant_id=c.actor.tenant_id,
                user_id=c.actor.user_id,
                route=route,
                tools=tools.trace,
                latency_ms=elapsed,
                success=True,
                result_count=len(rows),
            )
        )
        followups = ["Tell me more about that", "What does this document say about deadlines?"] if document_id else (
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
            trace=tools.trace,
            latency_ms=elapsed,
            evidence_status=evidence_status,
            answer_mode="generated" if synthesis and not synthesis.insufficient else "extractive" if kind == "passages" else "structured" if rows else "abstained",
            reason=reason,
            generated_claims=[{**claim.model_dump(), **{k: next(r for r in rows if r["id"] == claim.chunk_id)[k] for k in ("page", "title", "source_id", "document_id")}} for claim in synthesis.claims] if synthesis else [],
        )
