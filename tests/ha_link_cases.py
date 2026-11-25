"""The listener Winston's Home Assistant integration talks to (api/ha_link.py, docs/ha-integration.md): the
pairing code, then a winstond on a spare port with the fake Home Assistant, a made-up brain
(tests/fake_openai.py) and the chores extension, switched on by a save, added from Ways to talk and paired,
streamed and asked as the integration does. Every advertisement goes to a recording
fake, so nothing reaches the real network's mDNS.

    uv run python tests/ha_link_cases.py
"""
import asyncio
import json
import time
from typing import ClassVar

from harness import expect, finish, free_port, scratch_data, serve, until, winstond_data

DATA = scratch_data("winston-ha-link-")

from loguru import logger  # noqa: E402

from winston.api import ha_link  # noqa: E402  (after WINSTON_DATA is set)
from winston.channels import assist  # noqa: E402

logger.remove()


class FakeZeroconf:
    made: ClassVar[list["FakeZeroconf"]] = []

    def __init__(self, *a, **kw):
        self.calls: list[str] = []
        self.info = None
        FakeZeroconf.made.append(self)

    async def async_register_service(self, info, **kw):
        self.calls.append("register"); self.info = info

    async def async_unregister_service(self, info):
        self.calls.append("unregister")

    async def async_close(self):
        self.calls.append("close")


ha_link.ZEROCONF = FakeZeroconf
assist.local_address = lambda: "127.0.0.1"


def versions():
    import winston
    from harness import REPO
    manifest = json.loads((REPO / "custom_components" / "winston" / "manifest.json").read_text())
    expect("the integration's version is Winston's", manifest["version"] == winston.__version__,
           (manifest["version"], winston.__version__))


def pairing():
    p = ha_link.Pairing()
    expect("no code open: nothing pairs", not p.take("000000"))
    code = p.new()
    expect("a code is six digits", len(code) == 6 and code.isdigit(), code)
    expect("the right code pairs once", p.take(code) and not p.take(code))
    code = p.new()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(ha_link.CODE_TRIES):
        p.take(wrong)
    expect("five wrong tries void it", not p.take(code))
    code = p.new(); p.until = time.time() - 1
    expect("an expired code does not pair", not p.take(code))


class Stream:
    """The integration's side of /stream: snapshots and events as they come."""

    def __init__(self, client, token: str):
        self.client, self.token = client, token
        self.snaps: list[dict] = []
        self.events: list[dict] = []
        self.ended = False
        self.task = asyncio.create_task(self._run())

    async def _run(self):
        async with self.client.stream("GET", "/stream", headers={"Authorization": f"Bearer {self.token}"},
                                      timeout=None) as r:
            self.status = r.status_code
            kind = None
            async for line in r.aiter_lines():
                if line.startswith("event: "):
                    kind = line[7:]
                elif line.startswith("data: "):
                    (self.snaps if kind == "snapshot" else self.events).append(json.loads(line[6:]))
        self.ended = True

    def last(self) -> dict:
        return self.snaps[-1] if self.snaps else {}

    def entity(self, key: str, entry: str | None = None) -> dict:
        ext = next((e for e in self.last().get("extensions", []) if e["id"] == "chores"), {"entities": []})
        return next((e for e in ext["entities"] if e["key"] == key and e["entry"] == entry), {})


