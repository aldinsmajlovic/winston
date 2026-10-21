"""State check cases: after an action he answers at once, then reads Home Assistant and corrects
himself when a device did not change. Runs against a fake Home Assistant (tests/fake_ha.py) in this
process; never the real one. No hardware and no winstond needed.

    uv run python tests/verify_cases.py
"""
import asyncio
import json
import os
import time
from types import SimpleNamespace

import harness
from harness import expect, finish, free_port, scratch_data

scratch_data("winston-verify-")
os.environ["WINSTON_VERIFY_TEST_TOKEN"] = "fake-ha-token"
harness.detail_on_pass = False

from fake_ha import FakeHA  # noqa: E402
from pipecat.frames.frames import (BotStartedSpeakingFrame, BotStoppedSpeakingFrame,  # noqa: E402
                                   FunctionCallCancelFrame, FunctionCallsStartedFrame, LLMFullResponseEndFrame,
                                   LLMFullResponseStartFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame)
from pipecat.services.llm_service import FunctionCallParams  # noqa: E402
from pipecat.services.openai.llm import OpenAILLMService  # noqa: E402

from winston.config import resolve  # noqa: E402
from winston.config.schema import WinstonConfig  # noqa: E402
from winston.core.verify import (Expect, Outcome, Verifier, compose, expectation, mismatch,  # noqa: E402
                                 number_words)

WAIT = 2.0          # the last read; the default is 3.0, shorter here to keep the check quick


def cfg(url: str, **verify) -> WinstonConfig:
    return WinstonConfig.model_validate({"llm": {"model": "x"}, "providers": {"home_assistant": {
        "url": url, "token_env": "WINSTON_VERIFY_TEST_TOKEN",
        "expose": {"light.hylla": {"name": "shelf lamp"}, "light.taklampa": {"aliases": ["ceiling lamp"]},
                   "light.alla": {"name": "Main room lights"}},
        "verify": {"wait_secs": WAIT, **verify}}}})


def ha_result(*targets) -> str:
    """Home Assistant's answer to an intent: "done" for each target, as its MCP server sends it."""
    return json.dumps({"speech": {}, "response_type": "action_done",
                       "data": {"success": [{"type": "entity", **t} for t in targets], "failed": []}})


# ---------------------------------------------------------------- pure parts

expect("turn on expects on", expectation("intent__HassTurnOn", {"name": "x"}).state == "on")
expect("turn off expects off", expectation("intent__HassTurnOff", {}).state == "off")
e = expectation("light__HassLightSet", {"name": "x", "brightness": 50, "temperature": 2700})
expect("set light expects on, 50 %, 2700 K", (e.state, e.pct, e.brightness, e.kelvin) == ("on", 50, 128, 2700), e)
expect("brightness 0 expects off", expectation("light__HassLightSet", {"brightness": 0}).state == "off")
expect("read tools are not checked", expectation("homeassistant__GetLiveContext", {}) is None)
expect("other act tools are not checked", expectation("media_player__HassMediaPause", {}) is None)
on = lambda **a: {"state": "on", "attributes": a}                                   # noqa: E731
expect("brightness within 3 matches", mismatch(e, on(brightness=126, color_temp_kelvin=2750)) is None)
expect("brightness off by 5 does not", mismatch(e, on(brightness=133, color_temp_kelvin=2700)) == "brightness")
expect("kelvin off by 150 does not", mismatch(e, on(brightness=128, color_temp_kelvin=2850)) == "kelvin")
expect("unavailable is dead", mismatch(e, {"state": "unavailable"}) == "dead")
expect("unread is no claim", mismatch(e, None) is None)
expect("numbers as words", [number_words(n) for n in (0, 7, 30, 45, 100)]
       == ["zero", "seven", "thirty", "forty-five", "one hundred"])
