"""Extension host cases: a real winstond on a spare port with a throwaway data directory and the fake
Home Assistant, two extensions (the docs' coin, and a probe that misbehaves on request). Checked
through the API as the page and the CLI see it: what runs, the tool list, keys reaching only their
own extension, Home Assistant permissions, field errors on save, crashes, memory and a blocked loop.
Never touches data/ or the real Home Assistant.

    uv run python tests/extensions_cases.py
"""
import asyncio
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import ClassVar

import harness
from harness import REPO, expect, finish, free_port, scratch_data, serve, until, winstond_data

DATA = scratch_data("winston-extensions-")
REMOTE_TOKEN = "remote-token-made-up-for-tests"
harness.detail_on_pass = False

import httpx  # noqa: E402
from fake_ha import FakeHA  # noqa: E402


def signatures():
    from winston.extension_host.client import signature
    tool = {"name": "x_play", "description": "Play a song.", "parameters": {"properties": {"what": {"type": "string"}}}}
    same = signature([dict(tool)], False)
    expect("a conversation's tools: the same tools, the same signature",
           signature([tool], False) == same and signature([{**tool, "tier": "act"}], False) == same)
    changed = [signature([{**tool, "description": "Play a song or a genre."}], False),
               signature([{**tool, "parameters": {"properties": {"what": {"type": "string"}, "genre": {}}}}], False),
               signature([tool, {**tool, "name": "x_mix"}], False), signature([tool], True)]
    expect("a new description, argument, tool or follow_up is a new signature",
           len({same, *changed}) == 5, changed)


def scheduling():
    from datetime import datetime, time as dtime
    from zoneinfo import ZoneInfo
    from winston.extension_host.scheduler import at_next, cron_next, every_aligned
    z = ZoneInfo("Europe/Stockholm")
    now = datetime(2026, 9, 29, 10, 23, tzinfo=z)                     # a Tuesday
    f = lambda d: d.strftime("%a %d %b %H:%M")  # noqa: E731
    cases = [(cron_next("0 7 * * 1", now), "Mon 05 Oct 07:00"), (cron_next("*/15 * * * *", now), "Tue 29 Sep 10:30"),
             (cron_next("30 18 * * 1-5", now), "Tue 29 Sep 18:30"), (cron_next("0 0 1 * *", now), "Thu 01 Oct 00:00"),
             (cron_next("0 9 * * 0,6", now), "Sat 03 Oct 09:00"),
             (at_next(dtime(7, 30), [0, 1, 2, 3, 4], datetime(2026, 10, 2, 8, 0, tzinfo=z)), "Mon 05 Oct 07:30"),
             (at_next(dtime(18, 0), None, now), "Tue 29 Sep 18:00"),
             (every_aligned(900, now), "Tue 29 Sep 10:30"), (every_aligned(3600, now), "Tue 29 Sep 11:00")]
    bad = [(f(a), b) for a, b in cases if f(a) != b]
    expect("cron, times of day and aligned intervals", not bad, bad)
    from winston.extension_host.scheduler import Scheduler

    class Stub:
        cfg = None
        described: ClassVar[dict] = {"x": {"settings": {"schema": {"properties": {"t": {"default": "07:30"}}}}}}

        def config(self, _):
            from winston.config.schema import ExtensionConfig
            return ExtensionConfig()
    sch = Scheduler(Stub(), None)
    sch.zone = z
    job = {"name": "j", "trigger": {"kind": "at", "time": {"from": "t"}, "days": None}, "catch_up": True}
    two_days_ago = datetime(2026, 9, 27, 8, 0, tzinfo=z).timestamp()
    due = sch._due("x", job, two_days_ago, 0, now)
    expect("an at job missed while down runs once when back", due < now.timestamp(), datetime.fromtimestamp(due, z))
    due = sch._due("x", {**job, "catch_up": False}, two_days_ago, 0, now)
    expect("catch_up=False waits for the next time instead", f(datetime.fromtimestamp(due, z)) == "Wed 30 Sep 07:30",
           datetime.fromtimestamp(due, z))
    dst = cron_next("0 12 * * *", datetime(2026, 10, 24, 13, 0, tzinfo=z))
    expect("across the change from summer time the wall clock holds", f(dst) == "Sun 25 Oct 12:00"
           and dst.utcoffset().total_seconds() == 3600, dst)


