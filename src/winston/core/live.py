"""Live feed: what Winston hears, says and does, as it happens. The Talk page and dashboard mode
draw from it.

winstond keeps the recent events and streams them on `GET /api/live`. Channels inside winstond (web
chat) publish directly; channel processes (voice) post to `POST /api/live` through a FeedPoster.
A LiveObserver in a channel's pipeline turns frames into events:

    Pipeline(...), observers=[LiveObserver("voice", poster.put, spoken=True), ...]

Replies stream as `reply` pieces closed by `reply_end`; the stored history keeps each reply and each
tool call as one event, so a page that connects late gets whole lines, not tokens. While he speaks, a
`level` event says how loud his voice is as it plays, for his mark; levels are streamed, never kept."""
from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections import deque
from collections.abc import Callable
from typing import Literal

import httpx
import numpy as np
from loguru import logger
from pipecat.frames.frames import (BotStartedSpeakingFrame, BotStoppedSpeakingFrame, FunctionCallResultFrame,
                                   FunctionCallsStartedFrame, InterruptionFrame, LLMFullResponseEndFrame,
                                   LLMFullResponseStartFrame, LLMTextFrame, TranscriptionFrame, TTSAudioRawFrame,
                                   UserStartedSpeakingFrame, UserStoppedSpeakingFrame)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pydantic import BaseModel

from winston.config.schema import WinstonConfig
from winston.core.common import Once, key
from winston.core.guard import result_ok
from winston.core.reply import ReplyFilter
from winston.providers.home_assistant import tool_label as ha_tool_label

State = Literal["idle", "listening", "thinking", "speaking"]


class LiveEvent(BaseModel):
    """One thing that happened. Which fields are set depends on `type`:
    state: state, room (when the channel is in one). user: text, room (where it was said, when a room is known). reply: reply, text (a piece). reply_end: reply.
    tool: call, name, status, label, ms, icon (an extension's). announcement: text. error: text.
    mic: room, held_by (who has the room's mic; absent when nobody has it, core/rooms.py).
    confirm: call (the confirmation's id), name (the tool), label (what it would do), status (waiting, then
    ok, error, declined or expired), text (the outcome): a restricted tool waiting for you to confirm it.
    nudge: call (the nudge's id), label (extension and kind), text (its plain line), name (where it went:
    speakers, phone, discord, conversation, silent), links (title and url, if it has any): he spoke up,
    or would have.
    turn: call (the turn's id in the turn record), text (what was heard), room: a spoken turn was kept
    (core/turns.py); the history gives its id to the channel's latest user line, so the page can mark it.
    conversation: room, text (why, in words: "after 30 min of quiet", "his actions changed", "settings were
    saved"): the next line starts a new typed conversation (channels/web.py). One you start is its start_over
    tool line.
    level: room, loudness (0 to 1): how loud his voice is as it plays, every 50 ms while he speaks. Streamed, never
    kept in the history.
    lockdown: text ("on" or "off"), name (who changed it)."""
    id: str = ""
    ts: float = 0
    channel: str
    type: Literal["state", "user", "reply", "reply_end", "tool", "announcement", "error", "mic", "confirm", "nudge",
                  "turn", "conversation", "level", "lockdown"]
    state: State | None = None
    text: str | None = None
    reply: str | None = None
    call: str | None = None
    name: str | None = None
    status: Literal["running", "ok", "error", "waiting", "declined", "expired"] | None = None
    label: str | None = None
    icon: str | None = None
    ms: int | None = None
    room: str | None = None
    held_by: str | None = None
    links: list[dict[str, str]] | None = None
    loudness: float | None = None                   # not `level`: a nudge's own events carry that


