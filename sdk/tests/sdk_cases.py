"""SDK cases: the manifest, the examples in docs/extensions.md exactly as written, every decorator,
the test harness, and the extension as an MCP server (in memory and over stdio). No winstond needed.

    uv run python sdk/tests/sdk_cases.py
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters
from mcp.shared.exceptions import MCPError
import mcp.types as mt

from winston_sdk import manifest
from winston_sdk.errors import NotPermitted
from winston_sdk.testing import FakeHA, Harness, HandlerFailed, recorded
from winston_sdk.when import parse_when

HERE = Path(__file__).parent
REPO = HERE.parent.parent
failed = 0


def expect(name: str, ok: bool, detail=""):
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name} {detail if not ok else ''}")


def examples() -> Path:
    """The docs' examples, exactly as written, as files."""
    out = Path(tempfile.mkdtemp(prefix="winston-sdk-examples-"))
    doc = (REPO / "docs" / "extensions.md").read_text()
    for block in re.findall(r"```python\n(# /// script.*?)```", doc, re.S):
        ident = re.search(r'^# id = "(\w+)"', block, re.M).group(1)
        (out / f"{ident}.py").write_text(block)
    return out


EX = examples()
EVERYTHING = HERE / "fixtures" / "everything.py"


def manifests():
    m = manifest.read(EX / "hevy.py")
    expect("manifest from the PEP 723 block", m.id == "hevy" and m.permissions.network == ["api.hevyapp.com"]
           and m.dependencies == [] and m.api == 1, m)
    e = manifest.read(EVERYTHING)
    expect("several permissions in one table", e.permissions.home_assistant == "act" and e.permissions.llm
           and e.permissions.extensions == ["other"], e.permissions)
    bad = Path(tempfile.mkdtemp()) / "bad.py"
    bad.write_text('# /// script\n# [tool.winston]\n# id = "Bad Id"\n# name = "x"\n# api = 1\n# ///\n')
    try:
        manifest.read(bad)
        expect("a bad id is refused", False)
    except manifest.ManifestError as err:
        expect("a bad id is refused", "id" in str(err), err)
    pkg = Path(tempfile.mkdtemp())
    (pkg / "pyproject.toml").write_text('[project]\nname = "p"\ndependencies = ["winston-sdk>=1,<2", "feedparser"]\n'
                                        '[tool.winston]\nid = "pkg"\nname = "Pkg"\napi = 1\n')
    (pkg / "pkg.py").write_text("")
    m = manifest.read(pkg / "pkg.py")
    expect("a package's pyproject, the SDK left out of its dependencies", m.id == "pkg"
           and m.dependencies == ["feedparser"], m.dependencies)


