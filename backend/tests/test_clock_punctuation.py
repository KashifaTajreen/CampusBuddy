import pytest

from backend.app.router import Router, parse_time


@pytest.mark.parametrize("text,expected", [
    ("Monday at 11 a.m.", "11:00"), ("Monday at 11 AM", "11:00"),
    ("Monday at 11 a. m.", "11:00"), ("at 11:30 a.m.", "11:30"),
    ("at 1 p.m.", "13:00"), ("at 12 a.m.", "00:00"),
    ("at 12 p.m.", "12:00"), ("at 9am", "09:00"),
    ("at 23:15", "23:15"),
])
def test_clock_notation(text, expected):
    assert parse_time(text) == expected


def test_exact_spoken_question_filters_timetable_at_eleven(env, monkeypatch):
    timetable = env.service.timetable
    calls = []

    def capture(on=None, at=None):
        calls.append(at)
        return timetable(on, at)

    monkeypatch.setattr(env.service, "timetable", capture)
    answer = Router(env.service).ask("What class do I have on Monday at 11 a.m.")
    assert calls[0] == "11:00"
    # A break-time lookup may consult the day again to explain the next class.
    assert calls[1:] == ([] if answer["records"] else [None])
    assert answer["kind"] == "classes"
    assert all(row["start_time"] <= "11:00:00" < row["end_time"] for row in answer["records"])
    if not answer["records"]:
        assert "no scheduled class at 11:00" in answer["answer"]