def setup(ha_port: int, api_port: int, llm_port: int, remote_port: int):
    cfg = {"llm": {"model": "fake", "base_url": f"http://127.0.0.1:{llm_port}/v1"},
           "channels": {"voice": {"enabled": False}, "satellite": {"enabled": False}, "discord": {"enabled": False}},
           "providers": {"home_assistant": {"url": f"http://127.0.0.1:{ha_port}", "token_env": "HA_TOKEN",
                                            "expose": {"light.taklampa": {}}}},
           "control": {"port": api_port},
           "speak_up": {"home": "person.someone"},
           "extensions": {"coin": {"enabled": True}, "probe": {"enabled": True, "memory_mb": 150},
                          "friend": {"enabled": True}, "clock": {"enabled": True},
                          "echo": {"enabled": True, "mcp": {"command": [sys.executable,
                                                                        str(REPO / "tests/fixtures/mcp_echo.py")],
                                                            "env": ["ECHO_KEY"]},
                                   "tools": {"echo": {"offer": True}, "delete_all": {}, "env": {}}},
                          "inventory": {"enabled": True}, "caller": {"enabled": True},
                          "remote": {"enabled": True, "mcp": {"url": f"http://127.0.0.1:{remote_port}/mcp",
                                                              "token_env": "REMOTE_MCP_TOKEN"},
                                     "tools": {"status": {}, "poke": {}}}}}
    winstond_data(DATA, cfg, fixtures=("probe.py", "friend.py", "clock.py", "inventory.py", "caller.py"),
                  env='PROBE_KEY="secret-one"\nOTHER_KEY="not-yours"\nECHO_KEY="for-echo"\n')
    doc = (REPO / "docs" / "extensions.md").read_text()
    coin = next(b for b in re.findall(r"```python\n(# /// script.*?)```", doc, re.DOTALL) if 'id = "coin"' in b)
    (DATA / "extensions" / "coin.py").write_text(coin)
    from datetime import date, timedelta
    (DATA / "memory" / "diary").mkdir(parents=True, exist_ok=True)
    (DATA / "memory" / "diary" / f"{date.today() - timedelta(days=1)}.md").write_text("He asked about the lamp.\n")


async def fake_llm(port: int):
    """An OpenAI-style server that answers every question with {"yes": true}, with llama.cpp's two slots;
    records the requests."""
    from aiohttp import web
    seen = []

    async def complete(request):
        body = await request.json()
        seen.append(body)
        return web.json_response({"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                                  "choices": [{"index": 0, "finish_reason": "stop",
                                               "message": {"role": "assistant", "content": '{"yes": true}'}}]})
    app = web.Application()
    app.add_routes([web.post("/v1/chat/completions", complete),
                    web.get("/props", lambda _: web.json_response({"total_slots": 2}))])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, seen


async def main():
    ha_port, api_port, llm_port, remote_port = free_port(), free_port(), free_port(), free_port()
    setup(ha_port, api_port, llm_port, remote_port)
    llm_runner, llm_seen = await fake_llm(llm_port)
    remote = await asyncio.create_subprocess_exec(sys.executable, str(REPO / "tests/fixtures/mcp_token.py"),
                                                  str(remote_port), REMOTE_TOKEN)
    try:
        await run(ha_port, api_port, llm_runner, llm_seen)
    finally:
        remote.terminate()
        await remote.wait()