def describe_cli():
    for name in ("coin", "hevy", "plants", "remind"):
        r = subprocess.run([sys.executable, "-m", "winston_sdk", "describe", str(EX / f"{name}.py")],
                           capture_output=True, text=True, timeout=60)
        ok = r.returncode == 0
        d = json.loads(r.stdout) if ok else {}
        expect(f"describe the doc's {name} example", ok and d["manifest"]["id"] == name, r.stderr[-300:])
    d = json.loads(subprocess.run([sys.executable, "-m", "winston_sdk", "describe", str(EX / "hevy.py")],
                                  capture_output=True, text=True).stdout)
    props = d["settings"]["schema"]["properties"]
    expect("hevy: the key is a secret field defaulting to HEVY_API_KEY",
           d["settings"]["secrets"] == {"api_key": "HEVY_API_KEY"} and props["api_key"]["secret"]["default"] ==
           "HEVY_API_KEY", props["api_key"])
    expect("hevy: an hourly job", d["jobs"] == [{"name": "poll", "trigger": {"kind": "every", "seconds": 3600.0,
                                                                         "align": False},
                                                 "timeout": 120, "catch_up": True}], d["jobs"])
    expect("hevy: a checked kind delivered when home", d["kinds"][0]["kind"] == "rest" and d["kinds"][0]["checked"]
           and d["kinds"][0]["deliver"] == "when_home", d["kinds"])
    t = d["tools"][0]
    expect("hevy: its tool, read, outside by default", t["name"] == "last_workout" and t["tier"] == "read"
           and t["outside"] and "most recent workout" in t["description"], t)
    expect("hevy: its sensor", d["entities"][0]["key"] == "days_since_workout"
           and d["entities"][0]["state_class"] == "measurement", d["entities"])
    r = subprocess.run([sys.executable, "-m", "winston_sdk", "describe", str(EVERYTHING)],
                       capture_output=True, text=True, timeout=60)
    d = json.loads(r.stdout)
    props = d["settings"]["schema"]["properties"]
    expect("an entity picker with domains", props["heater"]["options"] == "ha_entities"
           and props["heater"]["options_query"] == {"domain": "switch,climate"}, props["heater"])
    expect("times and durations shown as text", props["remind_at"]["default"] == "07:30"
           and props["remind_at"]["widget"] == "time" and props["wait"]["default"] == "1h30m", props["wait"])
    expect("a memory topic picker", props["topic"]["options"] == "memory_topics", props["topic"])
    trig = {j["name"]: j["trigger"] for j in d["jobs"]}
    expect("From() travels as a field name", trig["morning"]["time"] == {"from": "remind_at"}
           and trig["heater_on"]["entity"] == {"from": "heater"} and trig["morning"]["days"] == [0, 1, 2, 3, 4],
           trig)
    mark = next(t for t in d["tools"] if t["name"] == "mark")
    ps = mark["input_schema"]["properties"]
    expect("a Choices argument carries its setting", ps["chore"].get("x-winston-choices") == {"from": "chores"}, ps)
    expect("a When argument is text for the brain", ps["when"].get("x-winston-when") is True
           and "chore" in mark["input_schema"]["required"] and "when" not in mark["input_schema"]["required"], ps)
    expect("argument descriptions from the docstring", ps["chore"]["description"] == "Which chore.", ps["chore"])
    kinds = {k["kind"]: k for k in d["kinds"]}
    expect("a delivery per level", kinds["due"]["deliver"] == {"1": "next_talk", "2": "when_home"}
           or kinds["due"]["deliver"] == {1: "next_talk", 2: "when_home"}, kinds["due"])
    expect("a pushed kind", not kinds["told"]["checked"] and kinds["told"]["requested"], kinds["told"])
    ent = {e["key"]: e for e in d["entities"]}
    expect("a per entity and what it takes", ent["done"]["per"] == {"from": "chores"}
           and ent["done"]["takes"] == "press" and ent["level"]["takes"] == "change", ent)
    broken = Path(tempfile.mkdtemp()) / "broken.py"
    broken.write_text('# /// script\n# [tool.winston]\n# id = "broken"\n# name = "B"\n# api = 1\n# ///\n'
                      "from winston_sdk import Extension\next = Extension()\n@ext.tool(tier='read')\n"
                      "def sync(ctx):\n    'Not async.'\n")
    r = subprocess.run([sys.executable, "-m", "winston_sdk", "describe", str(broken)], capture_output=True, text=True)
    expect("a sync handler is refused with a plain message", r.returncode == 1 and "must be async" in r.stderr,
           r.stderr)


