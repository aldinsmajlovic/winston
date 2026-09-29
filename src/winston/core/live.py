"""Live feed: what Winston hears, says and does, as it happens. The Talk page and dashboard mode
draw from it.

winstond keeps the recent events and streams them on `GET /api/live`. Channels inside winstond (web
chat) publish directly; channel processes (voice) post to `POST /api/live` through a FeedPoster.
A LiveObserver in a channel's pipeline turns frames into events:

    Pipeline(...), observers=[LiveObserver("voice", poster.put, spoken=True), ...]

Replies stream as `reply` pieces closed by `reply_end`; the stored history keeps each reply and each
tool call as one event, so a page that connects late gets whole lines, not tokens."""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from typing import Callable, Literal

import httpx
from loguru import logger
from pipecat.frames.frames import (BotStartedSpeakingFrame, BotStoppedSpeakingFrame, FunctionCallResultFrame,
                                   FunctionCallsStartedFrame, LLMFullResponseEndFrame, LLMFullResponseStartFrame,
                                   LLMTextFrame, TranscriptionFrame, UserStartedSpeakingFrame,
                                   UserStoppedSpeakingFrame)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pydantic import BaseModel

from winston.config.schema import WinstonConfig
from winston.core.guard import ClaimGuard, result_ok

State = Literal["idle", "listening", "thinking", "speaking"]


class LiveEvent(BaseModel):
    """One thing that happened. Which fields are set depends on `type`:
    state: state. user: text. reply: reply, text (a piece). reply_end: reply.
    tool: call, name, status, label, ms. announcement: text. error: text.
    mic: room, held_by (who has the room's mic; absent when nobody has it, core/rooms.py)."""
    id: str = ""
    ts: float = 0
    channel: str
    type: Literal["state", "user", "reply", "reply_end", "tool", "announcement", "error", "mic"]
    state: State | None = None
    text: str | None = None
    reply: str | None = None
    call: str | None = None
    name: str | None = None
    status: Literal["running", "ok", "error"] | None = None
    label: str | None = None
    ms: int | None = None
    room: str | None = None
    held_by: str | None = None


class Feed:
    """In-process fan-out plus a short history. The history merges reply pieces and tool updates
    into one event each."""

    def __init__(self, keep: int = 400):
        self._history: deque[LiveEvent] = deque(maxlen=keep)
        self._open: dict[str, LiveEvent] = {}          # reply or call id -> its event in the history
        self._state: dict[str, LiveEvent] = {}         # latest state per channel
        self._mic: dict[str, LiveEvent] = {}           # latest mic holder per room
        self._subs: set[asyncio.Queue] = set()

    def publish(self, ev: LiveEvent) -> LiveEvent:
        ev.id = ev.id or uuid.uuid4().hex[:12]
        ev.ts = ev.ts or time.time()
        if ev.type == "state":
            self._state[ev.channel] = ev
        elif ev.type == "mic":
            self._mic[(ev.room or "").casefold()] = ev
        elif ev.type == "reply" and ev.reply in self._open:
            self._open[ev.reply].text = (self._open[ev.reply].text or "") + (ev.text or "")
        elif ev.type == "tool" and ev.call in self._open:
            stored = self._open[ev.call]
            stored.status, stored.label, stored.ms = ev.status, ev.label, ev.ms
        elif ev.type != "reply_end":
            stored = ev.model_copy()
            self._history.append(stored)
            if ev.type in ("reply", "tool"):
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
    "hassturnon": ("Turning on {t}", "Turned on {t}", "Could not turn on {t}"),
    "hassturnoff": ("Turning off {t}", "Turned off {t}", "Could not turn off {t}"),
    "hasslightset": ("Setting {t}", "Set {t}", "Could not set {t}"),
    "getlivecontext": ("Checking the home", "Checked the home", "Could not check the home"),
    "getdatetime": ("Checking the time", "Checked the time", "Could not check the time"),
    "get_sensor_readings": ("Reading sensors", "Read sensors", "Could not read sensors"),
    "web_search": ("Searching the web for {q}", "Searched the web for {q}", "Web search failed"),
    "ask_claude": ("Asking Claude", "Asked Claude", "Could not reach Claude"),
    "remember": ("Remembering {q}", "Remembered {q}", "Could not remember that"),
    "forget": ("Forgetting {q}", "Forgot {q}", "Could not forget that"),
    "send_message": ("Messaging {t}", "Messaged {t}", "Could not message {t}"),
}