async def run(ha_port: int, api_port: int, llm_runner, llm_seen):
    import winston.extension_host.process as P
    P.BACKOFF, P.WATCH_EVERY, P.PING_EVERY, P.PING_MISSES = (0.2, 0.2, 0.2), 0.3, 1.0, 2
    from winston.api.app import create_app
    from winston.config import load

    async with FakeHA(ha_port) as fake:
        app = create_app(load(), autostart=False)
        stop_server = await serve(app, api_port)
        host = app.state.extensions
        t0 = time.perf_counter()
        await host.start()
        api = httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}", timeout=30)

        async def states():
            return {e["id"]: e for e in (await api.get("/api/extensions")).json()}

        async def running(ext_id):
            return (await states())[ext_id]["state"] == "running"

        async def state_is(ext_id, state):
            return (await states())[ext_id]["state"] == state

        async def stopped_because(ext_id, text):
            return any(text in r["why"] for r in (await states())[ext_id]["exits"])

        ok = all([await until(lambda i=i: running(i), 30) for i in ("coin", "probe", "friend", "clock", "echo", "inventory")])
        expect(f"six extensions run, one a plain MCP server ({time.perf_counter() - t0:.1f} s from start)", ok, await states())

        tools = (await api.get("/api/extensions/tools")).json()
        names = [t["name"] for t in tools]
        owners = [t["extension"] for t in tools]
        expect("the brain's tools: prefixed, grouped by extension in id order", owners == sorted(owners)
               and all(t["name"].startswith(t["extension"] + "_") for t in tools) and "coin_flip" in names, names)
        expect("the same list twice", names == [t["name"] for t in (await api.get("/api/extensions/tools")).json()])

        t1 = time.perf_counter()
        r = (await api.post("/api/extensions/coin/tools/flip", json={})).json()
        ms = (time.perf_counter() - t1) * 1000
        expect(f"a tool call through winstond ({ms:.0f} ms)", r["ok"] and r["result"] in ("heads", "tails"), r)

        seen = (await api.post("/api/extensions/probe/tools/seen", json={})).json()["result"]
        expect("its own key arrives in its settings", seen["key"] == "secret-one", seen)
        expect("no key is in its environment, its own or another's",
               not {"HA_TOKEN", "PROBE_KEY", "OTHER_KEY"} & set(seen["env"]), seen["env"])

        r = (await api.post("/api/extensions/probe/tools/state", json={"entity_id": "light.taklampa"})).json()
        expect("reads an exposed entity", r["ok"] and r["result"]["state"] == "on", r)
        r = (await api.post("/api/extensions/probe/tools/state", json={"entity_id": "light.kok"})).json()
        expect("refused an entity neither exposed nor picked", not r["ok"] and "neither exposed" in r["error"], r)
        fake.SERVICES = {d: s for d, s in FakeHA.SERVICES.items() if d != "weather"}     # its integration not set up
        r = (await api.post("/api/extensions/probe/tools/query", json={"entity_id": "weather.forecast_home"})).json()
        expect("a service Home Assistant does not have is refused", not r["ok"] and "change things" in r["error"], r)
        del fake.SERVICES                                   # set up now: the cached listing is read again at once
        r = (await api.post("/api/extensions/probe/tools/query", json={"entity_id": "weather.forecast_home"})).json()
        expect("a response-only service on an exposed domain", r["ok"] and "weather.forecast_home" in r["result"], r)
        r = (await api.post("/api/extensions/probe/tools/switch_off", json={"entity_id": "light.taklampa"})).json()
        expect("read permission cannot call a service", not r["ok"] and "act" in r["error"], r)
        expect("nothing reached Home Assistant for it", not any("turn_off" in w.get("type", "") for w in fake.writes))
        r = (await api.post("/api/extensions/probe/tools/home", json={})).json()
        expect("presence from the person entity", r["ok"] and r["result"] is True, r)

        r = await api.patch("/api/config", json={"changes": [{"path": ["extensions", "probe", "settings", "pick"],
                                                              "value": "light.kok"}]})
        expect("a setting saves", r.status_code == 200, r.text)
        r = (await api.post("/api/extensions/probe/tools/state", json={"entity_id": "light.kok"})).json()
        expect("picking an entity lets it read that entity", r["ok"] and r["result"]["state"] == "off", r)
        r = await api.patch("/api/config", json={"changes": [{"path": ["extensions", "probe", "settings", "mode"],
                                                              "value": "bad"}]})
        detail = r.json().get("detail", [{}])
        expect("its own check refuses a value, on the field", r.status_code == 422 and detail[0]["loc"] ==
               ["extensions", "probe", "settings", "mode"] and "Bad is not" in detail[0]["msg"], r.text)

        schema = (await api.get("/api/config/schema")).json()
        key = schema["$defs"]["Ext_probe"]["properties"]["settings"]["properties"]["key"]
        expect("its settings are in the config schema, key field and all", key["secret"]["default"] == "PROBE_KEY", key)
        listed = {s["name"]: s for s in (await api.get("/api/secrets")).json()}
        expect("its key is listed, as used by it", ["extensions", "probe", "settings", "key"] in
               listed.get("PROBE_KEY", {}).get("used_by", []), listed.get("PROBE_KEY"))
        r = await api.put("/api/secrets/PROBE_KEY", json={"value": "secret-two"})
        expect("a new key saves from this machine", r.status_code == 200, r.text)
        seen = (await api.post("/api/extensions/probe/tools/seen", json={})).json()["result"]
        expect("and reaches it without a restart", seen["key"] == "secret-two", seen)

        call = lambda name_, **args: api.post(f"/api/extensions/friend/tools/{name_}", json=args)  # noqa: E731
        r = (await call("note", text="The bins go out on Mondays.")).json()
        facts = (await call("notes")).json()
        expect("memory: written through remember's checks, and read back", r["ok"] and "Remembered" in r["result"]
               and facts["result"] == ["The bins go out on Mondays."], (r, facts))
        r = (await call("note", text="The door code is 1234.")).json()
        expect("memory: remember's own checks refuse a secret", not r["ok"], r)
        r = (await call("ask", question="Is it Monday?")).json()
        expect("the background model answers in the shape asked for", r["ok"] and r["result"] is True, r)
        expect("the model is asked with thinking off and a JSON schema", llm_seen and
               llm_seen[-1].get("response_format", {}).get("type") == "json_schema", llm_seen[-1:])
        expect("a background call keeps to the server's last slot", llm_seen[-1].get("id_slot") == 1, llm_seen[-1:])
        from winston.core.brain import Brain, build_llm, server_slot
        slots = (await server_slot(app.state.cfg, True), await server_slot(app.state.cfg, False),
                 Brain(app.state.cfg, "satellite").spoken, Brain(app.state.cfg, "satellite", spoken=False).spoken)
        expect("a spoken conversation keeps to the first slot; a check on the box's brain is not spoken",
               slots == (0, 1, True, False), slots)
        body = build_llm(app.state.cfg, "satellite", slot=0)._settings.extra["extra_body"]
        expect("the slot goes with every request of the conversation", body.get("id_slot") == 0, body)
        spends = [(await call("spend")).json()["result"] for _ in range(3)]
        expect("a daily quota runs out", spends == [True, True, False], spends)
        r = (await call("peek", tool="home")).json()
        expect("it may call another extension's read tool", r["ok"] and r["result"] is True, r)
        r = (await call("peek", tool="wipe")).json()
        expect("but not one that is not read", not r["ok"] and "no read tool" in r["error"], r)
        r = (await call("lights")).json()
        expect("no home_assistant permission: refused", not r["ok"] and "home_assistant" in r["error"], r)
        r = (await call("gear")).json()
        expect("no devices permission: refused", not r["ok"] and "devices permission" in r["error"], r)

        inv = lambda name_, **args: api.post(f"/api/extensions/inventory/tools/{name_}", json=args)  # noqa: E731
        r = (await inv("devices")).json()
        rows = {d["id"]: d for d in r.get("result") or []}
        expect("devices: every device, exposed or not, with name, maker, model, firmware and area",
               r["ok"] and len(rows) == 5 and rows["dev_ceiling"] == {
                   "id": "dev_ceiling", "name": "TAKLAMPA E27", "manufacturer": "Glimt Lighting", "model": "Ceiling E27",
                   "model_id": "GL-E27", "sw_version": "1.4.2", "hw_version": "2", "area": "Living room",
                   "service": False} and rows["dev_shelf"]["name"] == "Hyllan" and rows["dev_plex"]["service"], r)
        expect("and no addresses, serial numbers or integration ids", not any(
            w in json.dumps(r) for w in ("02:00:00:00:00:01", "GL-SERIAL", "entry_glimt", "identifiers")), r)

        use = lambda name_, **args: api.post("/api/extensions/caller/tools/use",  # noqa: E731
                                             json={"name": name_, "args": json.dumps(args)})
        r = (await api.post("/api/extensions/caller/tools/listing", json={})).json()
        tiers = {t["name"]: t["tier"] for t in r.get("result") or []}
        expect("tools: the brain's tools from Home Assistant and other extensions, with their tiers, not its own",
               r["ok"] and tiers.get("intent__HassTurnOn") == "act" and tiers.get("homeassistant__GetLiveContext")
               == "read" and tiers.get("coin_flip") == "read" and tiers.get("echo_delete_all") == "restricted"
               and not any(n.startswith("caller_") for n in tiers), r)
        r = (await use("intent__HassTurnOn", name="Taklampa")).json()
        expect("tools: a Home Assistant tool runs through its MCP server", r["ok"] and r["result"] == "Done: "
               "intent__HassTurnOn" and {"type": "mcp/intent__HassTurnOn", "name": "Taklampa"} in fake.writes, r)
        logged = [json.loads(line) for line in (DATA / "logs" / "tool_calls.jsonl").read_text().splitlines()]
        expect("tools: the call and its result are in the tool-call log", any(
            e["channel"] == "extension:caller" and e["event"] == "call" and e["tool"] == "intent__HassTurnOn"
            for e in logged) and any(e["channel"] == "extension:caller" and e["event"] == "result" for e in logged),
            logged[-3:])
        r = (await use("coin_flip")).json()
        expect("tools: another extension's tool runs", r["ok"] and r["result"] in ("heads", "tails"), r)
        r = (await use("echo_delete_all")).json()
        expect("tools: a restricted tool is refused", not r["ok"] and "restricted" in r["error"], r)
        r = (await use("intent__NoSuchThing")).json()
        expect("tools: an unknown tool is refused", not r["ok"] and "no tool" in r["error"], r)
        r = (await api.post("/api/extensions/caller/tools/diary", json={})).json()
        expect("memory: the diary of the last days", r["ok"] and [e["text"] for e in r["result"]]
               == ["He asked about the lamp."], r)

        refused = await until(lambda: stopped_because("remote", "wants a token (401)"), 20)
        expect("an HTTP MCP server that wants a token says so on its card", refused, (await states())["remote"])
        r = await api.put("/api/secrets/REMOTE_MCP_TOKEN", json={"value": "not-the-token"})
        refused = await until(lambda: stopped_because("remote", "refused its token (401)"), 20)
        expect("a wrong token: refused, and said", r.status_code == 200 and refused, (await states())["remote"]["exits"])
        r = await api.put("/api/secrets/REMOTE_MCP_TOKEN", json={"value": REMOTE_TOKEN})
        expect("with its token it runs", r.status_code == 200 and await until(lambda: running("remote"), 20),
               (await states())["remote"])
        r = (await api.post("/api/extensions/remote/tools/status", json={})).json()
        expect("and its tool answers over HTTP", r["ok"] and r["result"] == {"up": True}, r)
        leaks = [str(f.relative_to(DATA)) for f in DATA.rglob("*") if f.is_file() and f.name != ".env"
                 and REMOTE_TOKEN.encode() in f.read_bytes()]
        answers = [(await api.get(u)).text for u in ("/api/extensions", "/api/secrets", "/api/config",
                                                     "/api/config/schema", "/api/extensions/remote/log")]
        expect("the token is in data/.env only: no config, log or API answer has it",
               not leaks and not any(REMOTE_TOKEN in a for a in answers), leaks)

        opts = (await api.get("/api/options/extension_tools", params={"tool": "status"})).json()
        expect("the picker lists what has a read tool by that name", [c["value"] for c in opts["choices"]] == ["remote"],
               opts)
        r = (await inv("call", server="remote", tool="status")).json()
        expect("a server not picked: refused", not r["ok"] and "may not call remote" in r["error"], r)
        r = await api.patch("/api/config", json={"changes": [
            {"path": ["extensions", "inventory", "settings", "machines"], "value": ["remote"]},
            {"path": ["extensions", "inventory", "settings", "pokers"], "value": ["remote"]}]})
        expect("picking servers saves", r.status_code == 200, r.text)
        r = (await inv("call", server="remote", tool="status")).json()
        expect("picked, it calls the MCP server's read tool", r["ok"] and r["result"] == {"result": {"up": True}}, r)
        r = (await inv("call", server="echo", tool="echo")).json()
        expect("another server, not picked, stays refused", not r["ok"] and "may not call echo" in r["error"], r)
        r = (await inv("call", server="remote", tool="poke")).json()
        expect("picked for a tool that is not read: refused", not r["ok"] and "no read tool poke" in r["error"], r)

        r = (await api.post("/api/extensions/probe/tools/wipe", params={"channel": "web"}, json={})).json()
        expect("a restricted tool from a conversation waits", not r["ok"] and r["waiting"]
               and "Nothing has happened yet" in r["error"], r)
        pending = (await api.get("/api/confirmations")).json()
        expect("it is listed for confirming", len(pending) == 1 and pending[0]["state"] == "waiting", pending)
        feed = [e for e in app.state.feed.backlog() if e.type == "confirm"]
        expect("the Talk page's feed shows it", feed and feed[-1].status == "waiting" and "Probe" in feed[-1].label, feed)
        r = (await api.post(f"/api/confirmations/{pending[0]['id']}", json={"approve": True})).json()
        expect("confirmed, it runs", r["state"] == "ok" and r["outcome"] == "wiped", r)
        feed = [e for e in app.state.feed.backlog() if e.type == "confirm"]
        expect("and the feed shows the outcome on the same event", len(feed) == 1 and feed[0].status == "ok", feed)
        r = (await api.post("/api/extensions/probe/tools/wipe", json={})).json()
        expect("called at the machine (no conversation), it runs", r["ok"] and r["result"] == "wiped", r)

        from winston.core import guard
        from winston.core.brain import Brain
        from winston.extension_host import client as ext_client
        tools = (await api.get("/api/extensions/tools")).json()         # the token server's joined since
        brain = Brain(app.state.cfg, channel="web")
        await brain.start()
        names = [t.name for t in brain.tools.standard_tools]
        expect("a brain gets the extension tools after its own", names[-len(tools):] == [t["name"] for t in tools], names)
        expect("their tiers are known", guard.tool_tier("probe_seen") == guard.READ
               and guard.tool_tier("probe_switch_off") == guard.ACT and guard.tool_tier("probe_wipe") == guard.ACT)

        class Params:
            def __init__(self, args):
                self.arguments, self.result = args, None

            async def result_callback(self, result, **_):
                self.result = result

        spec = next(t for t in tools if t["name"] == "probe_state")
        p = Params({"entity_id": "light.taklampa"})
        await ext_client.make_handler(app.state.cfg, spec, "web", lambda: None)(p)
        expect("through the brain, outside results come marked as information", p.result == {
            "result": {"state": "on"}, "note": ext_client.OUTSIDE_NOTE}, p.result)
        spec = next(t for t in tools if t["name"] == "probe_state")
        p = Params({"entity_id": "light.hylla"})
        await ext_client.make_handler(app.state.cfg, spec, "web", lambda: None)(p)
        expect("and a refusal comes back as an error he can say", "neither exposed" in p.result.get("error", ""), p.result)
        said = []

        async def say(text):
            said.append(text)
        slow = {**next(t for t in tools if t["name"] == "probe_block"), "filler": "One moment, sir."}
        p = Params({"seconds": 1})
        await ext_client.make_handler(app.state.cfg, slow, "voice", lambda: say)(p)
        expect("a slow tool's filler is said once it takes over a second", said == ["One moment, sir."], said)
        said.clear()
        at_once = {**spec, "filler": "Let me look.", "filler_after": 0}
        await ext_client.make_handler(app.state.cfg, at_once, "voice", lambda: say)(Params({"entity_id": "light.taklampa"}))
        expect("with filler_after 0 it is said as the call starts, however quick", said == ["Let me look."], said)
        await brain.close()

        t1 = time.perf_counter()
        r = (await api.post("/api/extensions/probe/tools/block", json={"seconds": 20})).json()
        health = (await api.get("/api/health")).status_code
        took = time.perf_counter() - t1
        expect(f"a blocked extension times out ({took:.1f} s) and winstond still answers",
               not r["ok"] and "did not answer" in r["error"] and health == 200 and took < 6, r)
        restarted = await until(lambda: stopped_because("probe", "stopped answering"), 20)
        expect("a blocked extension is started again", restarted and await until(lambda: running("probe"), 20),
               (await states())["probe"])

        r = (await api.post("/api/extensions/probe/tools/hog", json={"mb": 400})).json()
        killed = await until(lambda: stopped_because("probe", "over its limit of 150 MB"), 20)
        expect("past its memory limit it is stopped, and says why", killed, (await states())["probe"])
        await until(lambda: running("probe"), 20)
        coin = (await states())["coin"]
        expect("coin kept running throughout, never restarted", coin["state"] == "running" and not coin["exits"], coin["exits"])

        for _ in range(5):
            await until(lambda: running("probe"), 20)
            await api.post("/api/extensions/probe/tools/die", json={})
            await asyncio.sleep(0.3)
        failed_state = await until(lambda: state_is("probe", "failed"), 20)
        st = (await states())["probe"]
        expect("five crashes in ten minutes stop it, with the reason", failed_state and "5 crashes" in st["detail"], st)
        r = (await api.post("/api/extensions/probe/tools/seen", json={})).json()
        expect("its tools stay listed and answer that it is not running", not r["ok"] and "not running" in r["error"]
               and "probe_seen" in [t["name"] for t in (await api.get("/api/extensions/tools")).json()], r)

        r = await api.patch("/api/config", json={"changes": [{"path": ["extensions", "coin", "enabled"], "value": False}]})
        stopped = await until(lambda: state_is("coin", "stopped"), 10)
        gone = "coin_flip" not in [t["name"] for t in (await api.get("/api/extensions/tools")).json()]
        expect("switched off: stopped and out of the tool list", r.status_code == 200 and stopped and gone)
        r = await api.patch("/api/config", json={"changes": [{"path": ["extensions", "coin", "enabled"], "value": True}]})
        expect("switched on again", await until(lambda: running("coin"), 20))

        host.scheduler.start()
        stored = lambda: api.post("/api/extensions/clock/tools/store", json={})  # noqa: E731

        async def has(key, value=None):
            got = (await stored()).json().get("result") or {}          # nothing while it restarts
            return key in got and (value is None or got[key] == value)
        expect("an every job runs once when the extension starts", await until(lambda: has("hourly", 1), 10))
        nxt = next(j for j in (await states())["clock"]["jobs"] if j["name"] == "hourly")["next_run"]
        expect("and the card shows its next run, an hour on", nxt and 3500 < nxt - time.time() < 3700, nxt)
        await api.post("/api/extensions/clock/tools/remind", json={"seconds": 1})
        expect("a timer runs its job with its data", await until(lambda: has("later", [{"n": 1}]), 10))
        await api.post("/api/extensions/clock/tools/remind", json={"seconds": 3})
        await api.post("/api/extensions/clock/restart")
        expect("a timer set before a restart still runs after it",
               await until(lambda: has("later", [{"n": 1}, {"n": 3}]), 20))
        r = await api.patch("/api/config", json={"changes": [
            {"path": ["extensions", "clock", "settings", "watch"], "value": "light.taklampa"}]})
        await asyncio.sleep(3.5)                         # the listener resubscribes within its 3 s check
        await fake.change("light.taklampa", "on")
        await fake.change("light.taklampa", "off")
        expect("a state trigger fires on its to-state, from a picked setting",
               await until(lambda: has("watched", ["light.taklampa", "on", "off"]), 10))
        await fake.change("person.someone", "not_home")
        expect("leaving home runs on_leave", await until(lambda: has("left", 1), 10))
        await fake.change("person.someone", "home")
        expect("arriving home runs on_arrive", await until(lambda: has("arrived", 1), 10))
        await fake.fire("winston_test", {"a": 1})
        expect("an event runs its job with its data", await until(lambda: has("event", {"a": 1}), 10))
        r = await api.post("/api/speakers/stopped", json={"ext": "clock", "room": "test room"})
        expect("\"stop\" to a box while its stream played runs its on_speakers_stop",
               r.status_code == 200 and await until(lambda: has("silenced", 1), 10), r.text)
        r = (await api.post("/api/extensions/clock/tools/louder", json={})).json()
        expect("with no box's volume reported, the speaker volume is not known", r["ok"] and r["result"] is None, r)
        from winston.core.common import sse
        told = []

        async def box():
            async for ev in sse(f"http://127.0.0.1:{api_port}/api/events", {"channel": "satellite", "room": "test room"}):
                if ev and ev.get("action") == "volume":
                    told.append(ev)
                    return
        listening = asyncio.create_task(box())
        await asyncio.sleep(1)
        await api.post("/api/speakers/volume", json={"room": "test room", "volume": 0.5})
        r = (await api.post("/api/extensions/clock/tools/louder", json={})).json()
        expect("an extension turns the box itself up from the volume it reported", r["ok"] and r["result"] == 0.6, r)
        await asyncio.wait_for(listening, 5)
        expect("and the box is sent the new volume", told and told[0]["level"] == 0.6, told)
        got = (await stored()).json()["result"]
        expect("nothing ran more than it should", got.get("hourly") in (1, 2) and got.get("left") == 1
               and got.get("arrived") == 1, got)

        names = [t["name"] for t in (await api.get("/api/extensions/tools")).json()]
        tiers = {t["name"]: t["tier"] for t in (await api.get("/api/extensions/tools")).json()}
        expect("an MCP server offers only its allowlisted tools", {"echo_echo", "echo_delete_all", "echo_env"} <=
               set(names) and "echo_hidden" not in names, names)
        expect("its tiers come from its annotations; none means restricted", tiers["echo_echo"] == "read"
               and tiers["echo_delete_all"] == "restricted", tiers)
        r = (await api.post("/api/extensions/echo/tools/echo", json={"text": "hello"})).json()
        expect("an MCP server's tool answers", r["ok"] and r["result"] == "hello", r)
        env_names = (await api.post("/api/extensions/echo/tools/env", json={})).json()["result"].split(",")
        expect("an MCP server gets the variables named for it and no others", "ECHO_KEY" in env_names
               and "HA_TOKEN" not in env_names and "PROBE_KEY" not in env_names, env_names)
        r = (await api.post("/api/extensions/echo/tools/delete_all", params={"channel": "voice"}, json={})).json()
        expect("its unannotated tool waits for confirmation", r.get("waiting"), r)

        from mcp.client.client import Client as MCPClient
        async with MCPClient(f"http://127.0.0.1:{api_port}/mcp") as out:          # as the docs spell it
            offered = [t.name for t in (await out.list_tools()).tools]
            expect("winstond's /mcp lists only the offered tools", offered == ["echo_echo"], offered)
            res = await out.call_tool("echo_echo", {"text": "from Claude"})
            expect("and calls them", not res.is_error and res.content[0].text == "from Claude", res)

        coin_before = host.procs["coin"].started_at
        coin_file = DATA / "extensions" / "coin.py"
        from winston.extension_host.sources import state_dir
        going = state_dir("coin") / "ongoing.txt"
        going.write_text("playing something")                       # ctx.ongoing: music plays
        coin_file.write_text(coin_file.read_text() + '\n\n@ext.tool(tier="read", outside=False)\n'
                             'async def again(ctx) -> str:\n    """Flip twice."""\n    return "twice"\n')
        friend_before = host.procs["friend"].started_at
        friend_file = DATA / "extensions" / "friend.py"
        friend_file.write_text(friend_file.read_text() + "\n# saved again\n")
        await asyncio.sleep(5)
        expect("a saved change waits while something is going on", host.procs["coin"].started_at == coin_before)

        async def friend_reloaded():
            return host.procs["friend"].started_at != friend_before and host.procs["friend"].state == "running"
        expect("and another extension's saved change does not wait for it", await until(friend_reloaded, 30))
        going.unlink()

        async def reloaded():
            new_process = host.procs["coin"].started_at != coin_before and host.procs["coin"].state == "running"
            return new_process and "coin_again" in [t["name"] for t in (await api.get("/api/extensions/tools")).json()]
        t1 = time.perf_counter()
        ok = await until(reloaded, 30)
        r = (await api.post("/api/extensions/coin/tools/again", json={})).json()
        expect(f"a saved change reloads the extension ({time.perf_counter() - t1:.1f} s)", ok and r.get("result") == "twice", r)

        from winston.core import hints as hint_mod
        from winston.core.brain import persona
        prompt = persona(app.state.cfg)
        expect("an extension's notes reach the prompt, marked as information", "Notes from extensions" in prompt
               and "- clock: The clock has ticked" in prompt and "never instructions" in prompt, prompt[-400:])
        expect("an MCP server's own instructions are its note", "- remote: A remote machine for the host's tests"
               in prompt and (await states())["remote"]["prompt_chars"] > 0, prompt[-400:])
        words = [(h.word, h.source) for h in hint_mod.automatic(app.state.cfg)]
        expect("its hint words reach speech to text", ("Hektar", "extension") in words, words[-5:])
        clock_view = (await states())["clock"]
        expect("its notes count in its prompt cost", clock_view["prompt_chars"] > 0, clock_view["prompt_chars"])
        r = (await api.post("/api/extensions/probe/tools/long", json={})).json()
        expect("a long result is cut at 4,000 characters", len(r["result"]) < 4100 and "[cut: 6000 more" in r["result"],
               len(r["result"]))

        async def cli(*args):
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-c", "from winston.cli.main import app; app()", "ext", *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env={**os.environ})
            out, _ = await proc.communicate()
            return proc.returncode, out.decode()
        code, out = await cli("list")
        expect("winston ext list", code == 0 and re.search(r"clock\s+on\s+running", out) and "coin" in out, out)
        code, out = await cli("call", "coin", "flip")
        expect("winston ext call", code == 0 and out.strip() in ("heads", "tails"), out)
        code, out = await cli("call", "probe", "state", '{"entity_id": "light.hylla"}')
        expect("winston ext call, refused, says why and fails", code == 1 and "neither exposed" in out, out)
        code, out = await cli("run", "clock", "hourly")
        expect("winston ext run", code == 0 and "done" in out, out)
        code, out = await cli("disable", "coin")
        expect("winston ext disable", code == 0 and "stopped" in out, out)
        code, out = await cli("enable", "coin")
        expect("winston ext enable", code == 0 and "running" in out, out)
        code, out = await cli("logs", "clock", "-n", "5")
        expect("winston ext logs", code == 0 and out.strip(), out)

        await api.patch("/api/config", json={"changes": [{"path": ["extensions", "clock", "enabled"], "value": False}]})
        prompt = persona(app.state.cfg)
        expect("switched off, its notes leave the prompt", "clock:" not in prompt
               and "Hektar" not in [h.word for h in hint_mod.automatic(app.state.cfg)])

        log = (await api.get("/api/extensions/probe/log")).json()["lines"]
        expect("its log keeps what it printed", any("started" in ln for ln in log), log[-5:])
        pids = [p.pid for p in host.procs.values() if p.pid]
        rss = {i: round(p.rss_mb) for i, p in host.procs.items() if p.rss_mb}
        print(f"     memory per extension process: {rss} MB")

        await host.stop_all()
        await llm_runner.cleanup()
        await stop_server()
        await api.aclose()
        alive = [p for p in pids if Path(f"/proc/{p}").exists() and "Z" not in
                 Path(f"/proc/{p}/status").read_text().split("State:")[1][:4]]
        expect("no extension process left behind", not alive, alive)


if __name__ == "__main__":
    scheduling()
    signatures()
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(DATA, ignore_errors=True)
    finish()