async def harness_examples():
    h = Harness(EX / "coin.py")
    expect("coin: flip", await h.tool("flip") in ("heads", "tails"))
    await h.close()

    h = Harness(EX / "hevy.py", settings={"rest_days": 3}, secrets={"api_key": "k"},
                http=recorded(HERE / "fixtures" / "hevy_one_workout.json"))
    await h.run("poll")
    expect("hevy: the sensor is set", h.entities.get("days_since_workout") == 0, h.entities)
    expect("hevy: no nudge on the day", await h.check("rest") == [])
    h.clock.advance(days=3)
    [n] = await h.check("rest")
    expect("hevy: a gentle nudge on day three", n.level == 1 and n.facts["days_since"] == 3
           and n.about == "2026-01-05T07:00:00+00:00", n)
    h.clock.advance(days=2)
    [n2] = await h.check("rest")
    expect("hevy: firmer on day five, still about the same gap", n2.level == 2 and n2.about == n.about, n2)
    expect("hevy: the tool", (await h.tool("last_workout"))["title"] == "Push day")
    expect("hevy: the status line", await h.status() == "Last workout: Push day")
    expect("hevy: the request was logged without its query", any(
        m.startswith("http GET api.hevyapp.com/v1/workouts 200") for _, m in h.log), h.log)
    await h.close()

    ha = FakeHA({"sensor.ficus": {"state": "22", "attributes": {"device_class": "moisture"}}})
    h = Harness(EX / "plants.py", settings={"sensor": "sensor.ficus"}, ha=ha)
    [n] = await h.check("thirsty")
    expect("plants: dry soil nudges, about the sensor", n.about == "sensor.ficus" and n.facts["moisture"] == 22.0, n)
    ha.set("sensor.ficus", "45")
    expect("plants: wet soil does not", await h.check("thirsty") == [])
    ha.states.pop("sensor.ficus")
    expect("plants: a missing sensor does not crash the check", await h.check("thirsty") == [])
    await h.close()

    h = Harness(EX / "remind.py")
    reply = await h.tool("add", what="call Mum", at="at six")
    [(kind, n)] = h.pushed
    expect("remind: a pushed nudge at the time said", kind == "due" and n.not_before == datetime(
        2026, 1, 5, 18, 0, tzinfo=h.clock.zone) and n.facts == {"what": "call Mum"}, n)
    expect("remind: the reply names the time", reply == "Set for Monday 18:00.", reply)
    try:
        await h.tool("add", what="x", at="whenever you like")
        expect("remind: an unreadable time is refused", False)
    except Exception as e:
        expect("remind: an unreadable time is refused with a hint", "Could not read the time" in str(e), e)
    await h.close()


class Answer(BaseModel):
    yes: bool


async def harness_everything():
    ha = FakeHA({"switch.heater": "off"}, services={"switch.turn_off": {"ok": True}})
    h = Harness(EVERYTHING, settings={"heater": "switch.heater", "mode": "keen",
                                      "chores": {"bins": {"every_days": 7}, "plants": {"every_days": 3, "at": "18:00"}}},
                secrets={"key": "k"}, ha=ha, llm=lambda prompt, schema: {"yes": True},
                others={"other": {"lookup": lambda q: {"q": q}}},
                http=httpx.MockTransport(lambda r: httpx.Response(200, text="hi")))
    await h._start()
    s = h.rt.settings
    expect("settings: times, durations and entries read", s.remind_at.hour == 7 and s.remind_at.minute == 30
           and s.wait == timedelta(hours=1, minutes=30) and s.chores["plants"].at.hour == 18, s)
    await asyncio.sleep(0)
    expect("background task started once settings took", h.store.get("background") is True, h.store)
    await h.run("start")
    await h.run("tick")
    await h.run("morning", time="2026-01-05T07:30:00+00:00")
    expect("jobs run with and without an event", h.store["started"] == 1 and h.store["ticks"] == 1
           and h.store["morning"] == "2026-01-05T07:30:00+00:00", h.store)
    await h.run("heater_on", entity_id="switch.heater", old={"entity_id": "switch.heater", "state": "off"},
                new={"entity_id": "switch.heater", "state": "on"})
    expect("an on_state event carries old and new", h.store["heater"] == ["switch.heater", "off", "on"], h.store)
    await h.run("on_test", type="winston_test", data={"a": 1})
    expect("an on_event event carries its data", h.store["event"] == {"a": 1})
    out = await h.tool("mark", chore="bins")
    expect("a tool changes the store and sets a sensor", out["chore"] == "bins" and h.entities["count"] == 1, out)
    await h.close()


