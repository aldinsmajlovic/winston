"""Home Assistant Assist over Wyoming (docs/assist.md): the protocol on a spare port with a stub answer,
and the advertisement through a recording fake, so nothing reaches the real network's mDNS.

    uv run python tests/assist_cases.py
"""
import asyncio
import socket
from typing import ClassVar

from harness import expect, finish, free_port, scratch_data, winstond_data

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


pure()
asyncio.run(protocol())
finish()
