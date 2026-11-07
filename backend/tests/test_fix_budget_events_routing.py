"""Regression and boundary tests for budget semantics, event scoping, and free-time routing."""

from datetime import datetime, time, timedelta
from backend.app.router import Router, has_explicit_date


def test_budget_within_parsing(env):
    """'within 30 rupees' must parse budget=30 and use inclusive comparison."""
    router = Router(env.service)
    result = router.ask("List available vegetarian canteen items within 30 rupees")
    assert result["kind"] == "food"
    assert result["context"]["budget"] == 30.0
    assert result["context"].get("strict") is False
    assert result["records"]
    assert all(r["price"] <= 30.0 for r in result["records"])
    assert any(r["price"] == 30.0 for r in result["records"])


def test_budget_under_strict_parsing(env):
    """'under 50 rupees' must parse budget=50 and enforce strict price < budget."""
    router = Router(env.service)
    result = router.ask("Find the highest protein vegetarian food under 50 rupees")
    assert result["kind"] == "food"
    assert result["context"]["budget"] == 50.0
    assert result["context"].get("strict") is True
    assert result["records"]
    assert all(r["price"] < 50.0 for r in result["records"])


def test_meals_under_strict_boundary(env):
    """'for under 60 rupees' must not admit any combination with total == 60."""
    router = Router(env.service)
    result = router.ask("Can I get two vegetarian items together for under 60 rupees?")
    assert result["kind"] == "meals"
    assert result["context"]["budget"] == 60.0
    assert result["context"].get("strict") is True
    assert result["records"]
    assert all(r["total"] < 60.0 for r in result["records"])
    assert not any(r["total"] == 60.0 for r in result["records"])


def test_meals_within_inclusive_boundary(env):
    """'within 60 rupees' must admit combinations where total <= 60."""
    router = Router(env.service)
    result = router.ask("Which two vegetarian items give the highest estimated protein within 60 rupees?")
    assert result["kind"] == "meals"
    assert result["context"]["budget"] == 60.0
    assert result["context"].get("strict") is False
    assert result["records"]
    assert all(r["total"] <= 60.0 for r in result["records"])


def test_service_menu_exact_boundary(env):
    """Direct service boundary test: strict excludes price == budget, inclusive includes it."""
    # Find items up to 60 to verify behavior around 60
    inclusive_items = env.service.menu(budget=60, vegetarian=True, strict=False)
    strict_items = env.service.menu(budget=60, vegetarian=True, strict=True)
    exact_60_items = [i for i in inclusive_items if i["price"] == 60.0]
    if exact_60_items:
        assert all(i["id"] not in {s["id"] for s in strict_items} for i in exact_60_items)
    assert all(i["price"] < 60.0 for i in strict_items)
    assert all(i["price"] <= 60.0 for i in inclusive_items)


def test_service_meals_exact_boundary(env):
    """Direct service boundary test: strict meals exclude combo total == budget."""
    inclusive_combos = env.service.meals(budget=60, vegetarian=True, count=2, strict=False)
    strict_combos = env.service.meals(budget=60, vegetarian=True, count=2, strict=True)
    exact_60_combos = [c for c in inclusive_combos if c["total"] == 60.0]
    if exact_60_combos:
        assert all(c["total"] < 60.0 for c in strict_combos)
    assert all(c["total"] <= 60.0 for c in inclusive_combos)
    assert all(c["total"] < 60.0 for c in strict_combos)


def test_event_day_window_today(env):
    """Events query for 'today' must narrow the query window to 1 day."""
    router = Router(env.service)
    result = router.ask("What events are happening today?")
    assert result["kind"] == "events"
    today = str(env.service.local_now().date())
    assert result["records"]
    # Every returned event must overlap with today
    assert all(r["start"][:10] == today for r in result["records"])


def test_event_day_window_tomorrow(env):
    """Events query for 'tomorrow' must narrow the query window to 1 day on tomorrow."""
    router = Router(env.service)
    result = router.ask("What events are happening tomorrow?")
    assert result["kind"] == "events"
    tomorrow = str(env.service.local_now().date() + timedelta(days=1))
    assert result["records"]
    assert all(r["start"][:10] == tomorrow for r in result["records"])


def test_event_empty_day_boundary(env):
    """Querying events on a day with no events should cleanly return empty records."""
    far_future = env.service.local_now().date() + timedelta(days=60)
    events = env.service.events(start=far_future, days=1)
    assert isinstance(events, list)
    assert len(events) == 0


