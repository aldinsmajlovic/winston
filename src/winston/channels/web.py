"""Web chat: typing on the Talk page. Runs inside winstond, one conversation for the household and
one per room a page is set to (shared with that room's box, core/rooms.py), with the same brain,
tools and reply filter as every other channel. Everything it says goes to the live feed;
`POST /api/chat` only queues the message. Home Assistant's Assist (channels/assist.py) keeps its
conversations in a second one, named by its channel, with one per Home Assistant conversation id too.

A conversation starts fresh after channels.web.session_idle_mins, and after a config save; the feed
gets a `conversation` event saying why before the message that starts it."""
import asyncio
import time
import uuid
from collections import deque

from loguru import logger

from winston.channels.text import TextSession, error_text, stale
from winston.core.announce import note
from winston.config.schema import WinstonConfig
from winston.core.live import Feed, LiveEvent, LiveObserver
from winston.core.rooms import RoomLink, Rooms, area, key, local_sync


class WebChat:
    def __init__(self, cfg: WinstonConfig, feed: Feed, rooms: Rooms | None = None, channel: str = "web"):
        self.cfg, self.feed, self.rooms, self.channel = cfg, feed, rooms, channel
        # by room key, "#" + a conversation id, or None for the household's
        self.sessions: dict[str | None, TextSession] = {}
        self._why: dict[str | None, str] = {}                   # by the same key: why the next one is new
        self._lock = asyncio.Lock()
        self._recent: deque[tuple[float, str, str]] = deque(maxlen=10)     # (at, note, said), for a new conversation

    @property
    def session(self) -> TextSession | None:
        """The household conversation, the one without a room."""
        return self.sessions.get(None)

    def _key(self, room: str | None, conversation: str | None = None) -> str | None:
        if room and self.rooms:
            return key(room)
        return "#" + conversation if conversation else None

    async def _end_stale(self, k: str | None) -> None:
        """Ends a conversation gone stale (channels/text.py), noting why for the next message."""
        s, mins = self.sessions.get(k), self.cfg.channels.web.session_idle_mins
        if s is not None and await stale(s, mins * 60):
            quiet = time.monotonic() - s.last_used > mins * 60
            self._why[k] = f"after {mins:g} min of quiet" if quiet else "his actions changed"
            await self.sessions.pop(k).close()

    async def current(self, room: str | None = None, conversation: str | None = None) -> TextSession:
        """The room's conversation, else the one for this conversation id, else the household's; started if
        there is none. reply() ends a stale one first."""
        k = self._key(room, conversation)
        in_room = k is not None and not k.startswith("#")
        async with self._lock:
            s = self.sessions.get(k)
            if s is None:
                obs = LiveObserver(self.channel, self.feed.publish, room=room if in_room else None)
                link = RoomLink(room, f"{self.channel}:{uuid.uuid4().hex[:8]}", local_sync(self.rooms)) \
                    if in_room else None
                # The Talk page's brain is the typed one; another channel's brain is its own (core/brain.HEARD).
                brain = "text" if self.channel == "web" else self.channel
                s = self.sessions[k] = await TextSession(self.cfg, channel=self.channel, observers=[obs], room=link,
                                                         area=area(self.cfg, room) if in_room else None,
                                                         brain_channel=brain).start()
                s.notify = self._correction
                window = self.cfg.channels.web.session_idle_mins * 60
                for at, house_note, said in self._recent:
                    if time.monotonic() - at < window:
                        await s.noted(house_note, said)
                logger.info(f"{self.channel} chat: new conversation{f' in {room}' if in_room else ''}")
            return s

    async def reply(self, text: str, room: str | None = None, conversation: str | None = None,
                    notify=None) -> str:
        """His answer, with the feed told as for any typed turn; raises what the session raises. notify, when
        given, is where a state check's correction goes instead of the Talk page alone."""
        k = self._key(room, conversation)
        try:
            async with self._lock:
                await self._end_stale(k)
        except Exception:                                               # noqa: BLE001  (it starts afresh)
            logger.exception(f"{self.channel} chat: ending a stale conversation failed")
        if (why := self._why.pop(k, None)) is not None:
            self.feed.publish(LiveEvent(channel=self.channel, type="conversation", room=room, text=why))
        self.feed.publish(LiveEvent(channel=self.channel, type="user", room=room, text=text))
        self.feed.publish(LiveEvent(channel=self.channel, type="state", state="thinking", room=room))
        s = await self.current(room, conversation)
        if notify is not None:
            s.notify = notify
        return await s.ask(text, timeout=60)

    async def ask(self, text: str, room: str | None = None):
        try:
            await self.reply(text, room)
        except Exception as e:                                          # noqa: BLE001
            logger.exception(f"{self.channel} chat failed")
            self.feed.publish(LiveEvent(channel=self.channel, type="error", text=error_text(e)))
            self.feed.publish(LiveEvent(channel=self.channel, type="state", state="idle", room=room))

    async def _correction(self, text: str):
        """A state check's correction (core/verify.py), shown on the Talk page as a reply of his."""
        ev = self.feed.publish(LiveEvent(channel=self.channel, type="reply", reply=uuid.uuid4().hex[:12], text=text))
        self.feed.publish(LiveEvent(channel=self.channel, type="reply_end", reply=ev.reply))

    async def announced(self, text: str, context: dict):
        """An announcement joins the conversation, so a typed follow-up has its context. Kept for a
        while, so a conversation that starts after it still knows."""
        await self.noted(note(text, context), text)

    async def noted(self, house_note: str, said: str):
        """Something he said on his own (an announcement, a nudge on the speakers) joins the typed
        conversations too, so "remind me tomorrow" typed after it has the nudge."""
        self._recent.append((time.monotonic(), house_note, said))
        for s in list(self.sessions.values()):
            await s.noted(house_note, said)

    async def start_over(self, room: str | None = None):
        """New conversation on the Talk page: a room's ends for every session in it (its typed one too, at
        the next message); the household's typed one closes."""
        if room and self.rooms:
            self.rooms.clear(room)
            return
        async with self._lock:
            s = self.sessions.pop(None, None)
        if s is not None:
            await s.close()

    async def reset(self, cfg: WinstonConfig | None = None):
        """Close the conversations; the next message starts one with the current config and memory."""
        async with self._lock:
            self.cfg = cfg or self.cfg
            self._why.update(dict.fromkeys(self.sessions, "settings were saved"))
            closing, self.sessions = list(self.sessions.values()), {}
        for s in closing:
            await s.close()

    async def close(self):
        await self.reset()
