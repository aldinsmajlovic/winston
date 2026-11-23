"""Home Assistant Assist over Wyoming (docs/assist.md): the protocol on a spare port with a stub answer, then a
winstond on a spare port with the fake Home Assistant and a fake streaming brain, switched on by a save. Every
advertisement goes to a recording fake, so nothing reaches the real network's mDNS.

    uv run python tests/assist_cases.py
"""
import asyncio
import json
import os
import socket
from typing import ClassVar

from harness import expect, finish, free_port, scratch_data, serve, winstond_data

DATA = scratch_data("winston-assist-")
winstond_data(DATA, {"llm": {"model": "x"}})        # the persona and memory the prompt is built from

from loguru import logger  # noqa: E402
from wyoming.asr import Transcript  # noqa: E402
from wyoming.client import AsyncTcpClient  # noqa: E402
from wyoming.handle import Handled  # noqa: E402
from wyoming.info import Describe, Info  # noqa: E402

from winston.channels import assist  # noqa: E402  (after WINSTON_DATA is set)
from winston.channels.assist import SERVICE, Listener, Unanswered  # noqa: E402
from winston.config.schema import WinstonConfig  # noqa: E402

logger.remove()


class FakeZeroconf:
    """Records what would have been advertised; never touches the network."""
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


assist.ZEROCONF = FakeZeroconf
assist.local_address = lambda: "127.0.0.1"


async def stub_answer(req):
    if "fail" in req.text:
        raise Unanswered("Sorry, I could not reach my brain.")
    return ("Which room, sir?", True) if "ask" in req.text else ("Done.", False)


async def exchange(port: int, event) -> object:
    async with AsyncTcpClient("127.0.0.1", port) as c:
        await c.write_event(event)
        return await asyncio.wait_for(c.read_event(), 5)


async def protocol():
    cfg = WinstonConfig.model_validate({"llm": {"model": "x"}})
    port, seen = free_port(), []
    lis = Listener()
    await lis.start(cfg, port, stub_answer, lambda: seen.append(1))
    zc = FakeZeroconf.made[-1]

    info = Info.from_event(await exchange(port, Describe().event()))
    expect("Describe: one installed handle program, named after him",
           [(h.name, h.installed, h.supports_home_control) for h in info.handle] == [("Winston", True, False)])
    expect("every language", info.handle[0].models[0].languages == [])
    ctx = {"conversation_id": "c1"}
    ev = await exchange(port, Transcript("turn on the lights", context=ctx, language="en").event())
    expect("a request gets the reply", (ev.type, ev.data.get("text")) == ("handled", "Done."), ev)
    ev2 = await exchange(port, Transcript("ask me", context=ctx).event())
    expect("a question listens again", Handled.from_event(ev2).continue_conversation is True, ev2)
    ev3 = await exchange(port, Transcript("fail now", context=ctx).event())
    expect("a failure is said, not dropped",
           (ev3.type, ev3.data.get("text")) == ("not-handled", "Sorry, I could not reach my brain."), ev3)
    expect("every event is seen", len(seen) == 4, len(seen))
    expect("advertised as a Wyoming service on its port", (zc.info.type, zc.info.port) == (SERVICE, port))
    expect("under his name", zc.info.name == f"Winston.{SERVICE}", zc.info.name)

    taken = socket.socket(); taken.bind(("0.0.0.0", free_port())); taken.listen()
    lis2, raised = Listener(), False
    try:
        await lis2.start(cfg, taken.getsockname()[1], stub_answer, lambda: None)
    except OSError:
        raised = True
    taken.close()
    expect("a taken port raises before advertising", raised and FakeZeroconf.made[-1] is zc)

    await lis.stop()
    expect("stopped: withdrawn and closed", zc.calls[-2:] == ["unregister", "close"], zc.calls)
    try:
        await exchange(port, Describe().event()); refused = False
    except OSError:
        refused = True
    expect("stopped: the port is closed", refused)

    expect("asks: a question mark anywhere", (assist.asks("Which one? The lamp."), assist.asks("Done.")) == (True, False))
    r = assist.Request.of(Transcript("hi", context=None))
    expect("no conversation id: a new one", len(r.conversation_id) == 32 and r.device_id is None)


