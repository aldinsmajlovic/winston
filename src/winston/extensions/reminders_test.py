"""Tests for Reminders: `winston ext test reminders`. The fake clock, no winstond."""
from datetime import datetime
from pathlib import Path

from winston_sdk.testing import FakeClock, Harness

HERE = Path(__file__).parent / "reminders.py"


def harness(start: str = "2026-10-05T10:00:00") -> Harness:        # a Monday
    return Harness(HERE, clock=FakeClock(start, zone="Europe/Stockholm"))


async def test_a_reminder_is_pushed_for_its_time():
    h = harness()
    said = await h.tool("add", what="call Mum", at="at six")
    assert said == "Set for today at 18:00: call Mum.", said
    [(kind, n)] = h.pushed
    assert kind == "due" and n.not_before == datetime.fromisoformat("2026-10-05T18:00:00+02:00")
    assert n.say == "A reminder: call Mum." and n.facts["what"] == "call Mum"
    assert h.entities["next"] == "2026-10-05T18:00:00+02:00"
    await h.close()


async def test_a_time_that_has_passed_is_refused():
    h = harness()
    try:
        await h.tool("add", what="call Mum", at="2026-10-05T09:00")
        raise AssertionError("a past time was taken")
    except Exception as e:                                          # noqa: BLE001
        assert "has passed" in str(e), e
    await h.close()


async def test_listed_soonest_first_and_cancelled_by_its_words():
    h = harness()
    await h.tool("add", what="take the bins out", at="tomorrow at seven")
    await h.tool("add", what="call the dentist", at="in 20 minutes")
    listed = await h.tool("upcoming")
    assert [r["what"] for r in listed] == ["call the dentist", "take the bins out"], listed
    assert listed[1]["when"] == "tomorrow at 07:00", listed
    said = await h.tool("cancel", which="the dentist")
    assert said == "Cancelled: call the dentist.", said
    assert [n.facts["what"] for _, n in h.pushed] == ["take the bins out"]
    assert [r["what"] for r in await h.tool("upcoming")] == ["take the bins out"]
    await h.close()


async def test_said_once_it_is_gone():
    h = harness()
    await h.tool("add", what="call Mum", at="at six")
    [(_, n)] = h.pushed
    h.clock.set("2026-10-05T18:00:00")
    await h.nudge_event("said", n, kind="due")
    assert await h.tool("upcoming") == "No reminders set."
    assert h.entities["next"] is None
    await h.close()


async def test_put_off_it_stays_listed():
    h = harness()
    await h.tool("add", what="call Mum", at="at six")
    [(_, n)] = h.pushed
    await h.nudge_event("put_off", n, kind="due")
    assert [r["what"] for r in await h.tool("upcoming")] == ["call Mum"]
    await h.close()


async def test_a_weekday_reminder_skips_the_weekend():
    h = harness("2026-10-09T08:00:00")                                    # a Friday
    said = await h.tool("add", what="stand-up", at="at 9:30", repeat="weekdays")
    assert said == "Set for today at 09:30, then every weekday: stand-up.", said
    [(_, first)] = h.pushed
    h.clock.set("2026-10-09T09:30:00")
    await h.nudge_event("said", first, kind="due")
    nxt = h.pushed[-1][1]
    assert nxt.not_before == datetime.fromisoformat("2026-10-12T09:30:00+02:00"), nxt.not_before
    assert nxt.about != first.about                                  # a new occurrence, not the old one
    await h.close()


async def test_a_daily_reminder_keeps_its_time_across_daylight_saving():
    h = harness("2026-10-24T20:00:00")                                    # summer time ends that night
    await h.tool("add", what="pills", at="at 21:00", repeat="daily")
    [(_, first)] = h.pushed
    h.clock.set("2026-10-24T21:00:00")
    await h.nudge_event("said", first, kind="due")
    nxt = h.pushed[-1][1]
    assert nxt.not_before == datetime.fromisoformat("2026-10-25T21:00:00+01:00"), nxt.not_before
    await h.close()


async def test_skipping_one_time_of_a_repeating_reminder():
    h = harness()
    await h.tool("add", what="gym", at="at 17:00", repeat="weekly")
    said = await h.tool("cancel", which="gym", all_times=False)
    assert said == "Skipped today at 17:00; next Monday 12 October at 17:00: gym.", said
    [(_, n)] = h.pushed
    assert n.not_before == datetime.fromisoformat("2026-10-12T17:00:00+02:00")
    await h.close()


async def test_two_that_match_ask_which():
    h = harness()
    await h.tool("add", what="call Mum", at="at six")
    await h.tool("add", what="call Mum back about Sunday", at="at seven")
    try:
        await h.tool("cancel", which="call Mum")
        raise AssertionError("cancelled one of two without asking")
    except Exception as e:                                          # noqa: BLE001
        assert "more than one" in str(e), e
    await h.close()


async def test_status_line():
    h = harness()
    assert await h.status() == "No reminders"
    await h.tool("add", what="call Mum", at="at six")
    assert await h.status() == "1 set; next today at 18:00: call Mum"
    await h.close()