E50 = Expect("on", 50)
dead = Outcome("light.hylla", "shelf lamp", "light", "dead", {"state": "unavailable"})
off = Outcome("light.taklampa", "ceiling lamp", "light", "state", {"state": "off"})
good = Outcome("light.x", "x", "light", None, on(brightness=128))
expect("dead alone", compose([dead], E50) == "The shelf lamp did not respond. Home Assistant shows it as unavailable.",
       compose([dead], E50))
expect("still off", compose([off], Expect("on")) == "The ceiling lamp is still off.", compose([off], Expect("on")))
expect("partial", compose([good, good, dead], E50) == "Two of the lights are at fifty percent; the shelf lamp did "
       "not respond.", compose([good, good, dead], E50))
expect("all fine says nothing", compose([good], E50) is None)
dim = Outcome("light.taklampa", "ceiling lamp", "light", "brightness", on(brightness=77))
expect("wrong brightness", compose([dim], E50) == "The ceiling lamp is at thirty percent, not fifty.", compose([dim], E50))
expect("a proper name gets no article", compose([Outcome("light.kok", "Köksspotlights", "light", "state",
                                                         {"state": "off"})], Expect("on"))
       == "Köksspotlights are still off.")
expect("a singular name gets is", compose([Outcome("light.hylla", "Hyllan", "light", "state", {"state": "on"})],
                                          Expect("off")) == "Hyllan is still on.")


# ---------------------------------------------------------------- against the fake Home Assistant


def log_lines(event: str) -> list[dict]:
    p = resolve(WinstonConfig.model_validate({"llm": {"model": "x"}}).logging.tool_calls)
    if not p.exists():
        return []
    return [r for r in map(json.loads, p.read_text().splitlines()) if r.get("event") == event]


class Session:
    """One verifier as a channel would hold it, with what it delivered and when."""

    def __init__(self, c: WinstonConfig):
        self.v = Verifier(c, channel="test")
        self.said: list[tuple[float, str]] = []
        self.t0 = time.monotonic()

        async def deliver(text):
            self.said.append((time.monotonic() - self.t0, text))
        self.v.deliver = deliver

    async def frame(self, f):
        await self.v.observer.on_push_frame(SimpleNamespace(frame=f))

    async def done(self):
        await self.v.drained(WAIT + 6)
        return [t for _, t in self.said]


