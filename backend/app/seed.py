"""Synthetic data enters through the same validation and ingestion services as real uploads."""

import argparse
import json
from datetime import date, timedelta
from uuid import UUID
from sqlalchemy import select
from backend.app.db import SessionLocal, initialize_local
from backend.app.models import Institution, Membership
from backend.app.auth import resolve_actor
from backend.app.services import CampusService
from backend.app.ingestion import Ingestion
from backend.app.schemas import ImportOptions

DEMO_TENANT = "00000000-0000-4000-8000-000000000001"
SUBJECTS = {
    "AIML": ["Machine Learning", "Python Programming", "Linear Algebra", "Neural Networks", "Data Ethics"],
    "CSE": [
        "Operating Systems",
        "Data Structures",
        "Database Systems",
        "Computer Networks",
        "Software Engineering",
    ],
    "ECE": [
        "Digital Electronics",
        "Signal Processing",
        "Embedded Systems",
        "Circuit Theory",
        "Communication Systems",
    ],
}


def seed(db, user_id, tenant_id=DEMO_TENANT, anchor=None, store=None):
    UUID(user_id)
    UUID(tenant_id)
    anchor = anchor or date.today()
    monday = anchor - timedelta(days=anchor.weekday())
    institution = db.get(Institution, tenant_id)
    if not institution:
        db.add(Institution(id=tenant_id, name="Meridian Institute of Technology", demo=True))
        db.flush()
    member = db.scalar(
        select(Membership).where(Membership.tenant_id == tenant_id, Membership.user_id == user_id)
    )
    if not member:
        db.add(
            Membership(
                tenant_id=tenant_id,
                user_id=user_id,
                name="Aarav",
                role="institution_admin",
                department="AIML",
                semester=3,
                section="A",
                interests="AI, robotics",
                diet="vegetarian",
            )
        )
        db.commit()
    c = CampusService(db, resolve_actor(db, user_id, tenant_id))
    ingestion = Ingestion(c, store)

    def ingest(kind, rows=None, text=None, **metadata):
        filename = (
            "Demo Data "
            + kind
            + (" " + metadata.get("title", "") if text else "")
            + (".txt" if text else ".json")
        )
        content = text.encode() if text else json.dumps(rows, sort_keys=True).encode()
        job = ingestion.receive(filename, content, ImportOptions(kind=kind, demo=True, **metadata))
        if job.status != "completed":
            ingestion.confirm(job)
            ingestion.process(job)
            job.status = "completed"
            db.commit()
        return job

    faculty = []
    names = [
        "Ananya Rao",
        "Vikram Shah",
        "Meera Nair",
        "Arjun Iyer",
        "Kavya Menon",
        "Rohan Desai",
        "Priya Kapoor",
        "Aditya Sen",
    ]
    for di, (dept, subjects) in enumerate(SUBJECTS.items()):
        for i, name in enumerate(names):
            faculty.append(
                dict(
                    code=f"{dept}-{i + 1}",
                    name="Dr. " + name,
                    department=dept,
                    designation="Assistant Professor",
                    subjects=subjects[i % 5],
                    office=f"Block {chr(65 + di)}, Room {210 + i}",
                    office_hours="Mon–Fri, 15:00–16:00",
                    contact=f"faculty{di * 8 + i + 1}@example.invalid",
                )
            )
    ingest("faculty", faculty)
    timetable = []
    slots = [
        ("09:00", "10:00"),
        ("10:00", "11:00"),
        ("11:15", "12:15"),
        ("13:00", "14:00"),
        ("14:00", "15:00"),
    ]
    for di, (dept, subjects) in enumerate(SUBJECTS.items()):
        for sem in [3, 5]:
            for section in ["A", "B"]:
                for day in range(5):
                    for i, (start, end) in enumerate(slots):
                        timetable.append(
                            dict(
                                department=dept,
                                semester=sem,
                                section=section,
                                subject=subjects[(day + i) % 5],
                                faculty_code=f"{dept}-{(day + i) % 5 + 1}",
                                room=f"{chr(65 + di)}-{300 + i}",
                                day=day,
                                start_time=start,
                                end_time=end,
                                effective_from=str(monday - timedelta(days=28)),
                                effective_to=str(monday + timedelta(days=90)),
                            )
                        )
    ingest("timetable", timetable)
    foods = [
        ("Idli & sambar", 35, 7),
        ("Masala dosa", 60, 8),
        ("Poha", 35, 5),
        ("Upma", 35, 6),
        ("Paneer wrap", 90, 21),
        ("Rajma rice", 80, 16),
        ("Chole rice", 75, 15),
        ("Veg thali", 95, 18),
        ("Curd rice", 55, 9),
        ("Lemon rice", 45, 6),
        ("Dal khichdi", 65, 14),
        ("Sprout salad", 50, 13),
        ("Peanut chaat", 40, 10),
        ("Samosa", 20, 4),
        ("Veg sandwich", 50, 8),
        ("Paneer sandwich", 75, 17),
        ("Banana", 10, 1),
        ("Apple", 25, 0),
        ("Fruit bowl", 45, 2),
        ("Milk", 25, 8),
        ("Buttermilk", 20, 3),
        ("Lassi", 35, 6),
        ("Lime water", 15, 0),
        ("Tea", 12, 1),
        ("Coffee", 18, 2),
        ("Egg roll", 65, 17),
        ("Chicken rice", 110, 28),
        ("Chicken sandwich", 85, 23),
        ("Egg bhurji", 55, 18),
        ("Boiled eggs", 30, 12),
        ("Tofu bowl", 100, 24),
        ("Soy curry", 70, 25),
        ("Ragi malt", 30, 5),
        ("Veg noodles", 70, 9),
    ]
    ingest(
        "menu",
        [
            dict(
                name=name,
                price=price,
                vegetarian=not 25 <= i <= 29,
                category="Meal" if price >= 55 else "Snack",
                available=i != 31,
                calories=price * 4,
                protein=protein,
                location="Central Canteen" if i % 2 else "Library Café",
            )
            for i, (name, price, protein) in enumerate(foods)
        ],
    )
    titles = [
        "AI workshop: build your first model",
        "Robotics open lab",
        "Campus hackathon",
        "Placement preparation",
        "Data ethics seminar",
        "Coding club meetup",
        "Startup pitch evening",
        "Signals workshop",
        "Research poster day",
        "Open-source sprint",
        "Internal assessment I",
        "Library book return deadline",
        "Scholarship application deadline",
        "Sports trials",
        "Design thinking studio",
        "AI career seminar",
        "Department town hall",
        "Internal assessment II",
    ]
    events = []
    for i, title in enumerate(titles):
        day = monday + timedelta(days=4 if i == 0 else i % 14)
        start = "14:00" if i % 3 == 0 else "16:00"
        end = "15:30" if i % 3 == 0 else "17:30"
        events.append(
            dict(
                title=title,
                start=f"{day}T{start}",
                end=f"{day}T{end}",
                location="Innovation Hall" if i % 2 == 0 else "Auditorium",
                kind="exam"
                if "assessment" in title
                else "deadline"
                if "deadline" in title
                else "workshop"
                if "workshop" in title
                else "event",
                tags="AI,technical" if i % 2 == 0 else "campus,career",
            )
        )
    ingest("events", events)
    notices = [
        (
            "Internal assessment original",
            "Examination",
            "internal-assessment",
            str(monday + timedelta(days=7)),
            f"Internal assessment I begins on {monday + timedelta(days=7)} at 09:00. Carry student ID.",
            -5,
            None,
        ),
        (
            "Internal assessment revised",
            "Examination",
            "internal-assessment",
            str(monday + timedelta(days=8)),
            f"Revised internal assessment I begins on {monday + timedelta(days=8)}. This conflicts with the previous circular. Confirm with the examination office.",
            -1,
            None,
        ),
        (
            "Previous semester examination",
            "Examination",
            "previous-exams",
            "closed",
            "Previous semester examinations have concluded. Archived circular.",
            -90,
            -30,
        ),
        (
            "AI workshop invitation",
            "Event",
            "ai-workshop",
            "scheduled",
            f"AI workshop on {monday + timedelta(days=4)}, 14:00–15:30 in Innovation Hall.",
            -2,
            None,
        ),
        (
            "Library guidelines",
            "Policy",
            "library",
            "14-days",
            "Students may borrow three books for 14 days. Renewals depend on reservation status.",
            -7,
            None,
        ),
        (
            "Placement aptitude assessment",
            "Placement",
            "placement",
            "registration",
            f"Register for placement aptitude assessment by {monday + timedelta(days=10)}.",
            -3,
            None,
        ),
        (
            "Academic calendar",
            "Academic",
            "semester",
            "calendar",
            f"Teaching continues until {monday + timedelta(days=60)}.",
            -10,
            None,
        ),
        (
            "Scholarship applications",
            "Administrative",
            "scholarship",
            "applications",
            f"Scholarship applications close on {monday + timedelta(days=12)}.",
            -2,
            None,
        ),
        (
            "Laboratory safety",
            "Policy",
            "lab",
            "supervision",
            "Laboratories require faculty supervision and appropriate protective equipment.",
            -8,
            None,
        ),
        (
            "Hackathon registration",
            "Event",
            "hackathon",
            "teams",
            "Campus hackathon teams may have two to four members.",
            -1,
            None,
        ),
        (
            "Project review",
            "Department",
            "project",
            "review",
            "AIML project reviews include a working demonstration and short report.",
            -1,
            None,
        ),
        (
            "Canteen nutrition",
            "Administrative",
            "nutrition",
            "estimates",
            "Nutritional values are demonstration estimates. Ask vendors about allergens.",
            0,
            None,
        ),
    ]
    for title, category, topic, claim, body, offset, expiry in notices:
        ingest(
            "document",
            text="Demo Data — synthetic institution.\n" + title + "\n" + body,
            title=title,
            category=category,
            topic=topic,
            claim=claim,
            published=anchor + timedelta(days=offset),
            expires=anchor + timedelta(days=expiry) if expiry else None,
        )
    ingest(
        "emails",
        [
            dict(
                sender="department@example.invalid",
                subject=title,
                body=f"Demo Data. Department update: {title}. Date: {monday + timedelta(days=i % 14)}. Venue: Innovation Hall.",
                received=f"{anchor - timedelta(days=i % 7)}T10:00:00",
                unread=i % 3 != 0,
            )
            for i, title in enumerate(titles)
        ],
    )
    return tenant_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Seed synthetic data; user ID must belong to an actual Supabase Auth account."
    )
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--tenant-id", default=DEMO_TENANT)
    parser.add_argument("--anchor", type=date.fromisoformat)
    args = parser.parse_args()
    initialize_local()
    with SessionLocal() as db:
        tenant = seed(db, args.user_id, args.tenant_id, args.anchor)
        db.commit()
    print("Demo Data seeded. Tenant ID: " + tenant)
