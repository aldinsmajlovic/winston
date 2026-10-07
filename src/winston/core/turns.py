"""The turn record: every spoken turn kept with its audio, so his mistakes can be found, marked and
used to test the next change (docs/understanding.md, part 1). Off unless logging.turns.enabled.

    <data>/turns/2026-10-07/satellite.jsonl   one JSON line per turn, a file per channel process
    <data>/turns/2026-10-07/<id>.flac         the audio the speech engine heard, as it heard it

A line holds: id, ts (when the speech ended), channel, room, start (wake, follow_up or button on a box,
speech in a browser), secs of audio, heard, raw (what the engine said before the hint-word fix, only when
it differs), engine, stt_ms, first_token_ms and first_audio_ms (from the end of speech), tools (name,
args, ok, result), said (what was spoken, fillers and corrections included), outcome (answered: he
spoke or acted; silent; dismissed; nothing_heard) and audio (the file's name).

A box's turn is begun and written by the satellite channel around one request. A browser's turns are
cut from the pipeline's frames by SpokenTurns: a turn starts with the first words past the room's mic
gate and is written when the next one starts, the session ends, or his reply is over.

Days older than keep_days are removed when a channel starts and when the day turns."""
import asyncio
import json
import shutil
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import soundfile as sf
from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallResultFrame,
    FunctionCallsStartedFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSTextFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from winston.config import resolve
from winston.config.schema import WinstonConfig
from winston.core.guard import result_ok
from winston.core.hints import Heard

SETTLE_SECS = 1.5          # a browser reply is over once it has been done this long (as channels/text.py)
RESULT_CHARS = 1000        # a tool result is cut to this many characters in the record


@dataclass
class Turn:
    """One spoken turn, filled in as it happens and written once it is over."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: float = field(default_factory=time.time)            # when the speech ended
    channel: str = ""
    room: str | None = None
    start: str = "speech"
    heard: str = ""
    engine: str | None = None
    stt_ms: int | None = None
    first_token_ms: int | None = None
    first_audio_ms: int | None = None
    tools: list[dict] = field(default_factory=list)
    spoken: list[str] = field(default_factory=list)         # what the TTS said, piece by piece
    outcome: str | None = None                              # set by the channel, else from what happened
    raws: list[str] = field(default_factory=list)
    audio: list[np.ndarray] = field(default_factory=list, repr=False)     # int16 pieces, one file when written
    rate: int = 16000

    def add(self, text: str, heard: Heard | None = None):
        """One piece of what was said; a browser turn can be several, split by pauses."""
        self.heard = f"{self.heard} {text}".strip()
        if heard is None:
            return
        self.raws.append(heard.raw)
        self.engine = heard.engine
        if heard.audio is not None and len(heard.audio):
            if self.audio and heard.rate != self.rate:
                logger.warning(f"turn {self.id}: audio at {heard.rate} Hz after {self.rate} Hz; not kept")
                return
            self.audio.append(heard.audio)
            self.rate = heard.rate or self.rate

    def ms_since_end(self) -> int:
        return round((time.time() - self.ts) * 1000)

    @property
    def said(self) -> str:
        return " ".join(s.strip() for s in self.spoken if s.strip())

    def line(self) -> dict:
        raw = " ".join(r for r in self.raws if r).strip()
        secs = sum(len(a) for a in self.audio) / self.rate if self.audio else 0.0
        outcome = self.outcome or ("answered" if self.said or self.tools else "silent")
        return {"id": self.id, "ts": round(self.ts, 3), "channel": self.channel, "room": self.room,
                "start": self.start, "secs": round(secs, 2), "heard": self.heard,
                **({"raw": raw} if raw and raw != self.heard else {}),
                "engine": self.engine, "stt_ms": self.stt_ms, "first_token_ms": self.first_token_ms,
                "first_audio_ms": self.first_audio_ms, "tools": self.tools, "said": self.said, "outcome": outcome}


class TurnLog:
    """Writes one channel process's turns, off the event loop, never raising into a turn."""

    def __init__(self, cfg: WinstonConfig, channel: str):
        t = cfg.logging.turns
        self.enabled, self.keep_days, self.root = t.enabled, t.keep_days, resolve(t.dir)
        self.channel = channel
        self._day: str | None = None
        self._lock = threading.Lock()                       # a satellite process writes for several boxes
        self._writing: set[asyncio.Task] = set()
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="turns")
        if self.enabled:
            self.prune()

    def begin(self, **fields) -> Turn:
        return Turn(channel=self.channel, **fields)

    def write(self, turn: Turn):
        if not self.enabled:
            return
        # One writer thread: the lines land in the order the turns ended.
        task = asyncio.ensure_future(asyncio.get_running_loop().run_in_executor(self._writer, self.write_now, turn))
        self._writing.add(task)
        task.add_done_callback(self._writing.discard)

    def write_now(self, turn: Turn):
        try:
            day = datetime.fromtimestamp(turn.ts).date().isoformat()
            folder = self.root / day
            folder.mkdir(parents=True, exist_ok=True)
            line = turn.line()
            if turn.audio:
                sf.write(folder / f"{turn.id}.flac", np.concatenate(turn.audio), turn.rate, format="FLAC",
                         subtype="PCM_16")
                line["audio"] = f"{turn.id}.flac"
            with self._lock, (folder / f"{self.channel}.jsonl").open("a") as f:
                f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
            if day != self._day:
                self._day = day
                self.prune()
        except Exception as e:                                              # noqa: BLE001
            logger.warning(f"turn record: could not write turn {turn.id}: {e}")

    def prune(self, today: date | None = None):
        """Remove the days older than keep_days; anything that is not a day's folder stays."""
        cutoff = (today or date.today()) - timedelta(days=self.keep_days)
        if not self.root.is_dir():
            return
        for d in self.root.iterdir():
            try:
                day = date.fromisoformat(d.name)
            except ValueError:
                continue
            if d.is_dir() and day <= cutoff:
                shutil.rmtree(d, ignore_errors=True)
                logger.info(f"turn record: removed {d.name}, older than {self.keep_days} days")


