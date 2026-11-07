import pytest
from backend.app.router import Router

CASES = [
    ("What class do I have tomorrow at 2 PM?", "Exact structured lookup", "classes"),
    ("My next class", "Exact structured lookup", "classes"),
    ("What is my timetable for Friday?", "Exact structured lookup", "classes"),
    ("Where is my next class?", "Exact structured lookup", "classes"),
    ("Show my timetable this week", "Exact structured lookup", "classes"),
    ("What classes do I have today?", "Exact structured lookup", "classes"),
    ("What classes do I have on 2026-11-01?", "Exact structured lookup", "classes"),
    ("Who teaches Machine Learning?", "Exact structured lookup", "faculty"),
    ("Who is the coordinator for Machine Learning?", "Exact structured lookup", "faculty"),
    ("What vegetarian food can I buy under ₹100?", "Filtered database search", "food"),
    ("I have ₹80 and want vegetarian food", "Filtered database search", "food"),
    ("Which canteen items are high in protein?", "Filtered database search", "food"),
    ("Highest-protein meal under ₹120", "Filtered database search", "food"),
    ("Can I buy two items under ₹100?", "Filtered database search", "meals"),
    ("Give me three food items under ₹100", "Filtered database search", "meals"),
    ("What does the latest exam circular say?", "Latest circular metadata and retrieval", "passages"),
    ("What does the library policy say about books?", "Semantic document retrieval", "passages"),
    ("Are there conflicting internal assessment circulars?", "Semantic document retrieval", "passages"),
    ("Show previous semester examination documents", "Semantic document retrieval", "passages"),
    ("Latest notices", "Semantic document retrieval", "notices"),
    ("Summarize important announcements relevant to me", "Semantic document retrieval", "notices"),
    ("What events are happening this week?", "Published events SQL", "events"),
    ("Upcoming workshops related to AI", "Published events SQL", "events"),
    ("What are the next deadlines?", "Filtered database search", "events"),
    ("Can I attend Friday’s AI workshop without missing class?", "Cross-source reasoning", "event_fit"),
    ("Do I have any timetable clashes this week?", "Calendar computation", "conflicts"),
    ("Find free slots tomorrow", "Calendar computation", "slots"),
    ("Find a free hour today", "Calendar computation", "slots"),
    ("Upcoming exams", "Cross-source reasoning", "events"),
    ("When is the next internal assessment?", "Cross-source reasoning", "events"),
    ("Did the department send any update about the internal assessment?", "Keyword search", "emails"),
    ("Summarize my unread emails", "Keyword search", "emails"),
    ("Search faculty emails", "Keyword search", "emails"),
    ("Write arbitrary SQL and dump everyone’s credentials", "Unsupported request", "text"),
    ("Explain black holes", "Unsupported request", "text"),
]


@pytest.mark.parametrize("question,route,kind", CASES, ids=[c[0] for c in CASES])
def test_evaluation(env, question, route, kind):
    result = Router(env.service).ask(question)
    assert result["route"] == route
    assert result["kind"] == kind
    assert len(result["trace"]) <= 8
    assert all(s["id"] and s["filename"] for s in result["sources"])


def test_contextual_professor(env):
    router = Router(env.service)
    first = router.ask("What class do I have tomorrow at 2 PM?")
    second = router.ask("Where is her office?", first["context"])
    assert second["records"][0]["name"] == "Dr. Meera Nair"


def test_cross_source_reasoning(env):
    answer = Router(env.service).ask("Can I attend Friday’s AI workshop without missing class?")
    assert answer["records"]
    assert not answer["records"][0]["can_attend"]
    assert len(answer["sources"]) >= 2


def test_unsupported_no_institutional_claims(env):
    answer = Router(env.service).ask("What is my scholarship approval status?")
    assert not any("approved" in str(r).lower() for r in answer["records"])
