"""The lockdown state (core/lockdown), secrets.this_computer, and winstond holding calls for the phone
(a throwaway winstond on the fake Home Assistant). No hardware needed.

    uv run python tests/lockdown_cases.py
"""
import asyncio
import time
from types import SimpleNamespace

import harness
from harness import expect, free_port, scratch_data, serve, until, winstond_data

DATA = scratch_data("winston-lockdown-")

from winston.api.secrets import this_computer  # noqa: E402  (after WINSTON_DATA is set)
from winston.core import lockdown as L  # noqa: E402

expect("off by default", L.state() is None and not L.on())
expect("on", L.turn_on("voice") and L.on() and L.state()["by"] == "voice")
expect("on twice is a no-op", L.turn_on("cli") is False and L.state()["by"] == "voice")
(DATA / "lockdown.json").write_text("{not json")
expect("an unreadable file counts as on", L.on())
expect("off", L.turn_off("his computer") and not L.on() and L.turn_off("x") is False)
expect("guarded places", [L.guarded(p, False) for p in ("voice", "satellite", "assist", "web", "discord", "text")]
       == [True, True, True, False, False, False] and L.guarded("discord", True))
w = L.windows(0, time.time() + 1)
expect("one closed window from the log", len(w) == 1 and w[0][0] < w[0][1], w)
L.turn_on("voice")
expect("an open window ends at end", L.windows(0, 5e9)[-1][1] == 5e9)
L.turn_off("cli")


def req(client, host):
    return SimpleNamespace(client=SimpleNamespace(host=client), headers={"host": host})


expect("this computer", this_computer(req("127.0.0.1", "127.0.0.1:8700")))
expect("a reverse proxy is not", not this_computer(req("127.0.0.1", "winston.lan")))
expect("another machine is not", not this_computer(req("192.168.1.20", "localhost")))

async def winstond():
    import httpx
    from fake_ha import FakeHA
    from winston.api.app import create_app
    from winston.config import load

    ha_port, api_port = free_port(), free_port()
    cfg = {"llm": {"model": "fake", "base_url": "http://127.0.0.1:9/v1"},
           "channels": {"voice": {"enabled": False}, "satellite": {"enabled": False}, "discord": {"enabled": False}},
           "providers": {"home_assistant": {"url": f"http://127.0.0.1:{ha_port}", "token_env": "HA_TOKEN",
                                            "expose": {"light.taklampa": {}}}},
           "control": {"port": api_port}, "speak_up": {"phone": "notify.mobile_app_test_phone"},
           "extensions": {"probe": {"enabled": True}, "friend": {"enabled": True}}}
    winstond_data(DATA, cfg, fixtures=("probe.py", "friend.py"), env='PROBE_KEY="x"\n')
    async with FakeHA(ha_port) as fake:
        app = create_app(load(), autostart=False)
        stop = await serve(app, api_port)
        host = app.state.extensions
        await host.start()
        api = httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}", timeout=30)

        async def confirmations(waiting=False):
            cs = (await api.get("/api/confirmations")).json()
            return [c for c in cs if c["state"] == "waiting"] if waiting else cs

        async def notified():
            return [w for w in fake.writes if w["type"] == "service/notify.mobile_app_test_phone"]

        async def ran():
            return all((p := host.procs.get(i)) and p.state == "running" for i in ("probe", "friend"))

        try:
            await until(ran, 30)
            tools = {t["name"]: t for t in host.brain_tools()}
            expect("a tool of an extension with memory is private, one without is not",
                   tools["friend_note"]["private"] and not tools["probe_state"]["private"])
            app.state.rooms.room("kitchen").messages = ["hello"]
            r = (await api.post("/api/lockdown", json={"on": True})).json()
            s = (await api.get("/api/lockdown")).json()
            expect("on at once, who set it, and the rooms forgotten", r["on"] and s["on"] and s["by"]
                   and not app.state.rooms.room("kitchen").messages, (r, s))

            hold = {"name": "intent__HassTurnOn", "args": {"name": "Taklampa"}, "channel": "satellite"}
            r = (await api.post("/api/lockdown/hold", json=hold)).json()
            await until(notified, 10)
            cs, sent = await confirmations(), await notified()
            expect("a Home Assistant call waits, with a Lockdown notification, and has not run",
                   r == {"waiting": True, "note": L.WAITING, "error": None} and len(cs) == 1
                   and cs[0]["label"].startswith("Lockdown: ") and sent[0]["message"].startswith("Lockdown: ")
                   and sent[0]["message"].endswith("Asked on a room box.")
                   and not any(w["type"] == "mcp/intent__HassTurnOn" for w in fake.writes), (r, cs, sent))
            c = (await api.post(f"/api/confirmations/{cs[0]['id']}", json={"approve": True})).json()
            expect("approved, it runs", c["state"] == "ok" and any(
                w["type"] == "mcp/intent__HassTurnOn" for w in fake.writes), c)

            note = {"name": "friend_note", "channel": "voice", "args": {"text": "The hold test passed."}}
            r = (await api.post("/api/lockdown/hold", json=note)).json()
            cs = await confirmations(True)
            expect("an extension's act tool waits", r["waiting"] and len(cs) == 1
                   and cs[0]["label"].startswith("Lockdown: Friend: "), (r, cs))
            c = (await api.post(f"/api/confirmations/{cs[0]['id']}", json={"approve": True})).json()
            expect("and runs once approved, its result the outcome",
                   c["state"] == "ok" and "Remembered" in c["outcome"], c)

            before = len(await confirmations())
            rs = [(await api.post("/api/lockdown/hold", json={"name": n, "args": {}, "channel": "voice"})).json()
                  for n in ("remember", "get_diary")]
            expect("a name that is no tool of his is refused, and nothing is queued",
                   all(x == {"waiting": False, "note": None, "error": L.UNAVAILABLE} for x in rs)
                   and len(await confirmations()) == before, rs)

            r = (await api.post("/api/lockdown", json={"on": False, "channel": "voice"})).json()
            cs = await confirmations(True)
            expect("off from a conversation waits, and it is still on", r["waiting"] and r["on"]
                   and L.on() and len(cs) == 1 and cs[0]["label"] == "Lockdown: turn off", (r, cs))
            await api.post(f"/api/confirmations/{cs[0]['id']}", json={"approve": True})
            expect("approving it turns it off", not L.on() and not (await api.get("/api/lockdown")).json()["on"])
            await api.post("/api/lockdown", json={"on": True})
            r = (await api.post("/api/lockdown", json={"on": False})).json()
            expect("off without a conversation is at once", not r["on"] and not r["waiting"] and not L.on(), r)
        finally:
            await api.aclose()
            await host.stop_all()
            await stop()


asyncio.run(winstond())

print(f"\n{'all passed' if not harness.failed else f'{harness.failed} FAILED'} (scratch data in {DATA})")
raise SystemExit(1 if harness.failed else 0)