def tool_label(name: str, args: dict | None, status: str) -> str:
    """"Turned on Kitchen lights", "Searched the web for population of Sarajevo"."""
    args = args if isinstance(args, dict) else {}
    base = name.split("__")[-1]
    target = args.get("name") or args.get("to") or args.get("area") or "something"
    if not args.get("name") and args.get("area") and args.get("domain"):
        domain = args["domain"][0] if isinstance(args["domain"], list) else args["domain"]
        target = f"{args['area']} {domain}s"
    detail = args.get("query") or args.get("fact") or args.get("question") or ""
    forms = _VERBS.get(base.lower()) or (f"Using {base}", f"Used {base}", f"{base} failed")
    return forms[("running", "ok", "error").index(status)].format(t=target, q=detail).strip()


class LiveObserver(BaseObserver):
    """Turns one pipeline's frames into feed events. `spoken` channels get their idle and speaking
    states from the bot's audio; text channels are idle when the reply ends, no tool is running and
    no guard retry is coming. The reply is what the pipeline's ClaimGuard lets through."""

    def __init__(self, channel: str, sink: Callable[[LiveEvent], object], spoken: bool = False):
        super().__init__()
        self.channel, self._sink, self._spoken = channel, sink, spoken
        self._seen: deque[int] = deque(maxlen=256)     # observers see a frame once per pipeline hop
        self._heard: list[str] = []
        self._reply: str | None = None
        self._calls: dict[str, tuple[float, str, dict]] = {}
        self._called: deque[tuple[str, str]] = deque(maxlen=256)   # call frames go both ways as two frames

    def _emit(self, type_: str, **fields):
        self._sink(LiveEvent(channel=self.channel, type=type_, **fields))

    def _state(self, s: State):
        self._emit("state", state=s)

    def _end_reply(self):
        if self._reply:
            self._emit("reply_end", reply=self._reply)
            self._reply = None

    async def on_push_frame(self, data: FramePushed):
        f = data.frame
        # Reply text and its end count only as the claim guard passes them on, not on their way in:
        # what it holds back is never said, and it may add to a reply before ending it.
        if isinstance(f, (LLMTextFrame, LLMFullResponseEndFrame)) and isinstance(data.destination, ClaimGuard):
            return
        # Speech counts once past a gate that may drop it (core/rooms.py), not on its way in.
        if isinstance(f, TranscriptionFrame) and getattr(data.destination, "may_drop_speech", False):
            return
        if f.id in self._seen:
            return
        self._seen.append(f.id)
        if isinstance(f, UserStartedSpeakingFrame):
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
            if not self._spoken and not self._calls and not getattr(data.source, "retrying", False):
                self._state("idle")
        elif isinstance(f, FunctionCallsStartedFrame):
            self._end_reply()
            for c in f.function_calls:
                if ("call", c.tool_call_id) in self._called:
                    continue
                self._called.append(("call", c.tool_call_id))
                args = c.arguments if isinstance(c.arguments, dict) else {}
                self._calls[c.tool_call_id] = (time.perf_counter(), c.function_name, args)
                self._emit("tool", call=c.tool_call_id, name=c.function_name, status="running",
                           label=tool_label(c.function_name, args, "running"))
        elif isinstance(f, FunctionCallResultFrame):
            if ("result", f.tool_call_id) in self._called:
                return
            self._called.append(("result", f.tool_call_id))
            t0, name, args = self._calls.pop(f.tool_call_id, (time.perf_counter(), f.function_name, {}))
            status = "ok" if result_ok(f.result) and not f.error else "error"
            self._emit("tool", call=f.tool_call_id, name=name, status=status, label=tool_label(name, args, status),
                       ms=round((time.perf_counter() - t0) * 1000))
        elif isinstance(f, BotStartedSpeakingFrame):
            self._state("speaking")
        elif isinstance(f, BotStoppedSpeakingFrame):
            self._state("idle")


class FeedPoster:
    """For channel processes: queues events and posts them to winstond's feed. Drops them when
    winstond is not there; the feed is for watching, not a record (the tool log is the record)."""

    def __init__(self, cfg: WinstonConfig):
        self._url = f"http://{cfg.control.host}:{cfg.control.port}/api/live"
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