class Feed:
    """In-process fan-out plus a short history. The history merges reply pieces and tool updates
    into one event each."""

    def __init__(self, keep: int = 400):
        self._history: deque[LiveEvent] = deque(maxlen=keep)
        self._open: dict[str, LiveEvent] = {}          # reply or call id -> its event in the history
        self._state: dict[str, LiveEvent] = {}         # latest state per channel
        self._mic: dict[str, LiveEvent] = {}           # latest mic holder per room
        self._subs: set[asyncio.Queue] = set()
        self.talked_at = 0.0                            # the last word heard or said, any channel

    def talking(self, secs: float = 30) -> bool:
        """A conversation is running: something was heard, said or done within secs."""
        return time.time() - self.talked_at < secs

    def publish(self, ev: LiveEvent) -> LiveEvent:
        ev.id = ev.id or uuid.uuid4().hex[:12]
        ev.ts = ev.ts or time.time()
        if ev.type in ("user", "reply", "tool") or (ev.type == "state" and ev.state in ("listening", "thinking",
                                                                                          "speaking")):
            self.talked_at = max(self.talked_at, time.time())
        if ev.type == "state":
            self._state[ev.channel] = ev
        elif ev.type == "mic":
            self._mic[key(ev.room or "")] = ev
        elif ev.type == "turn":
            # The turn's id onto the channel's latest user line that has none yet: what was heard is that line.
            for stored in reversed(self._history):
                if stored.type == "user" and stored.channel == ev.channel:
                    if stored.call is None:
                        stored.call = ev.call
                    break
        elif ev.type == "reply" and ev.reply in self._open:
            self._open[ev.reply].text = (self._open[ev.reply].text or "") + (ev.text or "")
        elif ev.type in ("tool", "confirm") and ev.call in self._open:
            stored = self._open[ev.call]
            stored.status, stored.label, stored.ms = ev.status, ev.label, ev.ms
            if ev.text is not None:
                stored.text = ev.text
        elif ev.type not in ("reply_end", "level"):
            stored = ev.model_copy()
            self._history.append(stored)
            if ev.type in ("reply", "tool", "confirm"):
                self._open[ev.reply or ev.call or ""] = stored
            if len(self._open) > 2 * (self._history.maxlen or 0):    # forget ids that left the history
                kept = {id(e) for e in self._history}
                self._open = {k: v for k, v in self._open.items() if id(v) in kept}
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                logger.warning("live feed: dropped an event for a stuck subscriber")
        return ev

    def backlog(self) -> list[LiveEvent]:
        return [*self._history, *self._state.values(), *self._mic.values()]

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subs.discard(q)


# Tool labels: (running, done, failed) verb phrases; {t} is the target, {q} a quoted argument.
_VERBS = {
    "hassturnon": ("Turning on {t}", "Turned on {t}", "Couldn't turn on {t}"),
    "hassturnoff": ("Turning off {t}", "Turned off {t}", "Couldn't turn off {t}"),
    "hasslightset": ("Setting {t}", "Changed {t}", "Couldn't set {t}"),       # "Set" done reads as an order
    "getlivecontext": ("Checking the home", "Checked the home", "Couldn't check the home"),
    "getdatetime": ("Checking the time", "Checked the time", "Couldn't check the time"),
    "remember": ("Remembering {q}", "Remembered {q}", "Couldn't remember that"),
    "forget": ("Forgetting {q}", "Forgot {q}", "Couldn't forget that"),
    "send_message": ("Messaging {t}", "Messaged {t}", "Couldn't message {t}"),
    "mark_mistake": ("Marking a mistake", "Marked a mistake", "Couldn't mark that"),
    "start_over": ("Starting over", "Started a new conversation", "Couldn't start over"),
}
PLURAL = {"switch": "switches"}                 # an area's devices of a domain: "Kitchen switches"


# Extension tools by the name the brain sees: their extension's name and icon and their own title, as winstond
# lists them.
EXTENSION_TOOLS: dict[str, tuple[str, str | None, str]] = {}


def know_extension_tools(tools: list[dict]) -> None:
    for t in tools:
        ext = t.get("title") or t["extension"]
        EXTENSION_TOOLS[t["name"]] = (ext, t.get("icon"), t.get("tool_title") or ext)


