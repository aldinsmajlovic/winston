"""Tests for Awareness: `winston ext test awareness`."""
from pathlib import Path

import httpx
from winston_sdk.testing import FakeClock, Harness

HERE = Path(__file__).parent / "awareness.py"

EVENING = "2026-10-10T23:00:00"
MORNING = "2026-10-11T08:00:00"


def _turns(count: int, last_ago_minutes: int, clock_now: str):
    """Fake turns API response: `count` answered turns, the last one `last_ago_minutes` ago."""
    now_ts = FakeClock(clock_now, zone="Europe/Stockholm").now().timestamp()
    return {
        "day": clock_now[:10],
        "turns": [{"ts": now_ts - last_ago_minutes * 60 - i * 60, "outcome": "answered"}
                  for i in range(count)],
        "days": [clock_now[:10]],
        "enabled": True,
    }


def _transport(turns_response: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/turns" in str(request.url):
            return httpx.Response(200, json=turns_response)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def harness(start: str = EVENING, turns_count: int = 5, quiet_minutes: int = 45) -> Harness:
    turns = _turns(turns_count, quiet_minutes, start)
    return Harness(HERE, clock=FakeClock(start, zone="Europe/Stockholm"),
                   http=_transport(turns))


async def test_goodnight_pushed_after_quiet_evening():
    h = harness(EVENING, turns_count=5, quiet_minutes=45)
    await h.run("check_goodnight")
    assert len(h.pushed) == 1, f"expected 1 nudge, got {len(h.pushed)}"
    kind, nudge = h.pushed[0]
    assert kind == "goodnight"
    assert "Goodnight" in nudge.say
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
