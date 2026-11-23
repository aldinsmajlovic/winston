"""Home Assistant Assist over Wyoming: winstond answers Home Assistant's built-in Wyoming integration as
a conversation agent ("handle" service). See docs/assist.md.

The protocol as Home Assistant uses it (its components/wyoming, and the wyoming 1.11.0 package it pins):
- `Describe` is answered with `Info(handle=[HandleProgram])`; Home Assistant names the entry after the first
  installed handle program. An empty `languages` list on a model offers the agent for every language, and
  `supports_home_control` would mark it as able to control the home (off: every request goes through him).
- Each request is a new TCP connection with `Transcript(text, context={"conversation_id", "device_id"?,
  "satellite_id"?}, language)`. Home Assistant reads `Handled(text, continue_conversation)` or
  `NotHandled(text)` and speaks the text of either; it never reads `HandledChunk`, so the reply goes whole.
- Discovery is zeroconf, `_wyoming._tcp.local.`. Wyoming has no authentication, so the switch is off by default.
"""
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial

from loguru import logger
from wyoming.asr import Transcript
from wyoming.event import Event
from wyoming.handle import Handled, NotHandled
from wyoming.info import Attribution, Describe, HandleModel, HandleProgram, Info
from wyoming.server import AsyncEventHandler, AsyncTcpServer
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

import winston
from winston.config.schema import WinstonConfig

SERVICE = "_wyoming._tcp.local."
MDNS_GROUP = "224.0.0.251"
ZEROCONF = AsyncZeroconf        # swapped in tests, so no test winstond is ever "Discovered"


def info(cfg: WinstonConfig) -> Info:
    """What Home Assistant is told he is: one installed handle program under his name, for every language."""
    by = Attribution(name="Winston", url="https://github.com/aldinsmajlovic/winston")
    desc = f"{cfg.assistant.name}, a self-hosted voice assistant"
    return Info(handle=[HandleProgram(
        name=cfg.assistant.name, attribution=by, installed=True, description=desc, version=winston.__version__,
        models=[HandleModel(name=cfg.assistant.name, attribution=by, installed=True, description=desc,
                            version=winston.__version__, languages=[])],
        supports_handled_streaming=False, supports_home_control=False)])


def asks(reply: str) -> bool:
    """His reply asks something, so a satellite listens again without the wake word (as the room box does)."""
    return "?" in reply


@dataclass
class Request:
    text: str
    conversation_id: str
    device_id: str | None
    satellite_id: str | None
    language: str | None

    @classmethod
    def of(cls, t: Transcript) -> "Request":
        c = t.context or {}
        return cls(t.text, c.get("conversation_id") or uuid.uuid4().hex, c.get("device_id"),
                   c.get("satellite_id"), t.language)


class Unanswered(Exception):
    """The request could not be answered; the message is what Home Assistant says instead."""


Answer = Callable[[Request], Awaitable[tuple[str, bool]]]


class Handler(AsyncEventHandler):
    """One connection from Home Assistant: a Describe or one request, then it closes."""

    def __init__(self, cfg: WinstonConfig, answer: Answer, seen: Callable[[], None], reader, writer):
        super().__init__(reader, writer)
        self.cfg, self.answer, self.seen = cfg, answer, seen

    async def handle_event(self, event: Event) -> bool:
        self.seen()
        if Describe.is_type(event.type):
            await self.write_event(info(self.cfg).event())
            return True
        if not Transcript.is_type(event.type):
            return True
        req = Request.of(Transcript.from_event(event))
        try:
            text, more = await self.answer(req)
            await self.write_event(Handled(text=text, continue_conversation=more).event())
        except Unanswered as e:
            await self.write_event(NotHandled(text=str(e)).event())
        return False


def local_address() -> str | None:
    """The IPv4 address of the interface that routes to the mDNS group (nothing is sent), or None."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((MDNS_GROUP, 1))
            return s.getsockname()[0]
    except OSError:
        return None


class Listener:
    """The Wyoming port on every interface, and its zeroconf advertisement while it is open."""

    def __init__(self):
        self.server: AsyncTcpServer | None = None
        self.zc = self.service = None

    async def start(self, cfg: WinstonConfig, port: int, answer: Answer, seen: Callable[[], None]):
        server = AsyncTcpServer("0.0.0.0", port)
        await server.start(partial(Handler, cfg, answer, seen))     # a taken port raises OSError here
        self.server = server
        addr = local_address()
        if not addr:
            logger.warning("assist: no route to the mDNS group, so not advertised; add Winston in Home "
                           "Assistant by host and port {}", port)
            return
        self.service = AsyncServiceInfo(SERVICE, f"{cfg.assistant.name}.{SERVICE}",
                                        addresses=[socket.inet_aton(addr)], port=port)
        self.zc = ZEROCONF()
        await self.zc.async_register_service(self.service, allow_name_change=True)
        logger.info("assist: listening on {} and advertised from {}", port, addr)

    async def stop(self):
        if self.zc:
            await self.zc.async_unregister_service(self.service)
            await self.zc.async_close()
        if self.server:
            await self.server.stop()       # closes the port at once, and any open request
        self.server = self.zc = self.service = None