def tool_icon(name: str) -> str | None:
    return EXTENSION_TOOLS.get(name, ("", None, ""))[1]


def _failed(label: str) -> str:
    return f"Couldn't {label[:1].lower()}{label[1:]}"


# A done line is in the past tense, as the built-in verbs above are: "Add a reminder" did "Added a reminder".
# ponytail: a fixed list of the verbs titles start with; a title led by any other word stays as it is.
_IRREGULAR = {"read": "read", "do": "did", "go": "went", "get": "got", "find": "found", "send": "sent",
              "make": "made", "take": "took", "run": "ran", "give": "gave", "tell": "told", "say": "said",
              "buy": "bought", "write": "wrote", "begin": "began", "keep": "kept", "bring": "brought",
              "put": "put", "set": "set", "skip": "skipped", "stop": "stopped", "plan": "planned",
              "dim": "dimmed", "log": "logged", "drop": "dropped", "shut": "shut"}
_REGULAR = {
    "add", "ask", "cancel", "check", "list", "play", "start", "pause", "resume", "shuffle", "move", "rate",
    "search", "open", "close", "turn", "change", "create", "delete", "remove", "update", "save", "show",
    "look", "fetch", "follow", "like", "mark", "mute", "unmute", "note", "order", "queue", "record", "remind",
    "rename", "reply", "restart", "schedule", "share", "snooze", "switch", "track", "call", "book", "block",
    "clear", "copy", "edit", "empty", "lock", "unlock", "lower", "raise", "reset", "toggle", "translate",
    "watch", "use", "count", "convert", "compare", "calculate", "summarize", "explain", "describe",
    "announce", "arm", "disarm", "brighten", "connect", "download", "upload", "install", "load", "print",
    "scan", "sync", "test", "clean", "water", "feed", "answer"}


def _past(label: str) -> str:
    verb, _, rest = label.partition(" ")
    low = verb.lower()
    if low in _IRREGULAR:
        past = _IRREGULAR[low]
    elif low in _REGULAR:
        past = low + "d" if low.endswith("e") else low[:-1] + "ied" if low[-1] == "y" and low[-2] not in "aeiou" \
            else low + "ed"
    else:
        return label
    return " ".join([past[:1].upper() + past[1:] if verb[:1].isupper() else past, rest]).rstrip()


def tool_label(name: str, args: dict | None, status: str) -> str:
    """"Turned on Kitchen lights"; an extension's tool by its title and first text argument, in the past tense
    once done, "Played music: Daft Punk", "Couldn't pause music"; another Home Assistant tool by its label and
    target, "Cleaned a room with the vacuum: Kitchen"."""
    args = args if isinstance(args, dict) else {}
    if name in EXTENSION_TOOLS:
        title = EXTENSION_TOOLS[name][2]
        detail = next((v.strip() for v in args.values() if isinstance(v, str) and v.strip()), "")
        title = _past(title) if status == "ok" else title
        return _failed(title) if status == "error" else f"{title}: {detail}" if detail else title
    base = name.split("__")[-1]
    target = args.get("name") or args.get("to") or args.get("area") or ""
    if not args.get("name") and args.get("area") and args.get("domain"):
        domain = args["domain"][0] if isinstance(args["domain"], list) else args["domain"]
        target = f"{args['area']} {PLURAL.get(domain, domain.replace('_', ' ') + 's')}"
    if (forms := _VERBS.get(base.lower())) is None:
        label = ha_tool_label(base)
        if status == "error":
            return _failed(label)
        label = _past(label) if status == "ok" else label
        return f"{label}: {target}" if target else label
    detail = args.get("query") or args.get("fact") or args.get("question") or ""
    return forms[("running", "ok", "error").index(status)].format(t=target or "something", q=detail).strip()


LEVEL_SECS = 0.05                   # how often the level of his voice goes out while he speaks