async def harness_everything_rest():
    ha = FakeHA({"switch.heater": "off"})
    h = Harness(EVERYTHING, settings={"heater": "switch.heater",
                                      "chores": {"bins": {"every_days": 7}, "plants": {"every_days": 3}}},
                secrets={"key": "k"}, ha=ha, llm=lambda prompt, schema: {"yes": True},
                others={"other": {"lookup": lambda q: {"q": q}}},
                http=httpx.MockTransport(lambda r: httpx.Response(200, text="hi")), background=False)
    try:
        await h.tool("mark", chore="plants", when="whenever")
        expect("an unreadable When is refused", False)
    except Exception as e:
        expect("an unreadable When is refused", "Could not read the time" in str(e), e)
    out = await h.tool("mark", chore="plants", when="in two hours")
    expect("a readable When arrives as a time", out["at"] == "2026-01-05T11:00:00+00:00", out)
    await asyncio.sleep(0)
    expect("background=False leaves background tasks unstarted", "background" not in h.store, h.store)
    t0 = time.perf_counter()
    try:
        await h.tool("slow")
        expect("a tool past its timeout fails", False)
    except Exception as e:
        expect("a tool past its timeout fails, in its time", "longer than 1 s" in str(e)
               and time.perf_counter() - t0 < 2, e)
    expect("fetch from a declared host", await h.tool("fetch", host="example.com") == 200)
    try:
        await h.tool("fetch", host="evil.example.net")
        expect("fetch from an undeclared host is refused", False)
    except Exception as e:
        expect("fetch from an undeclared host is refused", "not in this extension's network permission" in str(e), e)
    nudges = await h.check("due")
    expect("a check returns one nudge per chore", sorted(n.about for n in nudges) == ["bins", "plants"]
           or sorted(n.about for n in nudges) == ["bins"], nudges)
    result = await h.act("done_it", nudges[0], kind="due")
    expect("an action runs with its nudge", result.startswith("Marked ") and nudges[0].about in h.store["done"], result)
    await h.press("done", entry="bins")
    expect("a per button press names its entry", "bins" in h.store["done"])
    await h.set_entity("level", 4)
    expect("a number set from Home Assistant", h.store["level"] == 4)
    expect("a hook answers", await h.hook("ping", json={"n": 1}) == {"pong": {"n": 1}})
    await h.nudge_event("said", nudges[0], kind="due")
    expect("on_nudge hears it", h.store["heard"] == [["said", nudges[0].about]], h.store.get("heard"))
    timer = await h.rt.ctx.later(timedelta(minutes=5), "later", data={"x": 1})
    h.clock.advance(minutes=6)
    ran = await h.run_due_timers()
    expect("a timer runs its job with its data", ran == 1 and h.store["later"] == {"x": 1}, h.store)
    expect("context is cut at 300 characters", len(await h.context()) == 300)
    expect("hint words from settings", await h.hint_words() == ["bins", "plants"])
    errors = await h.configure({"key": "bad"}, {"key": "bad"})
    expect("validate refuses on the field", errors == [{"field": "key", "message": "That key was refused."}], errors)
    errors = await h.configure({"heater": "switch.heater", "mode": "keen", "chores": {}}, {"key": "k"})
    expect("a good save applies and on_settings sees the old", errors == [] and h.store["old_mode"] == "calm", errors)
    mod = sys.modules["winston_ext_everything"]
    facts, answer, home, other = await mod.remember_and_ask(h.rt.ctx)
    expect("memory, the model, presence and another extension", facts[0].text == "The bins go out on Mondays."
           and answer.yes is True and home is True and other == {"q": "x"}, (facts, answer, home, other))
    await h.close()

    h2 = Harness(EX / "coin.py", ha=FakeHA())
    try:
        await h2.rt.ctx.ha.state("sensor.x")
        expect("no home_assistant permission: refused", False)
    except NotPermitted:
        expect("no home_assistant permission: refused", True)
    await h2.close()
    h3 = Harness(EX / "plants.py", settings={"sensor": "sensor.x"})
    try:
        await h3.rt.ctx.ha.call("switch", "turn_off", target={"entity_id": "switch.x"})
        expect("read permission cannot call services", False)
    except NotPermitted:
        expect("read permission cannot call services", True)
    await h3.close()


