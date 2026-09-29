"""Web chat: typing on the Talk page. Runs inside winstond, one conversation for the household and
one per room a page is set to (shared with that room's box, core/rooms.py), with the same brain,
tools and claim guard as every other channel. Everything it says goes to the live feed;
`POST /api/chat` only queues the message.

A conversation starts fresh after channels.web.session_idle_mins, and after a config save."""
import asyncio
import time
import uuid
from collections import deque

from loguru import logger

from winston.channels.text import TextSession
from winston.config.schema import WinstonConfig
from winston.core.live import Feed, LiveEvent, LiveObserver
from winston.core.rooms import RoomLink, Rooms, area, key, local_sync


class WebChat:
    def __init__(self, cfg: WinstonConfig, feed: Feed, rooms: Rooms | None = None):
        self.cfg, self.feed, self.rooms = cfg, feed, rooms
        self.sessions: dict[str | None, TextSession] = {}       # by room key; None is the household's
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None
        self._recent: deque[tuple[float, str, dict]] = deque(maxlen=10)    # announcements, for a new conversation

    @property
    def session(self) -> TextSession | None:
        """The household conversation, the one without a room."""
        return self.sessions.get(None)

    async def current(self, room: str | None = None) -> TextSession:
        k = key(room) if room and self.rooms else None
        async with self._lock:
            s = self.sessions.get(k)
            if s is None:
                obs = LiveObserver("web", self.feed.publish)
                link = RoomLink(room, f"web:{uuid.uuid4().hex[:8]}", local_sync(self.rooms)) if k else None
                s = self.sessions[k] = await TextSession(self.cfg, channel="web", observers=[obs], room=link,
                                                         area=area(self.cfg, room) if k else None).start()
                s.notify = self._correction
                window = self.cfg.channels.web.session_idle_mins * 60
                for at, text, context in self._recent:
                    if time.monotonic() - at < window:
                        await s.announced(text, context)
                logger.info(f"web chat: new conversation{f' in {room}' if k else ''}")
                self._reaper = self._reaper or asyncio.create_task(self._reap())
            return s

    async def ask(self, text: str, room: str | None = None):
        self.feed.publish(LiveEvent(channel="web", type="user", text=text))
        self.feed.publish(LiveEvent(channel="web", type="state", state="thinking"))
        try:
            await (await self.current(room)).ask(text)
        except Exception as e:                                          # noqa: BLE001
            logger.exception("web chat failed")
            self.feed.publish(LiveEvent(channel="web", type="error", text=f"{type(e).__name__}: {e}"))
            self.feed.publish(LiveEvent(channel="web", type="state", state="idle"))

    async def _correction(self, text: str):
        """A state check's correction (core/verify.py), shown on the Talk page as a reply of his."""
        ev = self.feed.publish(LiveEvent(channel="web", type="reply", reply=uuid.uuid4().hex[:12], text=text))
        self.feed.publish(LiveEvent(channel="web", type="reply_end", reply=ev.reply))

    async def announced(self, text: str, context: dict):
        """An announcement joins the conversation, so a typed follow-up has its context. Kept for a
        while, so a conversation that starts after it still knows."""
        self._recent.append((time.monotonic(), text, context))
        for s in list(self.sessions.values()):
            await s.announced(text, context)

    async def reset(self, cfg: WinstonConfig | None = None):
        """Close the conversations; the next message starts one with the current config and memory."""
        async with self._lock:
            self.cfg = cfg or self.cfg
            closing, self.sessions = list(self.sessions.values()), {}
        for s in closing:
            await s.close()

    async def _reap(self):
        while True:
            await asyncio.sleep(60)
            window = self.cfg.channels.web.session_idle_mins * 60
            async with self._lock:
                idle = [k for k, s in self.sessions.items() if not s.busy and time.monotonic() - s.last_used > window]
                closing = [self.sessions.pop(k) for k in idle]
            for s in closing:
                await s.close()
                logger.info("web chat: conversation closed after idling")

    async def close(self):
        if self._reaper:
            self._reaper.cancel()
        await self.reset()