def pure():
    from winston.core.brain import persona, thinking_for
    from winston.core.rooms import room_for_area
    cfg_home = WinstonConfig.model_validate({"llm": {"model": "x", "thinking": {"spoken": "always", "typed": "never"}}})
    expect("assist thinks as a spoken channel",
           (thinking_for(cfg_home, "assist"), thinking_for(cfg_home, "text")) == (True, False))
    cfg_breeze = WinstonConfig.model_validate({"llm": {"model": "x"}, "engines": {"tts": {
        "driver": "subprocess", "cmd": ["python", "server.py"], "cwd": "services/tts-breeze2", "port": 9000}}})
    expect("the box's prompt has vocal events", "[sigh]" in persona(cfg_breeze, "", "satellite"))
    expect("assist's prompt has none", "[sigh]" not in persona(cfg_breeze, "", "assist"))
    cfg_box = WinstonConfig.model_validate({"llm": {"model": "x"}, "channels": {"satellite": {"devices": {
        "main room": {"host": "10.0.0.2", "area": "Living room"}}}}})
    expect("a configured room by its area", room_for_area(cfg_box, "living Room") == "main room")
    expect("else the area itself", room_for_area(cfg_box, "Kitchen") == "Kitchen")

# --- a winstond answering Home Assistant (Task 3), and speaking on its satellites (Task 4)

async def fake_brain(port: int):
    """An OpenAI-style brain that streams its reply, with llama.cpp's two slots; records every request and
    answers by the newest user message."""
    from aiohttp import web
    seen: list[dict] = []

    def reply_for(said: str) -> str | None:
        if "break" in said:
            return None
        if "Speaking up" in said:
            return "Your parcel has arrived, sir."
        if "colour" in said:
            return "Which colour, sir?"
        return "Done, sir."

    async def complete(request):
        body = await request.json()
        seen.append(body)
        said = next((m["content"] for m in reversed(body["messages"]) if m["role"] == "user"), "")
        said = said if isinstance(said, str) else " ".join(p.get("text", "") for p in said)
        text = reply_for(said)
        if text is None:
            return web.json_response({"error": {"message": "made-up failure"}}, status=500)
        if not body.get("stream"):
            return web.json_response({"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                                      "choices": [{"index": 0, "finish_reason": "stop",
                                                   "message": {"role": "assistant", "content": text}}]})
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for delta, stop_reason in (({"role": "assistant", "content": text}, None), ({}, "stop")):
            chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": body["model"],
                     "choices": [{"index": 0, "delta": delta, "finish_reason": stop_reason}]}
            await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await resp.write(b"data: [DONE]\n\n")
        return resp
    app = web.Application()
    app.add_routes([web.post("/v1/chat/completions", complete),
                    web.get("/props", lambda _: web.json_response({"total_slots": 2}))])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    return runner, seen


async def handled(port: int, text: str, **context) -> object:
    """Home Assistant's request: a new connection, the transcript, the answer."""
    async with AsyncTcpClient("127.0.0.1", port) as c:
        await c.write_event(Transcript(text, context=context, language="en").event())
        return await asyncio.wait_for(c.read_event(), 120)


def last_user(seen: list[dict]) -> str:
    m = next(m for m in reversed(seen[-1]["messages"]) if m["role"] == "user")
    return m["content"] if isinstance(m["content"], str) else " ".join(p.get("text", "") for p in m["content"])


def transcript(body: dict) -> str:
    return " ".join(str(m.get("content")) for m in body["messages"])


