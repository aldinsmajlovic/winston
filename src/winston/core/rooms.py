"""Rooms: one conversation and one microphone per room.

A room is a name shared by satellite boxes (the keys of `channels.satellite.devices`) and browser
clients (`/dashboard?room=main room`, the Talk page's room picker). A client without a room works as
before. Names match without regard to case.

winstond keeps each room's conversation (`Rooms`). Every session in a room links its own context to
it with a `RoomLink`: before the LLM runs, the link takes in what the other sessions in the room said
since (inserted before the newest user message) and hands over its own new messages; after each reply
it hands over the reply. So a request started at the box can be continued at the screen. A room's
conversation starts fresh after `channels.satellite.session_idle_mins` without a new message.

The microphone: a box that wakes holds its room's mic with a lease, renewed while its turn runs and
kept for the rest of its reply's playback (longer when it asked a question and listens again). A
browser voice session in that room hears silence meanwhile (`MicGate`), so it neither answers the
wake word said to the box nor takes his voice from the box's speaker for a turn.

    link = RoomLink("main room", "satellite:main room", http_sync(cfg))
    Pipeline([agg.user(), Clock(label), link.before, llm, ..., agg.assistant(), link.after])"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable
from urllib.parse import quote

import httpx
import numpy as np
from loguru import logger
from pipecat.frames.frames import (Frame, InputAudioRawFrame, InterimTranscriptionFrame, LLMContextFrame,
                                   TranscriptionFrame)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pydantic import BaseModel, Field

from winston.config.schema import WinstonConfig

SHARED_ROLES = ("user", "assistant", "tool")
MAX_HOLD_SECS = 120.0


def key(room: str) -> str:
    return " ".join(room.split()).casefold()


def area(cfg: WinstonConfig, room: str) -> str:
    """The Home Assistant area a room means: its box's `area`, else the room's own name."""
    for name, dev in cfg.channels.satellite.devices.items():
        if key(name) == key(room):
            return dev.area or name
    return room.strip()


def configured(cfg: WinstonConfig) -> list[str]:
    """Rooms named in config: the satellite boxes."""
    return list(cfg.channels.satellite.devices)


DEVICE_AREAS_SECS = 600.0
_device_areas: dict[str, str] = {}
_device_areas_at = -DEVICE_AREAS_SECS


async def device_areas(cfg: WinstonConfig) -> dict[str, str]:
    """Every name an exposed device answers to (its Home Assistant name, the config name, the aliases), folded,
    mapped to the Home Assistant area it is in. Read from Home Assistant's registry at most every ten
    minutes; empty when it cannot be read (no admin token, Home Assistant away)."""
    global _device_areas, _device_areas_at
    ha = cfg.providers.ha
    if ha is None or time.monotonic() - _device_areas_at < DEVICE_AREAS_SECS:
        return _device_areas
    _device_areas_at = time.monotonic()
    from winston.providers.home_assistant import HomeAssistant
    try:
        snap = await HomeAssistant(cfg).snapshot(list(ha.expose))
    except Exception as e:                                                  # noqa: BLE001
        logger.warning(f"device areas not read: {e}")
        return _device_areas
    found = {}
    for eid, ent in ha.expose.items():
        if where := snap.area(eid)[1]:
            for n in (snap.display_name(eid), ent.name, *ent.aliases, *snap.aliases(eid)):
                if n:
                    found[key(n)] = where
    _device_areas = found
    return found


def scoped_by_name(args: dict, areas: dict[str, str]) -> dict:
    """A home action's arguments without the area when they name a device that is in another area. The
    room in the time stamp made the model add it: "turn on the window lamps", said in the Main Room, went
    out as name "window lamps" with area "Main Room" (the lamps are in the Living Room), which Home
    Assistant refuses. Measured 2026-09-27: 0 of 3 right, then 3 of 4 once the stamp's rule said to
    leave the room out for a named device."""
    name, where = args.get("name"), args.get("area")
    if not (isinstance(name, str) and isinstance(where, str)):
        return args
    own = areas.get(key(name))
    if own is None or key(own) == key(where):
        return args
    return {k: v for k, v in args.items() if k not in ("area", "floor")}


class SyncRequest(BaseModel):
    origin: str = Field(description="The session handing over messages; its own come back to it never.")
    epoch: str | None = Field(None, description="The room conversation the session last saw; None at its start.")
    after: int = Field(0, ge=0, description="How many of the room's messages the session has taken in.")
    messages: list[dict[str, Any]] = Field(default_factory=list, description="The session's new messages, "
                                                                            "OpenAI chat format.")
    pull: bool = Field(True, description="False: only hand over (after a reply); nothing comes back.")


class SyncResult(BaseModel):
    epoch: str
    seq: int = Field(description="The room's message count after this call.")
    reset: bool = Field(description="The session's epoch is not the room's: its context is from an older "
                                    "conversation, and `others` is the whole of this one.")
    others: list[dict[str, Any]] = Field(default_factory=list)
    seqs: list[int] = Field(default_factory=list, description="Each of `others`' place in the room's messages.")


class MicHold(BaseModel):
    by: str = Field(description="Who holds the mic, e.g. \"box\".")
    secs: float = Field(ge=0, le=MAX_HOLD_SECS, description="How long from now; 0 lets go.")


class RoomState(BaseModel):
    name: str
    box: bool = Field(description="A satellite box is set up for this room.")
    area: str = Field(description="The Home Assistant area \"the lights\" means here.")
    held_by: str | None = Field(None, description="Who has the room's mic now; None: nobody.")
    screens: int = Field(0, description="Browser voice sessions in the room now (they follow its mic).")
    messages: int = 0


@dataclass
class Room:
    name: str
    epoch: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    messages: list[tuple[str, dict]] = field(default_factory=list)      # (origin, message)
    last_used: float = field(default_factory=time.monotonic)
    held_by: str | None = None
    held_until: float = 0.0


class Rooms:
    """winstond's side: each room's conversation and who holds its mic."""

    def __init__(self, cfg: WinstonConfig, on_mic: Callable[[str, str | None], None] | None = None):
        self.cfg, self.on_mic = cfg, on_mic
        self._rooms: dict[str, Room] = {}
        self._subs: dict[str, set[asyncio.Queue]] = {}

    @property
    def idle_secs(self) -> float:
        return self.cfg.channels.satellite.session_idle_mins * 60

    def room(self, name: str) -> Room:
        k = key(name)
        r = self._rooms.get(k)
        if r is None:
            r = self._rooms[k] = Room(name=name.strip())
        elif r.messages and time.monotonic() - r.last_used > self.idle_secs:
            logger.info(f"{r.name}: room conversation closed after idling")
            r.epoch, r.messages = uuid.uuid4().hex[:12], []
        return r

    def states(self) -> list[RoomState]:
        """The boxes' rooms and any room a browser session is in now. A room nobody uses any more
        (a test's, a name typed once) is forgotten once its conversation has idled out."""
        names = {key(n): n for n in configured(self.cfg)}
        now = time.monotonic()
        for k, r in list(self._rooms.items()):
            if self._subs.get(k):
                names.setdefault(k, r.name)
            elif k not in names and not self.holder(r.name) and now - r.last_used > self.idle_secs:
                del self._rooms[k]
        out = []
        for k, n in names.items():
            r = self._rooms.get(k)
            out.append(RoomState(name=n, box=k in {key(b) for b in configured(self.cfg)}, area=area(self.cfg, n),
                                 held_by=self.holder(n), screens=len(self._subs.get(k, ())),
                                 messages=len(r.messages) if r else 0))
        return out

    def sync(self, name: str, req: SyncRequest) -> SyncResult:
        r = self.room(name)
        reset = req.epoch != r.epoch
        others: list[dict] = []
        seqs: list[int] = []
        if req.pull:
            start = 0 if reset else min(req.after, len(r.messages))
            seqs = [i for i in range(start, len(r.messages)) if r.messages[i][0] != req.origin]
            others = [r.messages[i][1] for i in seqs]
        if req.messages and (req.pull or not reset):        # a stale hand-over waits for the next full sync
            r.messages.extend((req.origin, m) for m in req.messages)
            r.last_used = time.monotonic()
        return SyncResult(epoch=r.epoch, seq=len(r.messages), reset=reset, others=others, seqs=seqs)

    def recent(self, secs: float = 1800) -> str | None:
        """The room last talked in, within secs: where you probably are."""
        now = time.monotonic()
        best = max((r for r in self._rooms.values() if r.messages and now - r.last_used < secs),
                   key=lambda r: r.last_used, default=None)
        return best.name if best else None

    def holder(self, name: str) -> str | None:
        r = self._rooms.get(key(name))
        return r.held_by if r and r.held_by and time.monotonic() < r.held_until else None

    def hold(self, name: str, by: str, secs: float):
        """by holds the mic for secs from now (renewing its lease); 0 lets go if by holds it."""
        r = self.room(name)
        before = self.holder(name)
        if secs <= 0:
            if r.held_by == by:
                r.held_by, r.held_until = None, 0.0
        else:
            r.held_by, r.held_until = by, time.monotonic() + secs
            until = r.held_until
            asyncio.get_running_loop().call_later(secs + 0.01, self._expire, name, until)
        if self.holder(name) != before:
            self._publish(r)

    def _expire(self, name: str, until: float):
        r = self._rooms.get(key(name))
        if r and r.held_by and r.held_until == until:
            r.held_by, r.held_until = None, 0.0
            self._publish(r)

    def _publish(self, r: Room):
        held = self.holder(r.name)
        logger.info(f"{r.name}: mic {'held by ' + held if held else 'free'}")
        for q in list(self._subs.get(key(r.name), ())):
            q.put_nowait({"room": r.name, "held_by": held})
        if self.on_mic:
            self.on_mic(r.name, held)

    def subscribe(self, name: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(key(name), set()).add(q)
        q.put_nowait({"room": self.room(name).name, "held_by": self.holder(name)})
        return q

    def unsubscribe(self, name: str, q: asyncio.Queue):
        self._subs.get(key(name), set()).discard(q)


Sync = Callable[[str, SyncRequest], Awaitable[SyncResult | None]]


def http_sync(cfg: WinstonConfig) -> Sync:
    """For channel processes: sync through winstond. Returns None when winstond is not there, and the
    session carries on with its own context."""
    base = f"http://{cfg.control.host}:{cfg.control.port}/api/rooms"
    client = httpx.AsyncClient(timeout=1.5)

    async def call(room: str, req: SyncRequest) -> SyncResult | None:
        try:
            r = await client.post(f"{base}/{quote(room, safe='')}/sync", content=req.model_dump_json(),
                                  headers={"content-type": "application/json"})
            r.raise_for_status()
            return SyncResult.model_validate(r.json())
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(f"{room}: room conversation not reachable ({type(e).__name__}); going on alone")
            return None
    return call


def local_sync(rooms: Rooms) -> Sync:
    """For sessions inside winstond (web chat)."""
    async def call(room: str, req: SyncRequest) -> SyncResult:
        return rooms.sync(room, req)
    return call


def _shared(m: Any) -> bool:
    if not isinstance(m, dict) or m.get("role") not in SHARED_ROLES:
        return False
    try:
        json.dumps(m)
        return True
    except (TypeError, ValueError):
        return False


def _note(m: dict) -> bool:
    """An announcement or check note every session in the room adds for itself."""
    c = m.get("content")
    return m.get("role") == "user" and isinstance(c, str) and c.startswith("[") and "not said by the user" in c


class RoomLink:
    """One session's link to its room's conversation: `before` goes between the Clock and the LLM,
    `after` behind the assistant aggregator."""

    def __init__(self, room: str, origin: str, sync: Sync):
        self.room, self.origin, self._sync = room, origin, sync
        self.epoch: str | None = None
        self.taken = 0          # room messages taken in so far
        self.synced = 0         # messages at the front of this context that the room has
        self._lock = asyncio.Lock()
        self.before = _Hook(self, pull=True)
        self.after = _Hook(self, pull=False)

    async def sync(self, context: LLMContext, pull: bool):
        async with self._lock:
            msgs = list(context.get_messages())
            self.synced = min(self.synced, len(msgs))
            local = msgs[self.synced:]
            res = await self._sync(self.room, SyncRequest(origin=self.origin, epoch=self.epoch, after=self.taken,
                                                          messages=[m for m in local if _shared(m)], pull=pull))
            if res is None:
                return
            if not pull:
                if not res.reset:
                    self.synced = len(msgs)
                return
            # A question from another session that has no answer yet waits: its reply is still coming, or
            # that session closed before saying it. Taken in now, the model answers it instead of ours.
            ready = len(res.others)
            while ready and res.others[ready - 1].get("role") == "user":
                ready -= 1
            taken = res.seqs[ready] if ready < len(res.others) and len(res.seqs) == len(res.others) else res.seq
            seen = {m["content"] for m in msgs if isinstance(m, dict) and _note(m)}
            others = [m for m in res.others[:ready] if not (_note(m) and m["content"] in seen)]
            if res.reset and self.epoch is not None:
                logger.info(f"{self.room}: new room conversation")
            merged = (others if res.reset else msgs[:self.synced] + others) + local
            if others or res.reset:
                context.set_messages(merged)
            if others:
                logger.info(f"{self.room}: {len(others)} message(s) from the room")
            self.epoch, self.taken, self.synced = res.epoch, taken, len(merged)


class _Hook(FrameProcessor):
    def __init__(self, link: RoomLink, pull: bool):
        super().__init__()
        self._link, self._pull = link, pull

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            await self._link.sync(frame.context, self._pull)
        await self.push_frame(frame, direction)


def plain_words(text: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in text.casefold()).split())


def wake_phrases(cfg: WinstonConfig) -> set[str]:
    """What a box in the house wakes on, as words: "okay winston" from okay_winston, and the usual ways
    of calling him by name."""
    name = plain_words(cfg.assistant.name)
    phrases = {f"{w} {name}" for w in ("okay", "ok", "hey", "hi")}
    for dev in cfg.channels.satellite.devices.values():
        phrases |= {plain_words(w.replace("_", " ")) for w in (dev.wake_word or "").split(",") if w.strip()}
    return phrases


_DISMISSALS = {plain_words(p) for p in (
    "stop", "stop it", "stop that", "stop talking", "stop listening", "that's all", "that is all", "that's it",
    "that will be all", "that'll be all", "never mind", "nevermind", "cancel", "forget it", "be quiet", "quiet",
    "shut up", "go to sleep", "nothing", "not you", "not now", "goodbye", "bye", "i wasn't talking to you",
    "wasn't talking to you", "not talking to you")}
_LEAD = {"okay", "ok", "yeah", "yes", "no", "oh", "alright", "please", "hey", "just", "now"}
_TAIL = ("thank you", "thanks", "please", "sir", "now")


def dismissal(text: str, name: str = "") -> bool:
    """Whether what was heard ends the conversation instead of asking something: its last sentence is a
    dismissal on its own ("Stop.", "No, that's all, thanks.", "Yeah we're taking it. Stop."). On the box,
    "Stop" in the listening window after a reply got "Understood, sir." and another listening window, so
    a party in the room kept him going. "No" and "thank you" alone are answers, not dismissals; after a
    dismissal they are skipped (the STT writes "That's all. Thanks.")."""
    name = plain_words(name)
    for sentence in reversed(re.split(r"(?<=[.!?])\s+", text.strip())):
        words = plain_words(sentence).split()
        while words and (words[0] in _LEAD or words[0] == name):
            words = words[1:]
        phrase = " ".join(words)
        changed = True
        while changed and phrase:
            changed = False
            for t in (*_TAIL, name):
                if t and (phrase == t or phrase.endswith(" " + t)):
                    phrase, changed = phrase[: -len(t)].rstrip(), True
        if phrase:
            return phrase in _DISMISSALS
    return False


class MicGate:
    """A browser voice session in a room whose box has the mic: `audio` (behind the transport's input)
    turns the mic into silence, so the VAD sees the speech end; `text` (behind the STT) drops what was
    heard before the gate closed. `closed` is set from the room's mic events.

    A wake word said on its own is dropped even while the gate is open: people pause after it for the
    box, so the browser's VAD often ends that bit before the box's hold arrives (in the headless test
    "Okay, Winston." became a turn 0.1 s ahead of it). It holds no request, and it was said to a box."""

    def __init__(self, wake: set[str] | None = None):
        self.closed = False
        self.wake = wake or set()
        self.audio = _Silencer(self)
        self.text = _Deafener(self)

    def set(self, held_by: str | None) -> bool:
        """Returns True when this changed the gate."""
        was, self.closed = self.closed, bool(held_by)
        return was != self.closed


class _Silencer(FrameProcessor):
    def __init__(self, gate: MicGate):
        super().__init__(); self._gate = gate

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if self._gate.closed and isinstance(frame, InputAudioRawFrame):
            frame.audio = np.zeros(len(frame.audio), np.uint8).tobytes()
        await self.push_frame(frame, direction)


class _Deafener(FrameProcessor):
    may_drop_speech = True          # the live feed counts what it lets through, not what reaches it

    def __init__(self, gate: MicGate):
        super().__init__(); self._gate = gate

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
            if self._gate.closed:
                logger.info(f"mic paused for the box, not a turn: {frame.text!r}")
                return
            if plain_words(frame.text) in self._gate.wake:
                logger.info(f"a wake word on its own is for the box, not a turn: {frame.text!r}")
                return
        await self.push_frame(frame, direction)


async def mic_events(cfg: WinstonConfig, room: str) -> AsyncIterator[dict]:
    """The room's mic holder from winstond ({room, held_by}), the current one first, reconnecting for
    as long as the caller iterates."""
    url = f"http://{cfg.control.host}:{cfg.control.port}/api/rooms/{quote(room, safe='')}/mic"
    delay = 1.0
    while True:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5, read=None)) as c:
                async with c.stream("GET", url) as r:
                    r.raise_for_status()
                    delay = 1.0
                    async for line in r.aiter_lines():
                        if line.startswith("data: "):
                            yield json.loads(line[6:])
        except (httpx.HTTPError, ValueError) as e:
            logger.debug(f"{room}: mic stream: {e}; retrying in {delay:.0f} s")
            yield {"room": room, "held_by": None}          # never stay deaf because winstond went away
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)


class MicLease:
    """For a box: hold its room's mic through winstond. Calls never wait on the box's turn."""

    def __init__(self, cfg: WinstonConfig, room: str, by: str = "box"):
        self._url = f"http://{cfg.control.host}:{cfg.control.port}/api/rooms/{quote(room, safe='')}/mic"
        self.room, self.by = room, by
        self._client = httpx.AsyncClient(timeout=1.5)
        self._last: asyncio.Task | None = None

    def hold(self, secs: float):
        prev = self._last

        async def send():
            if prev:                                # in order: a release must not overtake a hold
                await asyncio.gather(prev, return_exceptions=True)
            try:
                await self._client.post(self._url, json={"by": self.by, "secs": min(secs, MAX_HOLD_SECS)})
            except httpx.HTTPError as e:
                logger.debug(f"{self.room}: mic hold: {e}")
        self._last = asyncio.get_running_loop().create_task(send())