async def scenarios(ha: FakeHA):
    c = cfg(ha.url)

    def later(secs, eid, state, **attrs):
        async def go():
            await asyncio.sleep(secs)
            ha.states[eid]["state"] = state
            ha.states[eid]["attributes"].update(attrs)
        return asyncio.create_task(go())

    # The wrapper: the model gets Home Assistant's answer unchanged and at once; the check follows.
    llm = OpenAILLMService(api_key="x", base_url="http://127.0.0.1:9", settings=OpenAILLMService.Settings(model="x"))
    answer = ha_result({"id": "light.kok", "name": "Köksspotlights"})

    async def mcp_handler(params):
        await params.result_callback(answer)
    for name in ("intent__HassTurnOn", "homeassistant__GetLiveContext"):
        llm.register_function(name, mcp_handler)
    s = Session(c)
    wrapped = s.v.wrap(llm, ["intent__HassTurnOn", "homeassistant__GetLiveContext"])
    expect("only act tools are wrapped", wrapped == ["intent__HassTurnOn"], wrapped)
    got = []

    async def model_gets(result, *, properties=None):
        got.append((time.monotonic(), result))
    t = time.monotonic()
    await llm._functions["intent__HassTurnOn"].handler(FunctionCallParams(
        function_name="intent__HassTurnOn", tool_call_id="c1", arguments={"name": "Köksspotlights"}, llm=llm,
        pipeline_worker=None, context=None, result_callback=model_gets))
    expect("the model gets the result unchanged", [r for _, r in got] == [answer])
    expect("and without waiting for the check", got and got[0][0] - t < 0.05, f"{(got[0][0] - t) * 1000:.0f} ms")
    expect("a check is running", not s.v.idle)

    # 1. A bulb that does not respond: still off after the last read.
    said = await s.done()
    expect("dead bulb: corrected", said == ["Köksspotlights are still off."], said)
    rec = [r for r in log_lines("verify") if r.get("id") == "c1"]
    expect("dead bulb: logged with entity, expected, observed, ms",
           len(rec) == 1 and rec[0]["entity"] == "light.kok" and rec[0]["expected"] == {"state": "on"}
           and rec[0]["observed"] == {"state": "off"} and not rec[0]["ok"] and abs(rec[0]["ms"] - WAIT * 1000) < 400,
           rec)

    # 2. One that is unavailable.
    ha.reset(); s = Session(c)
    s.v.after("intent__HassTurnOn", "c2", {"name": "shelf lamp"}, ha_result({"id": "light.hylla", "name": "Hyllan"}))
    said = await s.done()
    expect("unavailable: corrected", said == [("The shelf lamp did not respond. Home Assistant shows it as "
                                               "unavailable.")], said)

    # 3. A group with one dead member: the others reach 50 % a moment later.
    ha.reset(); s = Session(c)
    ha.states["light.alla"] = {"entity_id": "light.alla", "state": "on", "attributes": {
        "friendly_name": "Alla", "entity_id": ["light.taklampa", "light.hylla", "light.kok"]}}
    later(0.2, "light.taklampa", "on", brightness=128)
    later(0.3, "light.kok", "on", brightness=127)
    s.v.after("light__HassLightSet", "c3", {"name": "main room lights", "brightness": 50},
              ha_result({"id": "light.alla", "name": "Alla"}))
    said = await s.done()
    expect("group with a dead member: partial", said == [("Two of the lights are at fifty percent; the shelf lamp "
                                                          "did not respond.")], said)
    members = {r["entity"] for r in log_lines("verify") if r.get("id") == "c3"}
    expect("group: each member checked and logged", members == {"light.taklampa", "light.hylla", "light.kok"}, members)

    # 4. One that changes after a second: no correction, and the check stops at the next read.
    ha.reset(); s = Session(c)
    later(1.0, "light.kok", "on")
    s.v.after("intent__HassTurnOn", "c4", {"name": "Köksspotlights"}, ha_result({"id": "light.kok"}))
    said = await s.done()
    rec = [r for r in log_lines("verify") if r.get("id") == "c4"]
    expect("late change: no correction", said == [], said)
    expect("late change: logged ok at the 1.5 s read", len(rec) == 1 and rec[0]["ok"] and 1300 < rec[0]["ms"] < 1900,
           rec)

    # 5. "Try again" while the first check runs: only the newer action speaks about that entity.
    ha.reset(); s = Session(c)
    s.v.after("intent__HassTurnOn", "c5a", {}, ha_result({"id": "light.kok"}))
    await asyncio.sleep(0.3)
    s.v.after("intent__HassTurnOn", "c5b", {}, ha_result({"id": "light.kok"}))
    said = await s.done()
    expect("newer action on the same entity: one correction", said == ["Köksspotlights are still off."], said)
    expect("the older one logged as dropped", any(r.get("id") == "c5a" and r.get("dropped") == "newer action"
                                                  for r in log_lines("correction")))

    # 6. The user interrupts him: nothing is said.
    ha.reset(); s = Session(c)
    await s.frame(BotStartedSpeakingFrame())
    s.v.after("intent__HassTurnOn", "c6", {}, ha_result({"id": "light.kok"}))
    await asyncio.sleep(0.4)
    await s.frame(UserStartedSpeakingFrame())
    said = await s.done()
    expect("interrupted: no correction", said == [], said)
    expect("interrupted: the check stops and says so in the log",
           any(r.get("id") == "c6" and r.get("stopped") == "interrupted" for r in log_lines("verify")))

    # 7. He is still speaking when the check ends: the correction waits for him to finish.
    ha.reset(); s = Session(c)
    await s.frame(BotStartedSpeakingFrame())
    s.v.after("intent__HassTurnOn", "c7", {}, ha_result({"id": "light.kok"}))

    async def stop_speaking():
        await asyncio.sleep(WAIT + 1.0)
        await s.frame(BotStoppedSpeakingFrame())
    asyncio.create_task(stop_speaking())
    await s.done()
    at = s.said[0][0] if s.said else None
    expect("waits until his reply is spoken", at is not None and at >= WAIT + 1.3, at)

    # 7b. A tool call cancelled by an interruption or its timeout never gets a result: it must not keep
    # the channel busy, or every later correction waits a minute and is dropped.
    ha.reset(); s = Session(c)
    await s.frame(FunctionCallsStartedFrame(function_calls=[SimpleNamespace(tool_call_id="x7b")]))
    await s.frame(FunctionCallCancelFrame(function_name="intent__HassTurnOn", tool_call_id="x7b"))
    s.v.after("intent__HassTurnOn", "c7b", {}, ha_result({"id": "light.kok"}))
    said = await s.done()
    expect("a cancelled call does not hold corrections back", said == ["Köksspotlights are still off."], said)

    # 7c. The user has just finished speaking and his reply has not begun: the correction waits for it.
    ha.reset(); s = Session(c)
    s.v.after("intent__HassTurnOn", "c7c", {}, ha_result({"id": "light.kok"}))

    async def new_turn():
        await s.frame(UserStartedSpeakingFrame())
        await asyncio.sleep(WAIT - 0.2)
        await s.frame(UserStoppedSpeakingFrame())
        await asyncio.sleep(1.2)                         # speech to text and the model's first token
        await s.frame(LLMFullResponseStartFrame())
        await asyncio.sleep(0.3)
        await s.frame(LLMFullResponseEndFrame())
    asyncio.create_task(new_turn())
    await s.done()
    at = s.said[0][0] if s.said else None
    expect("waits for the reply to what the user just said", at is not None and at >= WAIT + 1.3 + 0.4, at)

    # 8. An older answer with a name and no id: found through his list.
    ha.reset(); s = Session(c)
    s.v.after("intent__HassTurnOff", "c8", {"name": "ceiling lamp"}, ha_result({"name": "ceiling lamp"}))
    said = await s.done()
    expect("name only: resolved and corrected", said == ["The ceiling lamp is still on."], said)

    # 9. Nothing to check: a failed call, a read tool, a domain that is not on/off.
    ha.reset(); s = Session(c)
    none = [s.v.after("intent__HassTurnOn", "c9a", {}, "Error: no such device"),
            s.v.after("homeassistant__GetLiveContext", "c9b", {}, ha_result({"id": "light.kok"})),
            s.v.after("intent__HassTurnOn", "c9c", {}, json.dumps({"response_type": "action_done", "data": {
                "success": [{"type": "area", "name": "Kitchen", "id": "kitchen"}], "failed": []}}))]
    expect("failures, reads and areas start no check", none == [None, None, None] and s.v.idle)
    s.v.after("intent__HassTurnOn", "c9d", {}, ha_result({"id": "media_player.plex_1"}))
    said = await s.done()
    expect("a media player is not judged", said == [], said)

    # 10. Switched off in config: nothing is wrapped or checked.
    s = Session(cfg(ha.url, enabled=False))
    expect("disabled: nothing wrapped", s.v.wrap(llm, ["intent__HassTurnOn"]) == [])
    expect("disabled: no check", s.v.after("intent__HassTurnOn", "c10", {}, ha_result({"id": "light.kok"})) is None)


async def unreachable():
    # 11. Home Assistant away: no reading, so no claim either way.
    s = Session(cfg(f"http://127.0.0.1:{free_port()}"))
    s.v.after("intent__HassTurnOn", "c11", {}, ha_result({"id": "light.kok"}))
    said = await s.done()
    rec = [r for r in log_lines("verify") if r.get("id") == "c11"]
    expect("HA away: no correction, logged unread", said == [] and rec and rec[0]["observed"] == "unread", rec)


async def main():
    async with FakeHA(free_port()) as ha:
        await scenarios(ha)
        expect("the check only read Home Assistant", ha.writes == [], ha.writes)
    await unreachable()


asyncio.run(main())
finish()
