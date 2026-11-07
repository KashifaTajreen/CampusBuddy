"""Tenant-aware domain service shared by REST, orchestration and MCP."""

import itertools
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, or_, func
from fastapi import HTTPException
from backend.app.auth import TOP_ROLES
from backend.app.models import CampusRecord, Source, Document, DocumentChunk, Institution
from backend.app.cache import SharedCache


class CampusService:
    def __init__(self, session, actor, clock=None):
        self.db = session
        self.actor = actor
        institution = session.get(Institution, actor.tenant_id)
        self.zone = ZoneInfo(institution.timezone if institution else "Asia/Kolkata")
        self.clock = clock or (lambda: datetime.now(self.zone))

    def scope(self, model, kind=None):
        stmt = select(model).where(model.tenant_id == self.actor.tenant_id)
        if hasattr(model, "owner_id"):
            stmt = stmt.where(or_(model.owner_id.is_(None), model.owner_id == self.actor.user_id))
        if hasattr(model, "department") and self.actor.role not in TOP_ROLES:
            stmt = stmt.where(model.department.in_(["All", self.actor.department]))
        if hasattr(model, "semester") and model is not Institution and self.actor.role not in TOP_ROLES:
            if self.actor.role != "department_admin":
                stmt = stmt.where(or_(model.semester.is_(None) & (model.section == "All"),
                    (model.semester == self.actor.semester) & (model.section == self.actor.section)))
        if kind:
            stmt = stmt.where(model.kind == kind)
        if model is Source:
            stmt = stmt.where(Source.publication_status == "published", Source.file_state == "active")
        elif model in (CampusRecord, Document):
            # A row is readable only through an active, identically owned source.
            stmt = stmt.where(
                model.source_id.in_(self.scope(Source).with_only_columns(Source.id)),
                select(Source.id).where(
                    Source.id == model.source_id,
                    Source.tenant_id == model.tenant_id,
                    Source.owner_id.is_not_distinct_from(model.owner_id),
                    Source.publication_status == "published",
                ).exists()
            )
            if model is CampusRecord:
                stmt = stmt.where(select(Source.id).where(Source.id == model.source_id,
                    or_(Source.kind == model.kind, (Source.kind == "google_calendar") & (model.kind == "events") & model.owner_id.is_not(None)),
                    Source.tenant_id == model.tenant_id).exists())
        elif model is DocumentChunk:
            stmt = stmt.where(DocumentChunk.document_id.in_(self.scope(Document).with_only_columns(Document.id)))
            if self.actor.role not in {"institution_admin", "department_admin", "section_admin"}:
                from backend.app.document_rows import identity_match
                public_or_owned = select(Source.id).join(Document, Document.source_id == Source.id).where(
                    Document.id == DocumentChunk.document_id, Source.tenant_id == self.actor.tenant_id,
                    or_(Source.student_records.is_(False), Source.owner_id == self.actor.user_id)).exists()
                permitted = identity_match(DocumentChunk.student_numbers, self.actor.student_number,
                    self.db.bind.dialect.name) if self.actor.student_number else False
                stmt = stmt.where(or_(public_or_owned, permitted))
        return stmt

    def serialize(self, r, source=None):
        source = source or self.db.scalar(self.scope(Source).where(Source.id == r.source_id))
        if not source:
            return None
        return {
            **r.payload,
            "id": r.id,
            "source_id": source.id,
            "demo": source.demo,
            "source_filename": source.filename,
            "source_timestamp": source.created_at.isoformat(),
            "original_download_allowed": self.original_download_allowed(source),
        }

    def records(self, kind, stmt=None):
        statement = stmt if stmt is not None else self.scope(CampusRecord, kind)
        cache = SharedCache(self, kind, statement)
        cached = cache.read()
        if cached is not None:
            # Public records can share a cache, but original-file permissions can
            # depend on the uploader's identity (not just department/role).
            sources = {s.id: s for s in self.db.scalars(self.scope(Source)
                .where(Source.id.in_({r["source_id"] for r in cached})))} if cached else {}
            return [{**r, "original_download_allowed": self.original_download_allowed(sources[r["source_id"]])}
                    for r in cached if r["source_id"] in sources]
        rows = self.db.scalars(statement).all()
        sources = {s.id: s for s in self.db.scalars(self.scope(Source).where(Source.id.in_({r.source_id for r in rows})))} if rows else {}
        result = [self.serialize(r, sources[r.source_id]) for r in rows if r.source_id in sources]
        cache.write(result, private=any(r.owner_id is not None for r in rows))
        return result

    def local_now(self):
        value = self.clock()
        return value.astimezone(self.zone) if value.tzinfo else value.replace(tzinfo=self.zone)

    def faculty(self, query="", code=None, name=None, subject=None):
        if subject:
            # Resolve teaching assignments only through the student's effective section.
            p = CampusRecord.payload
            today = str(self.local_now().date())
            assignments = self.records("timetable", self.scope(CampusRecord,"timetable").where(
                CampusRecord.department == self.actor.department, p["semester"].as_integer() == self.actor.semester,
                p["section"].as_string() == self.actor.section,
                or_(p['effective_from'].as_string().is_(None),p['effective_from'].as_string()<=today),
                or_(p['effective_to'].as_string().is_(None),p['effective_to'].as_string()>=today),
                func.lower(p["subject"].as_string()).contains(subject.lower(),autoescape=True)))
            codes = {r["faculty_code"] for r in assignments}
            return [dict(r, source_ids=list({r["source_id"]} | {a["source_id"] for a in assignments if a["faculty_code"] == r["code"]}))
                    for r in self.faculty() if r["code"] in codes]
        stmt = self.scope(CampusRecord, "faculty")
        if name:
            stmt = stmt.where(func.lower(CampusRecord.payload["name"].as_string()).contains(name.lower(), autoescape=True))
        if code:
            stmt = stmt.where(CampusRecord.payload["code"].as_string() == code)
        if query:
            q = "%" + query.lower() + "%"
            stmt = stmt.where(
                or_(
                    func.lower(CampusRecord.payload["name"].as_string()).like(q),
                    func.lower(CampusRecord.payload["subjects"].as_string()).like(q),
                    func.lower(CampusRecord.department).like(q),
                )
            )
        return self.records("faculty", stmt.limit(100))

    def timetable(self, on=None, at=None):
        on = date.fromisoformat(str(on)) if on else self.local_now().date()
        if any(r["classes_cancelled"] for r in self.academic_dates(on, 1)):
            return []
        a = self.actor
        p = CampusRecord.payload
        stmt = self.scope(CampusRecord, "timetable").where(
            CampusRecord.department == a.department,
            p["semester"].as_integer() == a.semester,
            p["section"].as_string() == a.section,
            p["day"].as_integer() == on.weekday(),
            or_(p['effective_from'].as_string().is_(None),p['effective_from'].as_string()<=str(on)),
            or_(p['effective_to'].as_string().is_(None),p['effective_to'].as_string()>=str(on)),
        )
        if at:
            normalized = time.fromisoformat(at).isoformat()
            stmt = stmt.where(
                p["start_time"].as_string() <= normalized, p["end_time"].as_string() > normalized
            )
        rows = self.records("timetable", stmt)
        exceptions = self.records("exceptions")
        faculty = {r["code"]: r for r in self.faculty()}
        out = []
        for row in rows:
            exception = next(
                (e for e in exceptions if e["timetable_id"] == row["id"] and e["date"] == str(on)), None
            )
            if exception and exception["cancelled"]:
                continue
            if exception and exception.get("room"):
                row["room"] = exception["room"]
            row.update(date=str(on), start=f"{on}T{row['start_time']}", end=f"{on}T{row['end_time']}")
            f = faculty.get(row["faculty_code"])
            if f:
                row.update(
                    faculty=f["name"],
                    office=f["office"],
                    office_hours=f["office_hours"],
                    faculty_source_id=f["source_id"],
                )
            elif row.get('faculty_name'):
                row.update(faculty=row['faculty_name'],faculty_source_id=row['source_id'])
            out.append(row)
        return sorted(out, key=lambda r: r["start"])

    def week(self, start=None):
        start = date.fromisoformat(str(start)) if start else self.local_now().date()
        start -= timedelta(days=start.weekday())
        return [r for i in range(7) for r in self.timetable(start + timedelta(days=i))]

    def next_class(self, faculty_code=None):
        current = self.local_now().replace(tzinfo=None)
        for i in range(15):
            for r in self.timetable(current.date() + timedelta(days=i)):
                if datetime.fromisoformat(r["start"]) > current and (not faculty_code or r["faculty_code"] == faculty_code):
                    return r
        return None

    def menu(self, budget=100, vegetarian=None, min_protein=0, query="", sort="price", category=None, strict=False,
             on=None, meal=None):
        if not 0 <= float(budget) <= 10000 or not 0 <= float(min_protein) <= 500:
            raise ValueError("Use a budget of ₹0–10,000 and protein of 0–500 g.")
        p = CampusRecord.payload
        on = date.fromisoformat(str(on)) if on else self.local_now().date()
        if meal not in (None, "breakfast", "lunch", "dinner"):
            raise ValueError("Choose breakfast, lunch or dinner.")
        price_cond = p["price"].as_float() < budget if strict else p["price"].as_float() <= budget
        stmt = self.scope(CampusRecord, "menu").where(
            price_cond, p["available"].as_boolean().is_(True),
            or_(p["served_on"].as_string().is_(None), p["served_on"].as_string() == str(on)),
            or_(p['served_day'].as_integer().is_(None),p['served_day'].as_integer()==on.weekday())
        )
        if meal:
            # Unknown availability is not evidence that an item is served at lunch.
            stmt = stmt.where(func.lower(func.trim(p["meal"].as_string())).in_([meal, "all day"]))
        if min_protein:
            stmt = stmt.where(p["protein"].as_float() >= min_protein)
        if category == "sweet":
            stmt=stmt.where(or_(func.lower(p["category"].as_string()).contains("sweet"),
                               func.lower(p["category"].as_string()).contains("dessert")))
        if vegetarian is not None:
            stmt = stmt.where(p["vegetarian"].as_boolean() == vegetarian)
        if query:
            stmt = stmt.where(func.lower(p["name"].as_string()).like("%" + query.lower() + "%"))
        rows = self.records("menu", stmt.limit(200))
        return sorted(
            rows,
            key=(lambda r: (-(r.get("protein") or 0), r["price"]))
            if sort == "protein"
            else lambda r: (0 if meal and r.get("meal", "").strip().lower() == meal else 1,
                            r["price"], r["name"]),
        )

    def meals(self, budget=100, vegetarian=None, count=2, category=None, strict=False):
        if count not in (2, 3):
            raise ValueError("Choose two or three distinct items.")
        # Bound CPU even for large institutional menus.
        items = self.menu(budget, vegetarian, sort="protein", category=category, strict=strict)[:60]
        combos = []
        for group in itertools.combinations(items, count):
            total = round(sum(r["price"] for r in group), 2)
            if total < budget if strict else total <= budget:
                combos.append(
                    {
                        "items": list(group),
                        "total": total,
                        "protein": round(sum(r.get("protein") or 0 for r in group), 1),
                        "demo": any(r["demo"] for r in group),
                        "source_ids": list({r["source_id"] for r in group}),
                    }
                )
        return sorted(combos, key=lambda r: (-r["protein"], r["total"]))[:10]

    def events(self, start=None, days=14, query="", kind=None):
        if not 1 <= int(days) <= 90:
            raise ValueError("Use a range of 1–90 days.")
        start = date.fromisoformat(str(start)) if start else self.local_now().date()
        end = start + timedelta(days=days)
        p = CampusRecord.payload
        stmt = self.scope(CampusRecord, "events").where(
            p["end"].as_string() > str(start) + "T00:00:00", p["start"].as_string() < str(end) + "T00:00:00"
        )
        if kind:
            stmt = stmt.where(p["kind"].as_string() == kind)
        if query:
            stmt = stmt.where(
                or_(
                    func.lower(p["title"].as_string()).like("%" + query.lower() + "%"),
                    func.lower(p["tags"].as_string()).like("%" + query.lower() + "%"),
                )
            )
        return sorted(self.records("events", stmt.limit(300)), key=lambda r: r["start"])

    def event_listing(self,start=None,days=14,query='',kind=None,category=None,status='all',include_undated=True):
        if not 1<=int(days)<=90 or status not in ('all','upcoming','ongoing','past','undated'):
            raise ValueError('Choose an event status and a date range of 1–90 days.')
        p=CampusRecord.payload
        first=func.coalesce(p['start_date'].as_string(),func.substr(p['start'].as_string(),1,10))
        last=func.coalesce(p['end_date'].as_string(),func.substr(p['end'].as_string(),1,10),first)
        today=str(self.local_now().date())
        statement=self.scope(CampusRecord,'events').where(CampusRecord.owner_id.is_(None))
        if start:
            on=date.fromisoformat(str(start))
            until=on+timedelta(days=days)
            dated=(last>=str(on))&(first<str(until))
            statement=statement.where(or_(dated,first.is_(None)) if include_undated else dated)
        elif not include_undated:
            statement=statement.where(first.is_not(None))
        if status=='undated':
            statement=statement.where(first.is_(None))
        elif status=='upcoming':
            statement=statement.where(first>=today)
        elif status=='ongoing':
            statement=statement.where(first<=today,last>=today)
        elif status=='past':
            statement=statement.where(or_(last<today,p['end'].as_string()<self.local_now().replace(tzinfo=None).isoformat()))
        if kind:
            statement=statement.where(p['kind'].as_string()==kind)
        if category:
            statement=statement.where(func.lower(p['category'].as_string()).contains(category.lower()))
        if query:
            term=query.lower()
            statement=statement.where(or_(*[func.lower(p[k].as_string()).contains(term) for k in ('title','category','tags','organizer','organizing_club')]))
        rows=self.records('events',statement.limit(300))
        documents={d.source_id:d for d in self.db.scalars(self.scope(Document).where(
            Document.source_id.in_({row['source_id'] for row in rows}),Document.category=='Campus Event'))} if rows else {}
        now=self.local_now().replace(tzinfo=None)
        def enrich(row):
            row=dict(row)
            first=row.get('start_date') or (row.get('start') or '')[:10] or None
            last=row.get('end_date') or (row.get('end') or '')[:10] or first
            if not first:
                state='undated'
            elif row.get('end') and datetime.fromisoformat(row['end'])<now:
                state='past'
            elif first>today or (row.get('start') and datetime.fromisoformat(row['start'])>now):
                state='upcoming'
            elif last<today:
                state='past'
            else:
                state='ongoing'
            deadline=row.get('registration_deadline')
            registration='not_announced'
            if deadline:
                registration='closed' if (datetime.fromisoformat(deadline)<now if 'T' in deadline else deadline<today) else 'deadline_announced'
            document=documents.get(row['source_id'])
            return {**row,'start_date':first,'end_date':row.get('end_date') or (row.get('end') or '')[:10] or None,
                    'start_time':row.get('start_time') or (row.get('start') or '')[11:19] or None,
                    'end_time':row.get('end_time') or (row.get('end') or '')[11:19] or None,'event_status':state,
                    'registration_status':registration,'document_id':document.id if document else None}
        result=[enrich(row) for row in rows]
        if status!='all':
            result=[row for row in result if row['event_status']==status]
        return sorted(result,key=lambda r:(r['event_status']=='undated',r.get('start_date') or '9999',r['title']))

    def event_record(self,event_id):
        record=self.db.scalar(self.scope(CampusRecord,'events').where(CampusRecord.id==event_id,CampusRecord.owner_id.is_(None)))
        if not record:
            raise HTTPException(404,'Published event not found in your authorized scope.')
        return record

    def academic_dates(self, start=None, days=30):
        if not 1 <= days <= 366:
            raise ValueError("Choose 1–366 calendar days.")
        start = date.fromisoformat(str(start)) if start else self.local_now().date()
        end = start+timedelta(days=days-1)
        p = CampusRecord.payload
        stmt = self.scope(CampusRecord, "calendar").where(p["start_on"].as_string() <= str(end),
            p["end_on"].as_string() >= str(start))
        return sorted(self.records("calendar", stmt.limit(500)), key=lambda r:r["start_on"])

    def notices(self, query="", include_outdated=False):
        stmt = self.scope(Document).where(Document.published <= str(self.local_now().date()))
        stmt=stmt.where(~select(Source.id).where(Source.id==Document.source_id,Source.kind=='events',Source.tenant_id==Document.tenant_id).exists())
        if not include_outdated:
            stmt = stmt.where(or_(Document.expires.is_(None), Document.expires >= str(self.local_now().date())))
        if query:
            stmt = stmt.where(
                or_(
                    func.lower(Document.title).like("%" + query.lower() + "%"),
                    func.lower(Document.category).like("%" + query.lower() + "%"),
                )
            )
        # Notice dates have day precision. Later publications on the same date
        # must win deterministically for "latest circular" routing.
        published_at = select(Source.published_at).where(Source.id == Document.source_id,
            Source.tenant_id == Document.tenant_id).correlate(Document).scalar_subquery()
        created_at = select(Source.created_at).where(Source.id == Document.source_id,
            Source.tenant_id == Document.tenant_id).correlate(Document).scalar_subquery()
        statement = stmt.order_by(Document.published.desc(), published_at.desc().nulls_last(),
            created_at.desc(), Document.id).limit(100)
        cache = SharedCache(self, "notices", statement)
        cached = cache.read()
        if cached is not None:
            return cached
        out = []
        documents = list(self.db.scalars(statement))
        for r in documents:
            source = self.db.scalar(self.scope(Source).where(Source.id == r.source_id))
            if source:
                out.append(
                    dict(
                        id=r.id,
                        title=r.title,
                        category=r.category,
                        published=r.published,
                        expires=r.expires,
                        version=r.version,
                        supersedes_id=r.supersedes_id,
                        source_id=r.source_id,
                        demo=source.demo,
                        source_filename=source.filename,
                        source_timestamp=source.created_at.isoformat(),
                        original_download_allowed=self.original_download_allowed(source),
                    )
                )
        cache.write(out, private=any(r.owner_id is not None for r in documents))
        return out

    def emails(self, query="", unread_only=False, days=90):
        if not 1 <= days <= 365:
            raise ValueError("Use a lookback of 1–365 days.")
        p = CampusRecord.payload
        stmt = self.scope(CampusRecord, "emails").where(
            CampusRecord.owner_id == self.actor.user_id,
            p["received"].as_string() >= str(self.local_now().date() - timedelta(days=days)),
        )
        if unread_only:
            stmt = stmt.where(p["unread"].as_boolean().is_(True))
        if query:
            stmt = stmt.where(
                or_(
                    func.lower(p["subject"].as_string()).like("%" + query.lower() + "%"),
                    func.lower(p["body"].as_string()).like("%" + query.lower() + "%"),
                    func.lower(p["sender"].as_string()).like("%" + query.lower() + "%"),
                )
            )
        return sorted(self.records("emails", stmt.limit(50)), key=lambda r: r["received"], reverse=True)

    @staticmethod
    def overlaps(a, b):
        return a["start"] < b["end"] and b["start"] < a["end"]

    def event_fit(self, event):
        clashes = [r for r in self.timetable(event["start"][:10]) if self.overlaps(r, event)]
        return dict(
            event=event,
            clashes=clashes,
            can_attend=not clashes,
            source_ids=list({event["source_id"]} | {r["source_id"] for r in clashes}),
        )

    def conflicts(self, start=None, days=7):
        if not 1 <= days <= 31:
            raise ValueError("Check 1–31 days at a time.")
        start = date.fromisoformat(str(start)) if start else self.local_now().date()
        rows = [r for i in range(days) for r in self.timetable(start + timedelta(days=i))] + self.events(
            start, days
        )
        return [
            dict(first=a, second=b, source_ids=list({a["source_id"], b["source_id"]}))
            for a, b in itertools.combinations(rows, 2)
            if self.overlaps(a, b)
        ]

    def free_slots(self, on=None, min_minutes=30, start_time=None, end_time=None):
        if not 1 <= min_minutes <= 480:
            raise ValueError("Use a duration of 1–480 minutes.")
        on = date.fromisoformat(str(on)) if on else self.local_now().date()
        if not self.timetable_coverage(on):
            return []
        st = time.fromisoformat(start_time) if start_time else time(9)
        et = time.fromisoformat(end_time) if end_time else time(17)
        start = datetime.combine(on, st)
        end = datetime.combine(on, et)
        rows = self.timetable(on) + self.events(on, 1)
        busy = sorted(
            (max(start, datetime.fromisoformat(r["start"])), min(end, datetime.fromisoformat(r["end"])))
            for r in rows
            if r["start"] < end.isoformat() and r["end"] > start.isoformat()
        )
        slots = []
        cursor = start
        for a, b in busy + [(end, end)]:
            if (a - cursor).total_seconds() / 60 >= min_minutes:
                slots.append(
                    dict(
                        start=cursor.isoformat(),
                        end=a.isoformat(),
                        minutes=int((a - cursor).total_seconds() / 60),
                        source_ids=list({r["source_id"] for r in rows}),
                    )
                )
            cursor = max(cursor, b)
        return slots

    def timetable_coverage(self, on):
        """A missing row on a day is meaningful only inside a known term schedule."""
        p=CampusRecord.payload
        return list(self.db.scalars(self.scope(CampusRecord,"timetable").where(
            CampusRecord.department==self.actor.department, p["semester"].as_integer()==self.actor.semester,
            p["section"].as_string()==self.actor.section,
            or_(p['effective_from'].as_string().is_(None),p['effective_from'].as_string()<=str(on)),
            or_(p['effective_to'].as_string().is_(None),p['effective_to'].as_string()>=str(on))).with_only_columns(CampusRecord.source_id).distinct()))

    def document_query(self, include_outdated=False):
        stmt = self.scope(Document)
        if not include_outdated:
            stmt = stmt.where((Document.expires.is_(None)) | (Document.expires >= str(self.local_now().date())))
        return stmt

    def selected_document(self, document_id, include_outdated=False):
        document = self.db.scalar(self.document_query(include_outdated).where(Document.id == document_id))
        if not document:
            raise HTTPException(404, "The selected document is unavailable. Choose another document or clear the selection.")
        return document

    def document_metadata(self, document):
        return dict(id=document.id, title=document.title, source_id=document.source_id,
                    category=document.category, published=document.published, expires=document.expires)

    def selectable_documents(self, limit=100, offset=0):
        rows = self.db.scalars(self.document_query().order_by(Document.title, Document.id)
                               .offset(offset).limit(limit + 1)).all()
        return dict(documents=[self.document_metadata(d) for d in rows[:limit]],
                    next_offset=offset + limit if len(rows) > limit else None)

    def source(self, source_id):
        source = self.db.scalar(self.scope(Source).where(Source.id == source_id))
        if not source:
            raise HTTPException(404, "Source not found or unavailable to your account.")
        return source

    def management_source(self, source_id, lock=False):
        """Explicit administrative/owner access; never use this for student retrieval."""
        stmt = select(Source).where(Source.id == source_id, Source.tenant_id == self.actor.tenant_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        source = self.db.scalar(stmt)
        if not source:
            raise HTTPException(404, "Source not found or unavailable to your account.")
        if source.owner_id is not None:
            if source.owner_id != self.actor.user_id:
                raise HTTPException(404, "Source not found or unavailable to your account.")
        else:
            self.actor.check_source_write(source)
            if (source.kind != "document" and self.actor.role not in TOP_ROLES
                    and source.uploaded_by != self.actor.user_id):
                raise HTTPException(403, "Unredacted structured-source review requires an institution administrator.")
        return source

    def canteen_source(self, source_id):
        """Replacement metadata only; never grants another uploader's raw preview."""
        self.actor.check_department_write("All", "menu")
        source = self.source(source_id)
        if source.kind != "menu" or source.owner_id or source.department != "All" or source.semester is not None or source.section != "All":
            raise HTTPException(403, "Only the published campus-wide canteen menu can be managed here.")
        if self.actor.role == "canteen_manager":
            if self.db.bind.dialect.name == "postgresql":
                from sqlalchemy import text
                safe = self.db.scalar(text("SELECT campus_canteen_shared(:sid)"), {"sid": source.id})
            else:
                safe = not self.db.scalar(select(CampusRecord.id).where(CampusRecord.source_id == source.id,
                    CampusRecord.tenant_id == self.actor.tenant_id,
                    or_(CampusRecord.department != "All", CampusRecord.semester.is_not(None))).limit(1))
            if not safe:
                raise HTTPException(403, "A College Admin must manage this legacy menu with restricted rows.")
        return source

    def original_download_allowed(self, source):
        if source.student_records and source.owner_id is None:
            return self.actor.role in {"institution_admin", "department_admin", "section_admin"}
        if source.owner_id is not None:
            return source.owner_id == self.actor.user_id
        # A structured file may contain multiple departments or unmapped columns.
        # Only institution-wide admins may download its entire unredacted bytes.
        return (source.kind == "document" or self.actor.role in TOP_ROLES or
                (source.kind == "menu" and self.actor.role == "canteen_manager" and source.uploaded_by == self.actor.user_id))

    def original_source(self, source_id):
        try:
            source = self.source(source_id)
        except HTTPException:
            source = self.management_source(source_id)
            if source.publication_status in ("revoked", "rejected", "replaced"):
                raise HTTPException(404, "Source is no longer available.")
        if source.file_state != "active":
            raise HTTPException(404, "File is deleted or unavailable.")
        if not self.original_download_allowed(source):
            raise HTTPException(
                403, "This unredacted original is restricted to institution administrators. Use the authorized records instead."
            )
        return source

    def classroom(self, query):
        return self.records(
            "timetable",
            self.scope(CampusRecord, "timetable")
            .where(func.lower(CampusRecord.payload["room"].as_string()).like("%" + query.lower() + "%"))
            .limit(50),
        )