async def winstond():
    import httpx
    from fake_ha import FakeHA
    from winston.api.app import create_app
    from winston.config import load
    from winston.core import directory

    ha_port, api_port, llm_port, port = free_port(), free_port(), free_port(), free_port()
    winstond_data(DATA, {
        "llm": {"model": "fake", "base_url": f"http://127.0.0.1:{llm_port}/v1"},
        "channels": {"voice": {"enabled": False}, "satellite": {"enabled": False}, "discord": {"enabled": False}},
        "providers": {"home_assistant": {"url": f"http://127.0.0.1:{ha_port}", "token_env": "HA_TOKEN",
                                         "expose": {"light.taklampa": {}}}},
        "control": {"port": api_port}})
    runner, seen = await fake_brain(llm_port)
    async with FakeHA(ha_port):
        app = create_app(load(), autostart=False)
        stop = await serve(app, api_port)
        api = httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}", timeout=60)

        saved: list[dict] = []

        async def save(**assist):
            r = await api.patch("/api/config", json={"changes": [
                {"path": ["channels", "assist", k], "value": v} for k, v in assist.items()]})
            saved.append(r.json())
            return r.status_code, (await api.get("/api/assist")).json()

        try:
            made = len(FakeZeroconf.made)
            code, st = await save(enabled=True, port=port)
            expect("switched on by a save, listening", (code, st["listening"], st["error"]) == (200, True, None), st)
            expect("applied on save, nothing to restart", saved[-1]["restarts"] == [], saved[-1])
            zc = FakeZeroconf.made[-1]
            expect("and advertised", len(FakeZeroconf.made) == made + 1 and zc.info.port == port)

            ev = await handled(port, "turn on the lights", conversation_id="app-1")
            expect("a typed request from the app is answered", (ev.type, ev.data.get("text")) == ("handled", "Done, sir."), ev)
            expect("Home Assistant was seen", (await api.get("/api/assist")).json()["last_seen"] is not None)
            expect("no area: no room in the stamp", "room:" not in last_user(seen), last_user(seen))
            await handled(port, "and the other one", conversation_id="app-1")
            expect("the same id continues it", "turn on the lights" in transcript(seen[-1]))
            await handled(port, "hello there", conversation_id="app-2")
            expect("a new id starts afresh", "turn on the lights" not in transcript(seen[-1]))

            ev = await handled(port, "the lights please", conversation_id="sat-1", device_id="dev_satellite",
                               satellite_id="assist_satellite.kitchen_satellite")
            expect("from a satellite in an area: stamped with that room", "room: Kitchen" in last_user(seen),
                   last_user(seen))
            kitchen = app.state.rooms.room("Kitchen")
            expect("and in that room's conversation, the room last talked in",
                   (len(kitchen.messages) > 0, app.state.rooms.recent()) == (True, "Kitchen"), kitchen.messages)
            ev = await handled(port, "what colour?", conversation_id="app-1")
            expect("a question listens again", Handled.from_event(ev).continue_conversation is True, ev)
            ev = await handled(port, "break it", conversation_id="app-1")
            expect("a failing brain is said",
                   (ev.type, ev.data.get("text")) == ("not-handled", "Sorry, something went wrong while I answered."), ev)
            feed = [e.model_dump() for e in app.state.feed.backlog()]
            expect("the Talk page shows the turn under assist",
                   any(e["channel"] == "assist" and e["type"] == "user" for e in feed))
            expect("and the error", any(e["channel"] == "assist" and e["type"] == "error" for e in feed))

            taken = socket.socket(); taken.bind(("0.0.0.0", 0)); taken.listen()
            busy = taken.getsockname()[1]
            code, st = await save(port=busy)
            expect("a taken port: saved, said, not advertised",
                   (code, st["listening"], st["error"], zc.calls[-2:], len(FakeZeroconf.made)) ==
                   (200, False, f"Port {busy} is in use by another program.", ["unregister", "close"], made + 1), st)
            taken.close()
            code, st = await save(port=port)
            expect("the next save with a free port starts it", (st["listening"], st["error"]) == (True, None), st)
            zc = FakeZeroconf.made[-1]
            code, st = await save(enabled=False)
            try:
                await handled(port, "anyone?"); refused = False
            except OSError:
                refused = True
            expect("switched off: closed and withdrawn", (refused, zc.calls[-2:], st["listening"]) ==
                   (True, ["unregister", "close"], False), st)

            await save(enabled=True, port=port)
            token = os.environ.pop("HA_TOKEN")
            directory._read = (-directory.READ_SECS, (), None)
            ev = await handled(port, "the lights please", conversation_id="sat-2", device_id="dev_satellite")
            expect("Home Assistant not connected: answered, with no room",
                   ev.type == "handled" and "room:" not in last_user(seen), (ev, last_user(seen)))
            os.environ["HA_TOKEN"] = token
            directory._read = (-directory.READ_SECS, (), None)
        finally:
            await api.aclose()
            await stop()
            await runner.cleanup()


async def main():
    await protocol()
    await winstond()

pure()
asyncio.run(main())
finish()