async def winstond():
    import httpx
    from fake_ha import FakeHA
    from winston.api.app import create_app
    from winston.config import load
    from winston.core.live import LiveEvent
    from winston.core.announce import Announcement  # noqa: F401
    import winston.extension_host.process as P
    P.BACKOFF, P.WATCH_EVERY, P.PING_EVERY, P.PING_MISSES = (0.2, 0.2, 0.2), 0.3, 1.0, 2

    import threading
    from http.server import ThreadingHTTPServer
    import fake_openai
    ha_port, api_port, port, llm_port = free_port(), free_port(), free_port(), free_port()
    brain = ThreadingHTTPServer(("127.0.0.1", llm_port), fake_openai.Handler)
    threading.Thread(target=brain.serve_forever, daemon=True).start()
    winstond_data(DATA, {
        "llm": {"model": "fake", "base_url": f"http://127.0.0.1:{llm_port}/v1"},
        "channels": {"voice": {"enabled": False}, "discord": {"enabled": False},
                     "satellite": {"enabled": False, "devices": {"Kitchen": {"host": "127.0.0.1"}}}},
        "providers": {"home_assistant": {"url": f"http://127.0.0.1:{ha_port}", "token_env": "HA_TOKEN"}},
        "control": {"port": api_port}, "extensions": {"chores": {"enabled": True}}}, fixtures=("chores.py",))
    async with FakeHA(ha_port) as fake:
        app = create_app(load(), autostart=False)
        stop = await serve(app, api_port)
        host = app.state.extensions
        await host.start()
        api = httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}", timeout=30)
        link = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30)
        streams: list[Stream] = []
        try:
            ok = await until(lambda: asyncio.sleep(0, result=host.procs.get("chores") is not None
                                                   and host.procs["chores"].state == "running"), 30)
            expect("the chores extension runs", ok)
            st = (await api.get("/api/ha_integration")).json()
            expect("off by default, not paired", (st["enabled"], st["listening"], st["paired"]) == (False, False, False), st)

            r = await api.patch("/api/config", json={"changes": [
                {"path": ["channels", "ha_integration", "enabled"], "value": True},
                {"path": ["channels", "ha_integration", "port"], "value": port}]})
            st = (await api.get("/api/ha_integration")).json()
            expect("switched on by a save, nothing to restart",
                   (r.status_code, r.json().get("restarts"), st["listening"], st["error"]) == (200, [], True, None),
                   (r.json(), st))
            expect("Home Assistant's satellites speak for him, as with Assist", app.state.hub.outside is not None)
            zc = FakeZeroconf.made[-1]
            expect("advertised with its instance id",
                   zc.info.port == port and zc.info.properties.get(b"instance") == ha_link.instance().encode(),
                   zc.info.properties if zc.info else None)

            r = await api.post("/api/ha_integration/add")
            expect("added from here, with Winston's Home Assistant token: Home Assistant paired at this address",
                   r.status_code == 200 and r.json()["paired"] and any(
                       w["type"] == "config_entries/winston" and w["port"] == port for w in fake.writes),
                   (r.text, fake.writes))
            fake.winston_installed = False
            r = await api.post("/api/ha_integration/add")
            expect("not downloaded in Home Assistant yet: says so", r.status_code == 409
                   and r.json()["detail"] == ha_link.NOT_INSTALLED, r.text)
            fake.winston_installed = True
            fake.winston_reachable = False                     # a firewall between them
            r = await api.post("/api/ha_integration/add")
            expect("Home Assistant cannot reach this computer: the address and port to open, its flow closed",
                   r.status_code == 409 and f"127.0.0.1, port {port}" in r.json()["detail"]
                   and fake.writes[-1]["type"] == "config_entries/flow_delete", (r.text, fake.writes[-1]))
            fake.winston_reachable = True
            await api.delete("/api/ha_integration/pairing")

            r = await link.get("/stream")
            expect("the stream needs a token", r.status_code == 401, r.status_code)
            r = await link.get("/api/config")
            expect("the control API is not on this port", r.status_code == 404, r.status_code)
            r = await link.post("/pair", json={"code": "123456"})
            expect("no code made: pairing refused", r.status_code == 403, r.text)

            st = (await api.post("/api/ha_integration/code")).json()
            expect("a code is shown for ten minutes",
                   st["code"] and 590 < st["code_until"] - time.time() <= 600, st)
            r = await link.post("/pair", json={"code": st["code"]})
            body = r.json()
            expect("the code pairs: a token, his instance and name",
                   r.status_code == 200 and len(body.get("token", "")) > 30
                   and (body["instance"], body["name"]) == (ha_link.instance(), "Winston"), body)
            token = body["token"]
            st = (await api.get("/api/ha_integration")).json()
            expect("paired, the code gone, no token in the answer",
                   (st["paired"], st["code"]) == (True, None) and token not in json.dumps(st), st)
            expect("the token is in data/.env", token in (DATA / ".env").read_text())

            s = Stream(link, token); streams.append(s)
            await until(lambda: asyncio.sleep(0, result=bool(s.snaps)), 10)
            snap = s.last()
            expect("a snapshot first: instance, the Kitchen idle",
                   snap.get("instance") == ha_link.instance() and snap.get("rooms") == [{"name": "Kitchen", "state": "idle"}],
                   snap)
            ents = {(e["key"], e["entry"]): e for x in snap.get("extensions", []) for e in x["entities"]}
            expect("the extension's entities, one button per chore",
                   set(ents) == {("done", "Dishes"), ("done", "Laundry"), ("charging", None), ("count", None)}, list(ents))
            expect("named with the entry, taking presses and changes",
                   (ents[("done", "Dishes")]["name"], ents[("done", "Dishes")]["takes"], ents[("charging", None)]["takes"])
                   == ("Dishes done", "press", "change"), ents.get(("done", "Dishes")))

            app.state.feed.publish(LiveEvent(channel="satellite", type="state", state="speaking", room="Kitchen"))
            ok = await until(lambda: asyncio.sleep(0, result=s.last()["rooms"][0]["state"] == "speaking"), 5)
            expect("a room's state reaches it at once", ok, s.last().get("rooms"))

            r = await link.post("/set", json={"extension": "chores", "key": "done", "entry": "Dishes", "press": True},
                                headers={"Authorization": f"Bearer {token}"})
            expect("a press runs the extension's on_press", r.status_code == 200 and r.json()["ok"], r.text)
            ok = await until(lambda: asyncio.sleep(0, result=s.entity("count").get("value") == 1), 5)
            expect("and the sensor it set reaches the stream", ok, s.entity("count"))

            r = await link.post("/set", json={"extension": "chores", "key": "charging", "value": True},
                                headers={"Authorization": f"Bearer {token}"})
            ok = await until(lambda: asyncio.sleep(0, result=s.entity("charging").get("value") is True), 5)
            expect("a switch turned on: handled, and on", r.status_code == 200 and ok, (r.text, s.entity("charging")))
            r = await link.post("/set", json={"extension": "chores", "key": "charging", "value": "refuse"},
                                headers={"Authorization": f"Bearer {token}"})
            await asyncio.sleep(0.5)
            expect("a handler that raises: an error, the value kept",
                   r.status_code == 500 and "not now" in r.json()["error"] and s.entity("charging")["value"] is True,
                   r.text)
            r = await link.post("/set", json={"extension": "chores", "key": "count", "value": 5},
                                headers={"Authorization": f"Bearer {token}"})
            expect("a sensor cannot be set from Home Assistant", r.status_code == 500, r.text)
            r = await link.post("/set", json={"extension": "chores", "key": "done", "press": True})
            expect("setting needs the token", r.status_code == 401, r.status_code)

            r = await link.post("/converse", json={"text": "Good evening"})
            expect("asking needs the token", r.status_code == 401, r.status_code)
            async with link.stream("POST", "/converse", headers={"Authorization": f"Bearer {token}"},
                                   json={"text": "Good evening", "conversation_id": "ha-1", "area": "Kitchen"}) as r:
                lines = [json.loads(x) async for x in r.aiter_lines() if x]
            pieces = "".join(x["text"] for x in lines if set(x) == {"text"})
            expect("asked as Home Assistant's agent: the reply streams, then the whole of it",
                   r.headers["content-type"].startswith("application/x-ndjson") and len(lines) >= 2
                   and pieces.strip() == fake_openai.REPLY
                   and lines[-1] == {"done": True, "text": fake_openai.REPLY, "continue": False}, lines)
            turns = [e for e in app.state.feed.backlog() if e.channel == "assist" and e.type == "user"]
            expect("shown on the Talk page in the area's room", turns and turns[-1].room == "Kitchen",
                   [(e.text, e.room) for e in turns])

            await api.post("/api/announce", json={"text": "Dinner is ready."})
            ok = await until(lambda: asyncio.sleep(0, result=any(e.get("type") == "announcement" for e in s.events)), 5)
            expect("an announcement is an event", ok, s.events)
            app.state.extensions.nudges.feed({"type": "nudge", "channel": "extensions", "call": "n1", "text": "Train.",
                                              "label": "Chores: x", "name": "silent", "ext": "chores", "kind": "x",
                                              "level": "normal"})
            ok = await until(lambda: asyncio.sleep(0, result=any(e.get("type") == "nudge" for e in s.events)), 5)
            expect("a nudge is an event, with its extension", ok and next(
                e for e in s.events if e["type"] == "nudge")["extension"] == "chores", s.events)

            await api.patch("/api/config", json={"changes": [
                {"path": ["extensions", "chores", "publish"], "value": False}]})
            ok = await until(lambda: asyncio.sleep(0, result=s.last().get("extensions") == []), 5)
            expect("publishing switched off on its card: its device leaves", ok, s.last().get("extensions"))

            await api.delete("/api/ha_integration/pairing")
            ok = await until(lambda: asyncio.sleep(0, result=s.ended), ha_link.TICK_SECS + 3)
            r = await link.get("/stream", headers={"Authorization": f"Bearer {token}"})
            expect("unpaired: the stream ends and the token is refused", ok and r.status_code == 401, r.status_code)

            await api.patch("/api/config", json={"changes": [
                {"path": ["channels", "ha_integration", "enabled"], "value": False}]})
            st = (await api.get("/api/ha_integration")).json()
            expect("switched off: the port closes, the advertisement withdrawn, the satellites quiet",
                   st["listening"] is False and zc.calls[-2:] == ["unregister", "close"]
                   and app.state.hub.outside is None, (st, zc.calls))
            try:
                await link.get("/stream")
                closed = False
            except httpx.ConnectError:
                closed = True
            expect("and nothing answers on it", closed)
        finally:
            for s in streams:
                s.task.cancel()
            await app.state.extensions.stop_all()
            await stop()
            brain.shutdown()


async def main():
    versions()
    pairing()
    await winstond()
    finish()


if __name__ == "__main__":
    asyncio.run(main())