PICKS = """# /// script
# [tool.winston]
# id = "picks"
# name = "Picks"
# api = 1
# permissions = { devices = true }
# ///
from winston_sdk import Extension, Extensions, Settings

ext = Extension()


class S(Settings):
    machines: list[str] = Extensions("status", default=[])


ext.settings(S)


@ext.tool(tier="read")
async def look(ctx, server: str) -> dict:
    \"\"\"Another server's status.\"\"\"
    return await ctx.ext(server).call("status")
"""


async def harness_picks_and_devices():
    folder = Path(tempfile.mkdtemp(prefix="winston-sdk-picks-"))
    (folder / "picks.py").write_text(PICKS)
    ha = FakeHA(devices=[{"id": "d1", "name": "Router", "manufacturer": "Made Up Networks", "model": "MU-1",
                          "sw_version": "2.0"}])
    h = Harness(folder / "picks.py", settings={"machines": ["desk"]}, ha=ha,
                others={"desk": {"status": {"up": True}}, "nas": {"status": {"up": False}}})
    await h._start()
    [d] = await h.rt.ctx.devices()
    expect("ctx.devices() gives the fake's devices", d.name == "Router" and d.manufacturer == "Made Up Networks"
           and d.sw_version == "2.0" and d.area is None and d.service is False, d)
    expect("a server picked in settings may be called", await h.tool("look", server="desk") == {"up": True})
    try:
        await h.tool("look", server="nas")
        expect("one not picked is refused", False)
    except Exception as e:
        expect("one not picked is refused", "may not call nas" in str(e), e)
    schema = h.describe()["settings"]["schema"]["properties"]["machines"]
    expect("the picker says what the form needs", schema.get("options") == "extension_tools"
           and schema.get("options_query") == {"tool": "status"} and schema.get("extension_tool") == "status", schema)
    await h.close()
    h2 = Harness(EX / "coin.py")
    try:
        await h2.rt.ctx.devices()
        expect("no devices permission: refused", False)
    except NotPermitted as e:
        expect("no devices permission: refused", "devices" in str(e), e)
    await h2.close()
    shutil.rmtree(folder, ignore_errors=True)


class Anything(BaseModel):
    model_config = ConfigDict(extra="allow")


async def over_mcp():
    from winston_sdk.loader import load
    from winston_sdk.runtime import Runtime
    from winston_sdk.server import IDENTIFIER, build, request
    from winston_sdk.testing import FakeServices

    h = Harness(EVERYTHING, settings={}, secrets={"key": "k"})
    srv = build(h.rt)
    async with Client(srv) as c:
        caps = c.server_capabilities.extensions if c.server_capabilities else {}
        expect("the Winston protocol extension is advertised", IDENTIFIER in (caps or {}), caps)
        tools = {t.name: t for t in (await c.list_tools()).tools}
        expect("tools are plain MCP tools", {"mark", "slow", "fetch", "wipe"} <= set(tools), list(tools))
        a = tools["wipe"].annotations
        expect("restricted maps to destructiveHint", a.destructive_hint is True and a.read_only_hint is False, a)
        a = tools["fetch"].annotations
        expect("read maps to readOnlyHint, outside to openWorldHint", a.read_only_hint and a.open_world_hint, a)

        async def send(method, params=None):
            name = method.removeprefix("winston/")
            return (await c.session.send_request(request(name, **(params or {})), Anything)).model_dump()
        early = await c.call_tool("mark", {"chore": "bins"})
        expect("with a key and no settings yet, a tool refuses rather than use the variable's name",
               early.is_error and "still starting" in early.content[0].text, early.content)
        d = await send("winston/info")
        expect("winston/info", d["id"] == "everything" and d["pid"] == os.getpid(), d)
        r = await send("winston/configure", {"settings": {"mode": "keen"}, "secrets": {"key": "k"}})
        expect("winston/configure", r["ok"], r)
        r = await send("winston/configure", {"settings": {"mode": "nonsense"}, "validate_only": True})
        expect("winston/configure refuses a bad value with its field", not r["ok"] and r["errors"][0]["field"] == "mode", r)
        r = await send("winston/run", {"job": "tick"})
        expect("winston/run", r["ok"], r)
        r = await send("winston/run", {"job": "nope"})
        expect("winston/run an unknown job", not r["ok"], r)
        try:
            await send("winston/run", {"data": [1]})
            expect("a malformed request is refused", False)
        except MCPError as e:
            expect("a malformed request is refused, naming each bad param",
                   "winston/run" in str(e) and "job" in str(e) and "data" in str(e), e)
        r = await send("winston/run", {"job": "tick", "from_a_newer_winstond": 1})
        expect("a param the method does not take is ignored", r["ok"], r)
        r = await c.call_tool("mark", {"chore": "bins"})
        expect("a tool call over MCP", not r.is_error, r)
        r = await c.call_tool("slow", {})
        expect("a failing tool keeps its message", r.is_error and "longer than 1 s" in r.content[0].text, r.content)
        r = await send("winston/status")
        expect("winston/status", r["text"] == "1 done", r)
    await h.close()