def loudness(pcm: bytes) -> float:
    """How loud a stretch of 16-bit audio is, 0 to 1: its RMS from -50 dBFS (silence) to -10 dBFS (loud)."""
    a = np.frombuffer(pcm[:len(pcm) // 2 * 2], dtype="<i2").astype(np.float32)
    if not len(a):
        return 0.0
    db = 20 * math.log10(max(float(np.sqrt(np.mean(a * a))) / 32768, 1e-6))
    return min(1.0, max(0.0, (db + 50) / 40))   # ponytail: fixed range; per-voice gain if a voice sits outside it


class LiveObserver(BaseObserver):
    """Turns one pipeline's frames into feed events. `spoken` channels get their idle and speaking
    states from the bot's audio; text channels are idle when the reply ends and no tool is running.
    The reply is what the pipeline's ReplyFilter lets through.

    A spoken channel also sends the level of his voice as it plays. Audio reaches the pipeline's end faster
    than it plays and plays back to back, so each stretch is due once the audio before it has played.
    `plays_after` is for a channel with no transport to say when he speaks (the room boxes): its audio
    plays that long after it arrives, and the observer says speaking when it starts and idle once a reply
    the channel has `finished` has played."""

    def __init__(self, channel: str, sink: Callable[[LiveEvent], object], spoken: bool = False,
                 room: str | None = None, plays_after: float | None = None):
        super().__init__()
        self.channel, self._sink, self._spoken, self.room = channel, sink, spoken, room
        self._first = Once(256)                         # observers see a frame once per pipeline hop
        self._heard: list[str] = []
        self._reply: str | None = None
        self._calls: dict[str, tuple[float, str, dict]] = {}
        self._called = Once(256)                        # call frames go both ways as two frames
        self._plays_after = plays_after
        self._levels: deque[tuple[float, float]] = deque()  # (when it plays, by perf_counter; its level)
        self._played = 0.0                              # when the audio so far will have played
        self._meter: asyncio.Task | None = None
        self._finished = False                          # the channel's reply is all in; idle once it has played
        self._said: State | None = None                 # the last state this observer sent

    def _emit(self, type_: str, **fields):
        self._sink(LiveEvent(channel=self.channel, type=type_, **fields))

    def _state(self, s: State):
        self._said = s
        self._emit("state", state=s, room=self.room)      # the room's state in Home Assistant (core/ha_link.py)

    def _queue_audio(self, f: TTSAudioRawFrame):
        rate = 2 * f.sample_rate * max(1, f.num_channels)  # bytes a second
        start = max(self._played, time.perf_counter() + (self._plays_after or 0.0))
        step = max(2, int(rate * LEVEL_SECS) // 2 * 2)
        for i in range(0, len(f.audio), step):
            self._levels.append((start + i / rate, loudness(f.audio[i:i + step])))
        self._played = start + len(f.audio) / rate
        if self._meter is None or self._meter.done():
            self._meter = asyncio.get_running_loop().create_task(self._run_meter())

    async def _run_meter(self):
        while self._levels:
            await asyncio.sleep(max(0.0, self._levels[0][0] - time.perf_counter()))
            level, now = 0.0, time.perf_counter()
            while self._levels and self._levels[0][0] <= now:
                level = max(level, self._levels.popleft()[1])
            if self._plays_after is not None and self._said != "speaking":
                self._state("speaking")
            self._emit("level", loudness=round(level, 2), room=self.room)
            await asyncio.sleep(LEVEL_SECS)
        if self._finished:
            self.finished()

    def finished(self):
        """The reply is all in (a channel with `plays_after`): idle once it has played."""
        self._finished = True
        if self._levels:
            return
        self._finished = False
        if self._said == "speaking":
            self._state("idle")

    def ahead(self, secs: float):
        """Audio the pipeline does not see (a chime) plays first, for secs."""
        self._played = max(self._played, time.perf_counter() + (self._plays_after or 0.0)) + secs

    def say(self, s: State):
        """A state the channel knows of itself: what is queued will not play after it."""
        self._cut()
        self._state(s)

    def _cut(self):
        """He was cut short: what is queued will not play."""
        self._levels.clear()
        self._played, self._finished = 0.0, False

    def _end_reply(self):
        if self._reply:
            self._emit("reply_end", reply=self._reply)
            self._reply = None

    async def on_push_frame(self, data: FramePushed):
        f = data.frame
        # Reply text and its end count only as the reply filter passes them on, not on their way in:
        # a stray </think> and what it repeats are never said.
        if isinstance(f, (LLMTextFrame, LLMFullResponseEndFrame)) and isinstance(data.destination, ReplyFilter):
            return
        # Speech counts once past a gate that may drop it (core/rooms.py), not on its way in.
        if isinstance(f, TranscriptionFrame) and getattr(data.destination, "may_drop_speech", False):
            return
        if not self._first(f.id):
            return
        if isinstance(f, TTSAudioRawFrame):
            if self._spoken:
                self._queue_audio(f)
        elif isinstance(f, InterruptionFrame):
            self._cut()
        elif isinstance(f, UserStartedSpeakingFrame):
            self._cut()
            self._state("listening")
        elif isinstance(f, UserStoppedSpeakingFrame):
            self._state("thinking")
        elif isinstance(f, TranscriptionFrame) and f.text.strip():
            self._heard.append(f.text.strip())
        elif isinstance(f, LLMFullResponseStartFrame):
            if self._heard:
                self._emit("user", text=" ".join(self._heard))
                self._heard = []
            self._state("thinking")
        elif isinstance(f, LLMTextFrame):
            text = f.text if self._reply else f.text.lstrip()
            if text:
                self._reply = self._reply or uuid.uuid4().hex[:12]
                self._emit("reply", reply=self._reply, text=text)
        elif isinstance(f, LLMFullResponseEndFrame):
            self._end_reply()
            if not self._spoken and not self._calls:
                self._state("idle")
        elif isinstance(f, FunctionCallsStartedFrame):
            self._end_reply()
            for c in f.function_calls:
                if not self._called(("call", c.tool_call_id)):
                    continue
                args = c.arguments if isinstance(c.arguments, dict) else {}
                self._calls[c.tool_call_id] = (time.perf_counter(), c.function_name, args)
                self._emit("tool", call=c.tool_call_id, name=c.function_name, status="running",
                           label=tool_label(c.function_name, args, "running"), icon=tool_icon(c.function_name))
        elif isinstance(f, FunctionCallResultFrame):
            if not self._called(("result", f.tool_call_id)):
                return
            t0, name, args = self._calls.pop(f.tool_call_id, (time.perf_counter(), f.function_name, {}))
            status = "ok" if result_ok(f.result) and not f.error else "error"
            self._emit("tool", call=f.tool_call_id, name=name, status=status, label=tool_label(name, args, status),
                       icon=tool_icon(name), ms=round((time.perf_counter() - t0) * 1000))
        elif isinstance(f, BotStartedSpeakingFrame):
            self._state("speaking")
        elif isinstance(f, BotStoppedSpeakingFrame):
            self._state("idle")


class FeedPoster:
    """For channel processes: queues events and posts them to winstond's feed. Drops them when
    winstond is not there; the feed is for watching, not a record (the tool log is the record)."""

    def __init__(self, cfg: WinstonConfig):
        self._url = cfg.control.url + "/api/live"
        self._q: asyncio.Queue[LiveEvent] = asyncio.Queue(maxsize=1000)
        self._task: asyncio.Task | None = None

    def put(self, ev: LiveEvent):
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())
        try:
            self._q.put_nowait(ev)
        except asyncio.QueueFull:
            pass

    async def _run(self):
        async with httpx.AsyncClient(timeout=3) as c:
            while True:
                batch = [await self._q.get()]
                while not self._q.empty() and len(batch) < 100:
                    batch.append(self._q.get_nowait())
                try:
                    await c.post(self._url, json=[e.model_dump(exclude_none=True) for e in batch])
                except httpx.HTTPError as e:
                    logger.debug(f"live feed: {e}")
