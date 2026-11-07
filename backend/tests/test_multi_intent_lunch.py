from datetime import timedelta
from unittest.mock import Mock

from backend.app.ai import JsonProvider
from backend.app.models import CampusRecord
from backend.app.router import Router, split_question


QUESTION = "What class do I have at 9 AM on Monday, and what can I eat for lunch under ₹60?"


def test_monday_and_lunch_use_both_tools_without_model(env):
    provider = JsonProvider()
    provider.plan = Mock(side_effect=TimeoutError("Provider must not gate deterministic tools"))
    provider.synthesize = Mock(side_effect=TimeoutError("Provider must not gate deterministic tools"))
    result = Router(env.service, provider).ask(QUESTION)
    assert result["kind"] == "multi"
    assert [s["kind"] for s in result["sections"]] == ["classes", "food"]
    calls = result["trace"]
    assert {c["tool"] for c in calls} >= {"get_my_timetable", "search_canteen_items"}
    assert "Your classes on Monday" in result["answer"]
    assert "Lunch on Monday" in result["answer"]
    assert all(r["price"] < 60 for r in result["sections"][1]["records"])
    provider.plan.assert_not_called()
    provider.synthesize.assert_not_called()


def test_lunch_filters_meal_day_and_strict_price(env):
    base = env.db.query(CampusRecord).filter(CampusRecord.kind == "menu").first()
    monday = env.service.local_now().date()
    monday += timedelta(days=(0 - monday.weekday()) % 7)
    for name, meal, price, day in [("Lunch test", "Lunch", 59, 0), ("Boundary test", "Lunch", 60, 0),
                                   ("Breakfast test", "Breakfast", 20, 0), ("Wrong day test", "Lunch", 10, 4),
                                   ("Unknown meal test", "", 15, 0)]:
        env.db.add(CampusRecord(tenant_id=base.tenant_id, source_id=base.source_id, department=base.department,
                               kind="menu", natural_key=name, payload={**base.payload, "name":name,
                               "meal":meal, "price":price, "served_day":day, "served_on":None, "available":True}))
    env.db.flush()
    rows = env.service.menu(budget=60, strict=True, on=monday, meal="lunch")
    names = {r["name"] for r in rows}
    assert "Lunch test" in names
    assert not names & {"Boundary test", "Breakfast test", "Wrong day test", "Unknown meal test"}
    assert rows[0]["meal"].lower() == "lunch"


def test_lunch_without_word_eat_is_independent_intent():
    assert len(split_question("my monday class and lunch under 60 rupees")) == 2