class TurnObserver(BaseObserver):
    """Fills in the open turn (`turn`) from a pipeline's frames: each tool call with its result, what
    was spoken, and when the first token and the first audio came."""

    def __init__(self):
        super().__init__()
        self.turn: Turn | None = None
        self._seen: deque = deque(maxlen=512)               # observers see a frame once per pipeline hop
        self._calls: dict[str, dict] = {}

    def _first(self, key) -> bool:
        if key in self._seen:
            return False
        self._seen.append(key)
        return True

    async def on_push_frame(self, data: FramePushed):
        f, t = data.frame, self.turn
        if t is None:
            return
        if isinstance(f, FunctionCallsStartedFrame):
            for c in f.function_calls:
                if self._first(("call", c.tool_call_id)):
                    rec = {"name": c.function_name, "args": c.arguments if isinstance(c.arguments, dict) else {}}
                    t.tools.append(rec)
                    self._calls[c.tool_call_id] = rec
        elif isinstance(f, FunctionCallResultFrame):
            if self._first(("result", f.tool_call_id)) and (rec := self._calls.pop(f.tool_call_id, None)):
                res = f.result if isinstance(f.result, str) else json.dumps(f.result, default=str)
                rec["ok"] = bool(result_ok(f.result) and not getattr(f, "error", None))
                rec["result"] = res[:RESULT_CHARS]
        elif isinstance(f, LLMTextFrame):
            if t.first_token_ms is None:
                t.first_token_ms = t.ms_since_end()
        elif isinstance(f, TTSAudioRawFrame):
            if t.first_audio_ms is None:
                t.first_audio_ms = t.ms_since_end()
        elif isinstance(f, TTSTextFrame) and self._first(f.id):
            t.spoken.append(f.text)


class SpokenTurns(TurnObserver):
    """A browser session's turns, cut from its frames. Speech that the room's mic gate drops is not a
    turn (it never reached him), so words count once past it, as in the live feed.

    A turn is written when the next one starts, when the session ends, or once his reply is over: the
    last response has ended, no tool call is running, he has stopped speaking, and `busy` (the claim
    guard retrying, a state check still reading) is false, with nothing new for SETTLE_SECS. Nothing
    here depends on how long a model or a tool takes; a slow one keeps its turn open."""

    def __init__(self, log: TurnLog, room: str | None = None, busy: Callable[[], bool] | None = None):
        super().__init__()
        self.log, self.room, self._busy = log, room, busy
        self._speech_end: float | None = None
        self._replied = False
        self._pending: set[str] = set()                     # tool calls without a result yet
        self._ended = False                                 # the last response has ended
        self._speaking = False
        self._last = 0.0                                    # monotonic time of the last thing that happened
        self._timer: asyncio.TimerHandle | None = None

    async def on_push_frame(self, data: FramePushed):
        if not self.log.enabled:
            return
        f = data.frame
        if isinstance(f, VADUserStoppedSpeakingFrame):
            self._speech_end = f.timestamp - f.stop_secs
            if self.turn is not None and not self._replied:
                self.turn.ts = self._speech_end                 # a pause inside one request moves its end
            return
        if isinstance(f, TranscriptionFrame):
            if (not f.text.strip() or getattr(data.destination, "may_drop_speech", False)
                    or not self._first(("heard", f.id))):
                return
            if self.turn is not None and self._replied:
                self.flush()
            if self.turn is None:
                self.turn = self.log.begin(room=self.room, ts=self._speech_end or time.time())
                self._replied, self._pending, self._ended = False, set(), False
            self.turn.add(f.text.strip(), f.result if isinstance(f.result, Heard) else None)
            self.turn.stt_ms = self.turn.ms_since_end()
            self._touch()
            return
        if self.turn is None:
            return
        await super().on_push_frame(data)
        if isinstance(f, FunctionCallsStartedFrame):
            self._pending.update(c.tool_call_id for c in f.function_calls)
        elif isinstance(f, FunctionCallResultFrame):
            self._pending.discard(f.tool_call_id)
            self._ended = False                                 # the model answers the result next
        elif isinstance(f, LLMFullResponseEndFrame):
            self._ended = True
        elif isinstance(f, BotStartedSpeakingFrame):
            self._speaking = True
        elif isinstance(f, BotStoppedSpeakingFrame):
            self._speaking = False
        else:
            if not isinstance(f, (LLMTextFrame, TTSTextFrame)):
                return
        self._replied = True
        self._touch()

    def _touch(self):
        self._last = time.monotonic()
        if self._timer is None:
            self._timer = asyncio.get_running_loop().call_later(SETTLE_SECS, self._check)

    def _over(self) -> bool:
        return (self._replied and self._ended and not self._pending and not self._speaking
                and not (self._busy and self._busy()) and time.monotonic() - self._last >= SETTLE_SECS)

    def _check(self):
        self._timer = None
        if self.turn is None:
            return
        if self._over():
            self.flush()
        else:
            self._timer = asyncio.get_running_loop().call_later(SETTLE_SECS, self._check)

    def flush(self):
        """Write the open turn, if any."""
        if self._timer:
            self._timer.cancel()
            self._timer = None
        t, self.turn = self.turn, None
        if t is not None:
            self._calls.clear()
            self._pending.clear()
            self.log.write(t)
