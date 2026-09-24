"""Web chat: typing on the Talk page. Runs inside winstond, one conversation for the household,
with the same brain, tools and claim guard as every other channel. Everything it says goes to the
live feed; `POST /api/chat` only queues the message.

A conversation starts fresh after channels.web.session_idle_mins, and after a config save."""
import asyncio
import time
import uuid
from collections import deque

from loguru import logger

from winston.channels.text import TextSession
from winston.config.schema import WinstonConfig
from winston.core.live import Feed, LiveEvent, LiveObserver


class WebChat:
    def __init__(self, cfg: WinstonConfig, feed: Feed):
        self.cfg, self.feed = cfg, feed
        self.session: TextSession | None = None
        self._lock = asyncio.Lock()
        self._reaper: asyncio.Task | None = None
        self._recent: deque[tuple[float, str, dict]] = deque(maxlen=10)    # announcements, for a new conversation

    async def current(self) -> TextSession:
        async with self._lock:
            if self.session is None:
                obs = LiveObserver("web", self.feed.publish)
                self.session = await TextSession(self.cfg, channel="web", observers=[obs]).start()
                self.session.notify = self._correction
                window = self.cfg.channels.web.session_idle_mins * 60
                for at, text, context in self._recent:
                    if time.monotonic() - at < window:
                        await self.session.announced(text, context)
                logger.info("web chat: new conversation")
                self._reaper = self._reaper or asyncio.create_task(self._reap())
            return self.session

    async def ask(self, text: str):
        self.feed.publish(LiveEvent(channel="web", type="user", text=text))
        self.feed.publish(LiveEvent(channel="web", type="state", state="thinking"))
        try:
            await (await self.current()).ask(text)
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
        if self.session:
            await self.session.announced(text, context)

    async def reset(self, cfg: WinstonConfig | None = None):
        """Close the conversation; the next message starts one with the current config and memory."""
        async with self._lock:
            self.cfg = cfg or self.cfg
            s, self.session = self.session, None
        if s:
            await s.close()

    async def _reap(self):
        while True:
            await asyncio.sleep(60)
            s = self.session
            if s and not s.busy and time.monotonic() - s.last_used > self.cfg.channels.web.session_idle_mins * 60:
                await self.reset()
                logger.info("web chat: conversation closed after idling")

    async def close(self):
        if self._reaper:
            self._reaper.cancel()
        await self.reset()