def test_event_midnight_boundary(env):
    """An event starting at tomorrow 00:00:00 must not be returned in today's 1-day window."""
    today = env.service.local_now().date()
    # Check that events() strictly uses end > startT00:00:00 and start < endT00:00:00
    today_events = env.service.events(start=today, days=1)
    tomorrow_iso = f"{today + timedelta(days=1)}T00:00:00"
    for ev in today_events:
        assert ev["start"] < tomorrow_iso


def test_free_time_interval_routing(env):
    """Query asking when one has free time tomorrow between hours must route to slots."""
    router = Router(env.service)
    result = router.ask("When do I have free time tomorrow between 09:00 and 17:00?")
    assert result["route"] == "Calendar computation"
    assert result["kind"] == "slots"
    assert "find_free_time_slots" in [t["tool"] for t in result["trace"]]
    assert result["records"]
    tomorrow = env.service.local_now().date() + timedelta(days=1)
    for slot in result["records"]:
        assert slot["start"].startswith(str(tomorrow))
        assert slot["end"].startswith(str(tomorrow))
        assert slot["minutes"] >= 30


def test_point_availability_routing(env):
    """Specific point-in-time availability query must route to Schedule availability SQL."""
    router = Router(env.service)
    result = router.ask("Am I free tomorrow at 11:00?")
    assert result["route"] == "Schedule availability SQL"
    assert result["kind"] == "availability"
    assert "get_my_timetable" in [t["tool"] for t in result["trace"]]


def test_free_slots_custom_bounds(env):
    """free_slots must respect custom start_time and end_time parameters."""
    tomorrow = env.service.local_now().date() + timedelta(days=1)
    slots = env.service.free_slots(on=tomorrow, min_minutes=30, start_time="10:00", end_time="16:00")
    for slot in slots:
        slot_start = datetime.fromisoformat(slot["start"])
        slot_end = datetime.fromisoformat(slot["end"])
        assert slot_start >= datetime.combine(tomorrow, time(10, 0))
        assert slot_end <= datetime.combine(tomorrow, time(16, 0))


def test_free_slots_overlapping_intervals(env):
    """free_slots must merge overlapping busy intervals without generating inverted slots."""
    tomorrow = env.service.local_now().date() + timedelta(days=1)
    slots = env.service.free_slots(on=tomorrow, min_minutes=30)
    for slot in slots:
        start_dt = datetime.fromisoformat(slot["start"])
        end_dt = datetime.fromisoformat(slot["end"])
        assert end_dt > start_dt
        assert (end_dt - start_dt).total_seconds() / 60 >= 30


def test_compound_date_propagation(env):
    """Compound query with date at the end propagates date to all sections."""
    router = Router(env.service)
    result = router.ask("What classes and campus events do I have tomorrow?")
    assert result["kind"] == "multi"
    assert len(result["sections"]) >= 2
    tomorrow = str(env.service.local_now().date() + timedelta(days=1))
    class_section = next((s for s in result["sections"] if s["kind"] == "classes"), None)
    event_section = next((s for s in result["sections"] if s["kind"] == "events"), None)
    assert class_section is not None
    assert event_section is not None
    assert all(r["date"] == tomorrow for r in class_section["records"])
    assert all(r["start"][:10] == tomorrow for r in event_section["records"])


def test_compound_merge_retains_records_and_sources(env):
    """Merged compound response must retain non-empty records, sources, and claims."""
    router = Router(env.service)
    result = router.ask("What classes and campus events do I have tomorrow?")
    assert result["kind"] == "multi"
    assert result["records"], "Compound result records must not be empty"
    assert len(result["records"]) == sum(len(s["records"]) for s in result["sections"])
    assert result["sources"], "Compound result sources must not be empty"
    for section in result["sections"]:
        for src in section["sources"]:
            assert any(s["id"] == src["id"] for s in result["sources"])


def test_has_explicit_date_helper():
    """Verify explicit date detector handles varied phrasing."""
    assert has_explicit_date("What events are happening today?")
    assert has_explicit_date("What classes do I have tomorrow?")
    assert has_explicit_date("Did I have classes yesterday?")
    assert has_explicit_date("Show timetable for Friday")
    assert has_explicit_date("Check 2026-11-05 schedule")
    assert has_explicit_date("What happens on 15 November 2026?")
    assert not has_explicit_date("What classes do I have?")
    assert not has_explicit_date("Show highest protein food")