async def over_stdio():
    state = tempfile.mkdtemp(prefix="winston-sdk-stdio-")
    params = StdioServerParameters(command=sys.executable, args=["-m", "winston_sdk", "run", str(EX / "coin.py")],
                                   env={"WINSTON_API": "http://127.0.0.1:9", "WINSTON_TOKEN": "t",
                                        "WINSTON_STATE": state})
    t0 = time.perf_counter()
    async with Client(params) as c:
        started = time.perf_counter() - t0
        r = await c.call_tool("flip", {})
        expect(f"over stdio: started in {started:.2f} s and flipped", json.loads(r.content[0].text) in ("heads", "tails"), r)
        from winston_sdk.server import request
        d = (await c.session.send_request(request("info"), Anything)).model_dump()
        expect("over stdio: info", d["id"] == "coin")
        t1 = time.perf_counter()
        for _ in range(20):
            await c.call_tool("flip", {})
        per = (time.perf_counter() - t1) / 20 * 1000
        expect(f"over stdio: a tool call takes {per:.1f} ms", per < 50, per)


def when_reader():
    from zoneinfo import ZoneInfo
    now = datetime(2026, 9, 29, 10, 23, tzinfo=ZoneInfo("Europe/Stockholm"))
    cases = {"at six": "09-29 18:00", "in 20 minutes": "09-29 10:43", "tomorrow morning": "09-30 08:00",
             "tomorrow at seven": "09-30 07:00", "Friday at six": "10-02 18:00", "half past six": "09-29 18:30",
             "quarter to seven": "09-29 18:45", "tonight": "09-29 20:00", "2026-10-02": "10-02 08:00",
             "in an hour and a half": "09-29 11:53", "Monday": "10-05 08:00"}
    bad = [k for k, v in cases.items() if (r := parse_when(k, now)) is None or r.strftime("%m-%d %H:%M") != v]
    expect("the time reader", not bad, bad)
    expect("the time reader leaves what it cannot read", all(parse_when(x, now) is None for x in
                                                             ("after work", "whenever", "in a bit")))
    dst = parse_when("in 1 day", datetime(2026, 10, 24, 12, 0, tzinfo=ZoneInfo("Europe/Stockholm")))
    expect("daylight saving: a zone, not a fixed offset", dst.utcoffset() == timedelta(hours=1), dst)


manifests()
describe_cli()
when_reader()
asyncio.run(harness_examples())
asyncio.run(harness_everything())
asyncio.run(harness_everything_rest())
asyncio.run(harness_picks_and_devices())
asyncio.run(over_mcp())
asyncio.run(over_stdio())
print(f"\n{'all passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
