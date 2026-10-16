"""Tests for Agenda: `winston ext test agenda`. Made-up events and tasks on the fake Home Assistant."""
from pathlib import Path

from winston_sdk.testing import FakeClock, FakeHA, Harness

HERE = Path(__file__).parent / "agenda.py"
EVENTS = {"calendar.work": [
    {"start": "2026-10-12T10:00:00+02:00", "end": "2026-10-12T11:00:00+02:00", "summary": "Dentist",
     "location": "Storgatan 1"},
    {"start": "2026-10-12T15:30:00+02:00", "end": "2026-10-12T16:00:00+02:00", "summary": "Call with the bank"},
    {"start": "2026-10-12", "end": "2026-10-13", "summary": "Name day"},
    {"start": "2026-10-13T09:00:00+02:00", "end": "2026-10-13T10:00:00+02:00", "summary": "Standup"},
]}
TASKS = {"todo.tasks": [
    {"uid": "1", "summary": "Take the pill", "status": "needs_action", "due": "2026-10-12"},
    {"uid": "2", "summary": "Return the parcel", "status": "needs_action", "due": "2026-10-10"},
    {"uid": "3", "summary": "Pay the rent", "status": "needs_action", "due": "2026-10-12T11:00:00+02:00"},
    {"uid": "4", "summary": "Book flights", "status": "needs_action", "due": "2026-10-15"},
    {"uid": "5", "summary": "Someday, maybe", "status": "needs_action"},
    {"uid": "6", "summary": "Water the plants", "status": "completed", "due": "2026-10-12"},
]}


def ha(events=EVENTS) -> FakeHA:
    def get_events(data, target):
        return {cal: {"events": [e for e in events.get(cal, [])
                                 if data["start_date_time"][:10] <= e["start"][:10] and e["start"] < data["end_date_time"]]}
                for cal in target["entity_id"]}

    def get_items(data, target):
        return {lst: {"items": [t for t in TASKS.get(lst, []) if t["status"] in data.get("status", [t["status"]])]}
                for lst in target["entity_id"]}
    return FakeHA(states={"calendar.work": "off", "todo.tasks": "5"},
                  services={"calendar.get_events": get_events, "todo.get_items": get_items})


def harness(at="2026-10-12T07:00:00", **settings) -> Harness:
    return Harness(HERE, settings={"calendars": ["calendar.work"], **settings}, ha=ha(),
                   clock=FakeClock(at, zone="Europe/Stockholm"))


async def test_soon_held_until_before_the_start():
    h = harness()
    await h.run("read")
    nudges = {n.facts["event"]: n for n in await h.check("soon")}
    assert set(nudges) == {"Dentist", "Call with the bank", "Standup"}, nudges   # no all-day event
    dentist = nudges["Dentist"]
    assert dentist.not_before.isoformat() == "2026-10-12T09:30:00+02:00", dentist.not_before
    assert dentist.expires.isoformat() == "2026-10-12T10:00:00+02:00"
    assert dentist.facts["location"] == "Storgatan 1"
    await h.close()


async def test_minutes_counted_when_said():
    h = harness()
    await h.run("read")
    h.clock.advance(hours=2, minutes=35)                       # 09:35, the core checks again before saying it
    [dentist] = [n for n in await h.check("soon") if n.facts["event"] == "Dentist"]
    assert dentist.facts["in_minutes"] == 25 and "25 minutes" in dentist.say, dentist
    await h.close()


async def test_cancelled_or_started_is_not_returned():
    h = harness()
    await h.run("read")
    h.clock.advance(hours=3, minutes=1)                        # 10:01: the dentist has started
    assert "Dentist" not in {n.facts["event"] for n in await h.check("soon")}
    h.ha.services["calendar.get_events"] = lambda data, target: {"calendar.work": {"events": []}}
    await h.run("read")                                        # everything cancelled
    assert await h.check("soon") == []
    await h.close()


async def test_custom_lead_time():
    h = harness(before="10m")
    await h.run("read")
    [dentist] = [n for n in await h.check("soon") if n.facts["event"] == "Dentist"]
    assert dentist.not_before.isoformat() == "2026-10-12T09:50:00+02:00"
    await h.close()


async def test_briefing_names_today_only():
    h = harness()
    await h.run("read")
    [n] = await h.check("today")
    assert [e["event"] for e in n.facts["events"]] == ["Name day", "Dentist", "Call with the bank"], n.facts
    assert n.about == "today:2026-10-12" and "Dentist at 10:00" in n.say
    h2 = harness(all_day=False)
    await h2.run("read")
    [n2] = await h2.check("today")
    assert "Name day" not in [e["event"] for e in n2.facts["events"]]
    await h.close()
    await h2.close()


async def test_tool_reads_the_days_asked():
    h = harness()
    today = await h.tool("upcoming")
    assert [e["event"] for e in today] == ["Name day", "Dentist", "Call with the bank"], today
    assert today[0]["when"] == "today, all day" and today[1]["when"] == "today 10:00"
    two = await h.tool("upcoming", days=2)
    assert two[-1] == {"event": "Standup", "when": "tomorrow 09:00"}, two
    await h.close()


async def test_nothing_picked():
    h = Harness(HERE, ha=ha(), clock=FakeClock("2026-10-12T07:00:00", zone="Europe/Stockholm"))
    await h.run("read")
    assert await h.check("soon") == [] and await h.check("today") == []
    assert "No calendar" in await h.tool("upcoming")
    assert await h.status() == "No calendar picked"
    await h.close()


async def test_tasks_with_a_due_date_are_read_like_events():
    h = harness(lists=["todo.tasks"])
    await h.run("read")
    [n] = await h.check("today")
    assert [e.get("event") or e.get("task") for e in n.facts["events"]] == \
        ["Return the parcel", "Name day", "Take the pill", "Dentist", "Pay the rent", "Call with the bank"], n.facts
    assert {"task": "Return the parcel", "when": "overdue since Saturday 10 October"} in n.facts["events"]
    assert "Return the parcel, overdue since" in n.say and "Take the pill" in n.say, n.say
    rent = next(x for x in await h.check("soon") if x.facts.get("task") == "Pay the rent")
    assert rent.not_before.isoformat() == "2026-10-12T10:30:00+02:00" and "is due in" in rent.say, rent
    week = await h.tool("upcoming", days=7)
    assert {"task": "Book flights", "when": "Thursday 15 October"} in week, week
    assert {"task": "Take the pill", "when": "today"} in week
    assert not any("Someday" in str(r) or "plants" in str(r) for r in week), week   # no date; done
    assert await h.status() == "1 calendar, 1 list; next: Dentist today 10:00"
    await h.close()


async def test_a_list_alone_is_enough():
    h = harness(calendars=[], lists=["todo.tasks"])
    assert [r["task"] for r in await h.tool("upcoming")] == ["Return the parcel", "Take the pill", "Pay the rent"]
    await h.close()
