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
import asyncio
import errno
import os
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial

import httpx
from loguru import logger
from wyoming.asr import Transcript
from wyoming.event import Event
from wyoming.handle import Handled, NotHandled
from wyoming.info import Attribution, Describe, HandleModel, HandleProgram, Info
from wyoming.server import AsyncEventHandler, AsyncTcpServer
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

import winston
from winston.channels.text import UNREACHABLE, error_text
from winston.channels.web import WebChat
from winston.config.memory import OutOfMemory
from winston.config.schema import WinstonConfig
from winston.core import directory, lines
from winston.core.announce import Hub, nudge_note
from winston.core.live import Feed, LiveEvent
from winston.core.rooms import Rooms, room_for_area

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
    area: str | None = None         # the device's area as Home Assistant names it, when it sends one (the integration)

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
            await self.write_event(NotHandled(text=await lines.say(self.cfg, str(e), wait=1)).event())
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


NO_BRAIN = "Sorry, I could not reach my brain."
NO_MEMORY = "Sorry, my brain ran out of memory."
FAILED = "Sorry, something went wrong while I answered."
WORDING_SECS = 20       # a nudge not worded by then is said as its plain line, as on a box
GONE = ("unavailable", "unknown")


class Assist:
    """Answers Home Assistant's requests in winstond: the listener switched by channels.assist, the
    conversations in a WebChat of their own, and the room each request came from. While it listens, or
    Winston's integration is on (api/ha_link.py answers through it), it is the hub's `outside`: Home
    Assistant's satellites speak announcements, nudges and corrections in rooms with no Winston speaker
    connected (core/announce.Hub)."""

    def __init__(self, cfg: WinstonConfig, feed: Feed, rooms: Rooms, hub: Hub,
                 ha: Callable[[], httpx.AsyncClient]):
        self.cfg, self.feed, self.hub, self.ha = cfg, feed, hub, ha
        self.chat = WebChat(cfg, feed, rooms, channel="assist")
        self.listener: Listener | None = None
        self._as: tuple[int, str] | None = None       # (port, name) it listens and is advertised as
        self.last_seen: float | None = None
        self.error: str | None = None
        self._snap = None                             # the last read of Home Assistant (directory.home)
        self._read_at = -directory.READ_SECS
        self._tasks: set[asyncio.Task] = set()

    @property
    def listening(self) -> bool:
        return self.listener is not None

    def _seen(self):
        self.last_seen = time.time()

    async def apply(self, cfg: WinstonConfig) -> None:
        """Listen, move or stop to match channels.assist. A port that cannot be opened is kept in error and
        logged, never raised, so the save that switched it on still succeeds; the next save tries again."""
        self.cfg, a = cfg, cfg.channels.assist
        want = (a.port, cfg.assistant.name) if a.enabled else None
        if self.listener and self._as != want:
            await self.listener.stop()
            self.listener, self._as = None, None
            logger.info("assist: stopped listening")
        self.error = None
        if want and not self.listener:
            listener = Listener()
            try:
                await listener.start(cfg, a.port, self.answer, self._seen)
                self.listener, self._as = listener, want
                self._spawn(lines.warm(cfg, (NO_BRAIN, NO_MEMORY, FAILED)))    # kept while the brain answers
            except OSError as e:
                self.error = (f"Port {a.port} is in use by another program." if e.errno == errno.EADDRINUSE
                              else f"Port {a.port} could not be opened: {e.strerror or e}.")
                logger.warning(f"assist: {self.error}")
        # Home Assistant's satellites speak for him while either way of talking to him there is on.
        speaks = self.listener is not None or cfg.channels.ha_integration.enabled
        self.hub.outside = self if speaks else None
        if speaks:
            self._read_at = -directory.READ_SECS
            self.rooms()                              # reads Home Assistant in the background
        await self.chat.reset(cfg)

    async def where(self, req: Request) -> str | None:
        """The room the request came from: its device's area in Home Assistant, else its satellite's, as a
        room (core/rooms.room_for_area). None without Home Assistant, its registry, or an area. An area the
        request names itself (the integration reads it in Home Assistant) needs none of that."""
        if req.area:
            return room_for_area(self.cfg, req.area)
        snap = self._snap = await directory.home(self.cfg)
        self._read_at = time.monotonic()
        if snap is None or snap.registry is None:
            return None
        aid = (snap.devices.get(req.device_id or "") or {}).get("area_id")
        if not aid and req.satellite_id:
            aid, _ = snap.area(req.satellite_id)
        name = snap.areas.get(aid) if aid else None
        return room_for_area(self.cfg, name) if name else None

    async def answer(self, req: Request, on_text: Callable[[str], None] | None = None) -> tuple[str, bool]:
        """His reply and whether it asks something. In the room the request came from, else in one
        conversation per Home Assistant conversation id. Never silent: a failure is Unanswered. on_text gets
        each piece of the reply as it comes (the integration streams it)."""
        room = None
        try:
            room = await self.where(req)
        except Exception:                                               # noqa: BLE001  (answered with no room)
            logger.exception("assist: the request's room not found")
        conv = None if room else req.conversation_id
        try:
            text = await self.chat.reply(req.text, room, conv, notify=self._notify(req), on_text=on_text)
        except Exception as e:
            logger.exception("assist: answering failed")
            said = error_text(e)
            self.feed.publish(LiveEvent(channel="assist", type="error", text=said))
            self.feed.publish(LiveEvent(channel="assist", type="state", state="idle", room=room))
            raise Unanswered(NO_MEMORY if isinstance(e, OutOfMemory) else NO_BRAIN if said == UNREACHABLE
                             else FAILED) from e
        s = self.chat.sessions.get(self.chat._key(room, conv))
        llm = s.brain.llm if s else None
        quiet = bool(getattr(llm, "ended", False))      # a scene that speaks for itself (quiet_after)
        if quiet:
            llm.ended = False
        if not text and not quiet:
            logger.warning("assist: no reply within the time limit")
            self.feed.publish(LiveEvent(channel="assist", type="error", text="He did not answer in time."))
            self.feed.publish(LiveEvent(channel="assist", type="state", state="idle", room=room))
            raise Unanswered(FAILED)
        return text, asks(text)

    def _notify(self, req: Request) -> Callable[[str], Awaitable[None]]:
        """Where a state check's correction goes, after the reply: the Talk page, and the satellite the
        request came from (a typed request from the app has none, so it is shown only)."""
        async def notify(text: str):
            await self.chat._correction(text)
            if req.satellite_id:
                self.say([req.satellite_id], text)
        return notify

    def _spawn(self, coro):
        t = asyncio.create_task(coro); self._tasks.add(t); t.add_done_callback(self._tasks.discard)

    async def _read(self):
        try:
            self._snap = await directory.home(self.cfg)
        finally:
            self._read_at = time.monotonic()

    def rooms(self) -> dict[str, list[str]]:
        """Home Assistant's satellites by room: each assist_satellite with an area that is not unavailable (an
        old box's entry left enabled), from the last read of Home Assistant; a read older than
        directory.READ_SECS starts a new one in the background. None without Home Assistant connected."""
        ha = self.cfg.providers.ha
        if ha is None or not os.environ.get(ha.token_env):
            return {}
        if time.monotonic() - self._read_at > directory.READ_SECS:
            self._read_at = time.monotonic()          # one read at a time
            self._spawn(self._read())
        snap, out = self._snap, {}
        if snap is None or snap.registry is None:
            return out
        for eid, st in snap.states.items():
            if eid.startswith("assist_satellite.") and st.get("state") not in GONE:
                _, area_name = snap.area(eid)
                if area_name:
                    out.setdefault(room_for_area(self.cfg, area_name), []).append(eid)
        return out

    def say(self, ids: list[str], text: str) -> None:
        """Spoken on these satellites in Home Assistant's voice (assist_satellite.announce); a failure is logged."""
        async def run():
            try:
                async with self.ha() as c:
                    r = await c.post("/api/services/assist_satellite/announce",
                                     json={"entity_id": ids, "message": text})
                if r.status_code >= 300:
                    logger.warning(f"assist: announce on {ids} answered {r.status_code}: {r.text[:200]}")
            except Exception as e:                                      # noqa: BLE001
                logger.warning(f"assist: announce on {ids} failed: {e}")
        self._spawn(run())

    def nudge(self, ids: list[str], ev: dict, room: str) -> None:
        """A nudge said on the room's satellites: worded by the room's conversation first, as a box does, else
        its plain line."""
        async def run():
            worded = ""
            try:
                s = await self.chat.current(room)
                can = any(t.name == "follow_up" for t in s.brain.tools.standard_tools)
                worded = await asyncio.wait_for(s.ask(nudge_note(ev, can), timeout=WORDING_SECS), WORDING_SECS + 5)
            except Exception as e:                                      # noqa: BLE001
                logger.warning(f"assist: nudge {ev.get('id')} not worded ({e!r}); the plain line")
            self.say(ids, worded or ev["say"])
        self._spawn(run())

    async def close(self):
        self.hub.outside = None
        if self.listener:
            await self.listener.stop()
        self.listener, self._as = None, None
        await self.chat.close()
