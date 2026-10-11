"""Tests for Later: `winston ext test later`. The brain's tools are fakes; the clock is in Stockholm."""
from pathlib import Path

from winston_sdk.testing import FakeClock, FakeHA, Harness

HERE = Path(__file__).parent / "later.py"
START = "2026-10-12T08:00:00"


def broken(**args):
    raise RuntimeError("the vacuum is offline")


def harness(home: bool = True, washer: str = "on") -> Harness:
    tools = {"vacuum__CleanArea": {"tier": "act", "answer": "Started cleaning the Kitchen."},
             "HassTurnOn": {"tier": "act", "answer": broken},
             "GetLiveContext": {"tier": "read", "answer": "..."},
             "lock__Unlock": {"tier": "restricted"}}
    return Harness(HERE, tools=tools, home=home, ha=FakeHA(states={"sensor.washer": washer}),
                   clock=FakeClock(START, zone="Europe/Stockholm"))


async def test_when_i_leave_runs_the_call_and_tells_him():
    h = harness()
    said = await h.tool("add", what="clean the kitchen", tool="vacuum__CleanArea", args='{"area": "Kitchen"}',
                        on="leave")
    assert said == "Will do, when you leave: clean the kitchen.", said
    assert h.tool_calls == []
    h.home = False
    await h.run("left")
    assert h.tool_calls == [("vacuum__CleanArea", {"area": "Kitchen"})], h.tool_calls
    [(kind, nudge)] = h.pushed
    assert kind == "done" and nudge.facts["result"] == "Started cleaning the Kitchen.", nudge
    assert await h.tool("waiting") == "Nothing is waiting."
    await h.run("left")                                  # once only
    assert len(h.tool_calls) == 1
    await h.close()


async def test_at_a_time_sets_a_timer_and_cancel_takes_it_back():
    h = harness()
    said = await h.tool("add", what="clean the kitchen", tool="vacuum__CleanArea", args='{"area": "Kitchen"}',
                        at="2026-10-12T18:00:00+02:00")
    assert said == "Will do, today at 18:00: clean the kitchen.", said
    [timer] = h.timers.values()
    assert timer["job"] == "due" and timer["at"].startswith("2026-10-12T18:00"), timer
    await h.run("due", data=timer["data"])
    del h.timers[timer["id"]]                            # fired: the core drops it
    assert h.tool_calls == [("vacuum__CleanArea", {"area": "Kitchen"})]
    await h.tool("add", what="clean the kitchen", tool="vacuum__CleanArea", args='{"area": "Kitchen"}',
                 at="2026-10-13T09:00:00+02:00")
    assert await h.tool("cancel", which="the kitchen") == "Cancelled: clean the kitchen."
    assert h.timers == {} and await h.tool("waiting") == "Nothing is waiting."
    await h.close()


async def test_a_state_is_watched_and_a_day_is_the_limit():
    h = harness()
    await h.tool("add", what="clean the kitchen", tool="vacuum__CleanArea", args='{"area": "Kitchen"}',
                 entity="sensor.washer", state="off")
    await h.run("watch")
    assert h.tool_calls == []
    h.ha.set("sensor.washer", "Off")
    await h.run("watch")
    assert h.tool_calls == [("vacuum__CleanArea", {"area": "Kitchen"})]
    h.ha.set("sensor.washer", "on")
    await h.tool("add", what="clean the kitchen", tool="vacuum__CleanArea", args='{"area": "Kitchen"}',
                 entity="sensor.washer", state="off")
    h.clock.advance(days=1, minutes=1)
    await h.run("watch")
    h.ha.set("sensor.washer", "off")
    await h.run("watch")
    assert len(h.tool_calls) == 1 and await h.tool("waiting") == "Nothing is waiting."
    await h.close()


async def test_a_failure_is_told():
    h = harness(home=False)
    await h.tool("add", what="turn on the hallway lamp", tool="HassTurnOn", args='{"name": "hallway lamp"}',
                 on="arrive")
    h.home = True
    await h.run("arrived")
    [(kind, nudge)] = h.pushed
    assert kind == "failed" and "offline" in nudge.facts["error"] and nudge.level == 2, nudge
    await h.close()


async def test_what_is_refused_up_front():
    h = harness()

    async def refused(**args) -> str:
        try:
            await h.tool("add", **args)
        except Exception as e:                                          # noqa: BLE001
            return str(e)
        raise AssertionError(f"not refused: {args}")
    base = {"what": "clean the kitchen", "tool": "vacuum__CleanArea", "args": "{}"}
    assert "no tool" in await refused(**base | {"tool": "vacuum_clean", "on": "leave"})
    assert "confirm" in await refused(**base | {"tool": "lock__Unlock", "on": "leave"})
    assert "exactly one trigger" in await refused(**base)
    assert "exactly one trigger" in await refused(**base | {"on": "leave", "at": "2026-10-12T18:00:00+02:00"})
    assert "exactly one trigger" in await refused(**base | {"entity": "sensor.washer"})
    assert "JSON" in await refused(**base | {"args": "area=Kitchen", "on": "leave"})
    assert "passed" in await refused(**base | {"at": "2026-10-12T07:00:00+02:00"})
    assert "already home" in await refused(**base | {"on": "arrive"})
    assert "already on" in await refused(**base | {"entity": "sensor.washer", "state": "on"})
    assert "not an entity" in await refused(**base | {"entity": "sensor.nothing", "state": "off"})
    assert h.store.get("items", {}) == {}
    await h.close()


async def test_asked_twice_is_kept_once_and_listed():
    h = harness()
    for _ in range(2):
        said = await h.tool("add", what="clean the kitchen", tool="vacuum__CleanArea", args='{"area": "Kitchen"}',
                            on="leave")
    assert said == "Already waiting, when you leave: clean the kitchen.", said
    [row] = await h.tool("waiting")
    assert row["what"] == "clean the kitchen" and row["when"] == "when you leave"
    assert await h.status() == "1 waiting; next: clean the kitchen when you leave"
    await h.close()
