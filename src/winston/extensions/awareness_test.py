"""Tests for Awareness: `winston ext test awareness`."""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from winston_sdk.testing import FakeClock, Harness

HERE = Path(__file__).parent / "awareness.py"
ZONE = "Europe/Stockholm"

EVENING = "2026-10-10T23:00:00"
MORNING = "2026-10-11T08:00:00"


def _turns(count: int, last_ago_minutes: int, clock_now: str) -> list[dict]:
    """`count` answered turns a minute apart, the last one `last_ago_minutes` before `clock_now`."""
    now_ts = FakeClock(clock_now, zone=ZONE).now().timestamp()
    return [{"ts": now_ts - last_ago_minutes * 60 - i * 60, "outcome": "answered"} for i in range(count)]


def _transport(turns: list[dict], clock: FakeClock):
    """The turns API: the turns of the day asked for, by local date, up to the clock's now."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/turns":
            day, now = request.url.params["day"], clock.now().timestamp()
            rows = [t for t in turns
                    if t["ts"] <= now and datetime.fromtimestamp(t["ts"], clock.zone).date().isoformat() == day]
            return httpx.Response(200, json={"day": day, "turns": rows, "days": [day], "enabled": True})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def harness(start: str = EVENING, turns_count: int = 5, quiet_minutes: int = 45,
            turns: list[dict] | None = None) -> Harness:
    turns = _turns(turns_count, quiet_minutes, start) if turns is None else turns
    clock = FakeClock(start, zone=ZONE)
    return Harness(HERE, clock=clock, http=_transport(turns, clock))


async def test_goodnight_pushed_after_quiet_evening():
    h = harness(EVENING, turns_count=5, quiet_minutes=45)
    await h.run("check_goodnight")
    assert len(h.pushed) == 1, f"expected 1 nudge, got {len(h.pushed)}"
    kind, nudge = h.pushed[0]
    assert kind == "goodnight"
    assert "Goodnight" in nudge.say
    assert nudge.expires == datetime(2026, 10, 11, 6, tzinfo=ZoneInfo(ZONE)), nudge.expires
    await h.close()


async def test_no_goodnight_before_evening():
    h = harness("2026-10-10T15:00:00", turns_count=5, quiet_minutes=45)
    await h.run("check_goodnight")
    assert len(h.pushed) == 0, "should not push goodnight in the afternoon"
    await h.close()


async def test_no_goodnight_with_few_turns():
    h = harness(EVENING, turns_count=2, quiet_minutes=45)
    await h.run("check_goodnight")
    assert len(h.pushed) == 0, "should not push goodnight with only 2 turns"
    await h.close()


async def test_no_goodnight_if_still_talking():
    h = harness(EVENING, turns_count=5, quiet_minutes=10)
    await h.run("check_goodnight")
    assert len(h.pushed) == 0, "should not push goodnight if last turn was 10 min ago"
    await h.close()


async def test_after_midnight_the_goodnight_is_the_evenings():
    """Quiet since 01:22, checked at 01:56: the goodnight is the evening's and ends at 06:00 that
    morning, so one held through quiet hours is not said in the day."""
    h = harness("2026-10-16T01:56:00", turns_count=5, quiet_minutes=34)
    await h.run("check_goodnight")
    [(_, nudge)] = h.pushed
    assert nudge.about == "2026-10-15", nudge.about
    assert nudge.expires == datetime(2026, 10, 16, 6, tzinfo=ZoneInfo(ZONE)), nudge.expires
    await h.close()


async def test_no_goodnight_while_still_talking_past_midnight():
    turns = _turns(5, 30, "2026-10-15T23:50:00") + _turns(1, 10, "2026-10-16T00:20:00")
    h = harness("2026-10-16T00:20:00", turns=turns)
    await h.run("check_goodnight")
    assert not h.pushed, "the turn ten minutes ago, after midnight, counts"
    await h.close()


async def test_one_goodnight_a_night_and_the_next_evening_has_its_own():
    turns = (_turns(5, 45, "2026-10-15T23:00:00") + _turns(4, 40, "2026-10-16T01:30:00")
             + _turns(3, 40, "2026-10-16T22:00:00"))
    h = harness("2026-10-15T23:00:00", turns=turns)
    await h.run("check_goodnight")
    h.clock.set("2026-10-16T01:30:00")
    await h.run("check_goodnight")
    assert [n.about for _, n in h.pushed] == ["2026-10-15"], "talk after midnight is the same night"
    h.clock.set("2026-10-16T22:00:00")
    await h.run("check_goodnight")
    assert [n.about for _, n in h.pushed] == ["2026-10-15", "2026-10-16"], "the next evening has its own"
    await h.close()
