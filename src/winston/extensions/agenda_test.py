"""Tests for Agenda: `winston ext test agenda`. Made-up events on the fake Home Assistant."""
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


def ha(events=EVENTS) -> FakeHA:
    def get_events(data, target):
        return {cal: {"events": [e for e in events.get(cal, [])
                                 if data["start_date_time"][:10] <= e["start"][:10] and e["start"] < data["end_date_time"]]}
                for cal in target["entity_id"]}
    return FakeHA(states={"calendar.work": "off"}, services={"calendar.get_events": get_events})


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
