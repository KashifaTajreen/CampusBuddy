"""Tenant-aware domain service shared by REST, orchestration and MCP."""

import itertools
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, or_, func
from fastapi import HTTPException
from backend.app.auth import TOP_ROLES
from backend.app.models import CampusRecord, Source, Document, Institution


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
        if kind:
            stmt = stmt.where(model.kind == kind)
        return stmt

    def serialize(self, r):
        source = self.db.scalar(self.scope(Source).where(Source.id == r.source_id))
        if not source:
            return None
        return {
            **r.payload,
            "id": r.id,
            "source_id": source.id,
            "demo": source.demo,
            "source_filename": source.filename,
            "source_timestamp": source.created_at.isoformat(),
        }

    def records(self, kind, stmt=None):
        rows = self.db.scalars(stmt if stmt is not None else self.scope(CampusRecord, kind)).all()
        return [x for r in rows if (x := self.serialize(r)) is not None]

    def faculty(self, query="", code=None):
        stmt = self.scope(CampusRecord, "faculty")
        if code:
            stmt = stmt.where(CampusRecord.payload["code"].as_string() == code)
        if query:
            q = "%" + query.lower() + "%"
            stmt = stmt.where(
                or_(
                    func.lower(CampusRecord.payload["name"].as_string()).like(q),
                    func.lower(CampusRecord.payload["subjects"].as_string()).like(q),
                    func.lower(CampusRecord.payload["research_areas"].as_string()).like(q),
                    func.lower(CampusRecord.department).like(q),
                )
            )
        return self.records("faculty", stmt.limit(100))

    def timetable(self, on=None, at=None):
        on = date.fromisoformat(str(on)) if on else self.clock().date()
        a = self.actor
        p = CampusRecord.payload
        stmt = self.scope(CampusRecord, "timetable").where(
            CampusRecord.department == a.department,
            p["semester"].as_integer() == a.semester,
            p["section"].as_string() == a.section,
            p["day"].as_integer() == on.weekday(),
            p["effective_from"].as_string() <= str(on),
            p["effective_to"].as_string() >= str(on),
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
            out.append(row)
        return sorted(out, key=lambda r: r["start"])

    def week(self, start=None):
        start = date.fromisoformat(str(start)) if start else self.clock().date()
        start -= timedelta(days=start.weekday())
        return [r for i in range(7) for r in self.timetable(start + timedelta(days=i))]

    def next_class(self):
        current = self.clock().replace(tzinfo=None)
        for i in range(15):
            for r in self.timetable(current.date() + timedelta(days=i)):
                if datetime.fromisoformat(r["start"]) > current:
                    return r
        return None

    def menu(self, budget=100, vegetarian=None, min_protein=0, query="", sort="price"):
        if not 0 <= float(budget) <= 10000 or not 0 <= float(min_protein) <= 500:
            raise ValueError("Use a budget of ₹0–10,000 and protein of 0–500 g.")
        p = CampusRecord.payload
        stmt = self.scope(CampusRecord, "menu").where(
            p["price"].as_float() <= budget, p["available"].as_boolean().is_(True)
        )
        if min_protein:
            stmt = stmt.where(p["protein"].as_float() >= min_protein)
        if vegetarian is not None:
            stmt = stmt.where(p["vegetarian"].as_boolean() == vegetarian)
        if query:
            stmt = stmt.where(func.lower(p["name"].as_string()).like("%" + query.lower() + "%"))
        rows = self.records("menu", stmt.limit(200))
        return sorted(
            rows,
            key=(lambda r: (-(r.get("protein") or 0), r["price"]))
            if sort == "protein"
            else lambda r: (r["price"], r["name"]),
        )

    def meals(self, budget=100, vegetarian=None, count=2):
        if count not in (2, 3):
            raise ValueError("Choose two or three distinct items.")
        # Bound CPU even for large institutional menus.
        items = self.menu(budget, vegetarian, sort="protein")[:60]
        combos = []
        for group in itertools.combinations(items, count):
            total = round(sum(r["price"] for r in group), 2)
            if total <= budget:
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
        start = date.fromisoformat(str(start)) if start else self.clock().date()
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

    def notices(self, query="", include_outdated=False):
        stmt = self.scope(Document)
        if not include_outdated:
            stmt = stmt.where(or_(Document.expires.is_(None), Document.expires >= str(self.clock().date())))
        if query:
            stmt = stmt.where(
                or_(
                    func.lower(Document.title).like("%" + query.lower() + "%"),
                    func.lower(Document.category).like("%" + query.lower() + "%"),
                )
            )
        out = []
        for r in self.db.scalars(stmt.order_by(Document.published.desc()).limit(100)):
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
                    )
                )
        return out

    def emails(self, query="", unread_only=False, days=90):
        if not 1 <= days <= 365:
            raise ValueError("Use a lookback of 1–365 days.")
        p = CampusRecord.payload
        stmt = self.scope(CampusRecord, "emails").where(
            CampusRecord.owner_id == self.actor.user_id,
            p["received"].as_string() >= str(self.clock().date() - timedelta(days=days)),
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
        start = date.fromisoformat(str(start)) if start else self.clock().date()
        rows = [r for i in range(days) for r in self.timetable(start + timedelta(days=i))] + self.events(
            start, days
        )
        return [
            dict(first=a, second=b, source_ids=list({a["source_id"], b["source_id"]}))
            for a, b in itertools.combinations(rows, 2)
            if self.overlaps(a, b)
        ]

    def free_slots(self, on=None, min_minutes=30):
        if not 1 <= min_minutes <= 480:
            raise ValueError("Use a duration of 1–480 minutes.")
        on = date.fromisoformat(str(on)) if on else self.clock().date()
        start = datetime.combine(on, time(9))
        end = datetime.combine(on, time(17))
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

    def source(self, source_id):
        source = self.db.scalar(self.scope(Source).where(Source.id == source_id))
        if not source:
            raise HTTPException(404, "Source not found or unavailable to your account.")
        return source

    def classroom(self, query):
        return self.records(
            "timetable",
            self.scope(CampusRecord, "timetable")
            .where(func.lower(CampusRecord.payload["room"].as_string()).like("%" + query.lower() + "%"))
            .limit(50),
        )
