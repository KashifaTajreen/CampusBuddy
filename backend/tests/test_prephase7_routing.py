from datetime import date, timedelta
import pytest
from backend.app.router import Router, parse_date
from backend.tests.test_product_workflows import member


@pytest.mark.parametrize("question,kind",[
    ("Kal am I free at 4 PM?","availability"),
    ("What's my next class?","classes"),
    ("Nanna next class yavaga?","classes"),
    ("Aaj mera exam hai kya?","events"),
    ("Tomorrow do I have any class?","classes"),
    ("Is November 14 a holiday?","calendar"),
])
def test_supported_mixed_language_routes(env,question,kind):
    result=Router(env.service).ask(question)
    assert result["kind"]==kind and result["provider_status"]=="disabled"
    if kind=="availability":
        assert result["records"][0]["date"]==str(env.service.local_now().date()+timedelta(days=1))
        assert result["language"]=="en"


def test_kal_ambiguity_and_past_context(env):
    unclear=Router(env.service).ask("Kal class?")
    assert unclear["reason"]=="ambiguous_date" and not unclear["trace"]
    result=Router(env.service).ask("Kal was I free at 4pm?")
    assert result["records"][0]["date"]==str(env.service.local_now().date()-timedelta(days=1))
    assert parse_date("is november 14 a holiday",date(2026,11,2))==date(2026,11,14)


def test_absent_timetable_does_not_prove_free_time(env):
    unknown=member(env,"student",section="Unmapped")
    result=Router(unknown).ask("On 2028-11-02 am I free at 4pm?",language="hi")
    assert result["reason"]=="schedule_unknown" and result["records"][0]["status"]=="schedule_unknown"
    assert "पुष्टि नहीं" in result["answer"]
    assert unknown.free_slots()==[]


@pytest.mark.parametrize("question,kinds",[
    ("What class do I have next, also what can I buy from the menu for my friend's birthday, something sweet under ₹300?",["classes","food"]),
    ("My next class,also canteen food under 100",["classes","food"]),
    ("Exam tomorrow and academic holiday tomorrow",["events","calendar"]),
    ("Who teaches Operating Systems and what does the latest examination circular say?",["faculty","passages"]),
    ("Tomorrow am I free at 4pm and canteen food under 100",["availability","food"]),
])
def test_every_compound_intent_has_its_own_answer(env,question,kinds):
    result=Router(env.service).ask(question)
    assert result["kind"]=="multi"
    assert [s["kind"] for s in result["sections"]]==kinds
    assert all(s["answer"] and s["question"] for s in result["sections"])
    if "sweet" in question:
        assert all(any(tag in r["category"].lower() for tag in ("sweet","dessert")) for r in result["sections"][1]["records"])


def test_holiday_does_not_hide_exam_conflict(env):
    import json
    from backend.app.ingestion import Ingestion
    from backend.app.schemas import ImportOptions
    from backend.tests.test_product_workflows import finish
    student=member(env,"student")
    today=student.local_now().date()
    for kind,rows in (("calendar",[dict(title="Test holiday",start_on=str(today),entry_kind="holiday")]),
                      ("events",[dict(title="Holiday exam",kind="exam",start=f"{today}T16:00:00",end=f"{today}T17:00:00",location="Exam hall")])):
        job=Ingestion(env.service,env.store).receive(kind+".json",json.dumps(rows).encode(),ImportOptions(kind=kind))
        finish(env,env.service,job)
    result=Router(student).ask("Am I free today at 4 PM?")
    assert result["reason"]=="busy" and result["sources"]
