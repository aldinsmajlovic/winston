"""The turn record: every spoken turn kept with its audio, so his mistakes can be found, marked and
used to test the next change (docs/understanding.md, part 1). Off unless logging.turns.enabled.

    <data>/turns/2026-10-07/satellite.jsonl   one JSON line per turn, a file per channel process
    <data>/turns/2026-10-07/<id>.flac         the audio the speech engine heard, as it heard it

A line holds: id, ts (when the speech ended), channel, room, start (wake, follow_up, barge_in, button or
hold on a box, speech in a browser), secs of audio, heard, raw (what the engine said before the
hint-word fix, only when it differs), engine, stt_ms, first_token_ms and first_audio_ms (from the end of
speech), tools (name, args, ok, result), said (what was spoken, fillers and corrections included),
outcome (answered: he spoke or acted; silent; dismissed; nothing_heard; interrupted: talked over on a
box) and audio (the file's name).

A box's turn is begun and written by the satellite channel around one request. A browser's turns are
cut from the pipeline's frames by SpokenTurns: a turn starts with the first words past the room's mic
gate and is written when the next one starts, the session ends, or his reply is over.

Days older than keep_days are removed when a channel starts and when the day turns."""
import asyncio
import json
import re
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import soundfile as sf
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
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
from pipecat.services.llm_service import FunctionCallParams

from winston.config import resolve
from winston.config.schema import WinstonConfig
from winston.core.common import Once
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

    @property
    def ended(self) -> str:
        """How it ended: as the channel set it, else answered (he spoke or acted) or silent."""
        return self.outcome or ("answered" if self.said or self.tools else "silent")

    @property
    def markable(self) -> bool:
        """A turn he answered or let pass: what "that's not what I said" refers to."""
        return self.ended in ("answered", "silent")

    def line(self) -> dict:
        raw = " ".join(r for r in self.raws if r).strip()
        secs = sum(len(a) for a in self.audio) / self.rate if self.audio else 0.0
        outcome = self.ended
        return {"id": self.id, "ts": round(self.ts, 3), "channel": self.channel, "room": self.room,
                "start": self.start, "secs": round(secs, 2), "heard": self.heard,
                **({"raw": raw} if raw and raw != self.heard else {}),
                "engine": self.engine, "stt_ms": self.stt_ms, "first_token_ms": self.first_token_ms,
                "first_audio_ms": self.first_audio_ms, "tools": self.tools, "said": self.said, "outcome": outcome}


class TurnLog:
    """Writes one channel process's turns, off the event loop, never raising into a turn. Each turn's
    timings and tool names go to logging.latency (when set) whether or not the turn record is on; the
    whole turn and its audio only when it is."""

    def __init__(self, cfg: WinstonConfig, channel: str, on_written: Callable[[Turn], object] | None = None):
        t = cfg.logging.turns
        self.enabled, self.keep_days, self.root = t.enabled, t.keep_days, resolve(t.dir)
        self.latency = resolve(cfg.logging.latency) if cfg.logging.latency else None
        self.channel = channel
        self.on_written = on_written        # on the event loop once a turn is on disk (the live feed's "turn")
        self._day: str | None = None
        self._lock = threading.Lock()                       # a satellite process writes for several boxes
        self._writing: set[asyncio.Task] = set()
        self._writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="turns")
        if self.enabled:
            self.prune()

    @property
    def active(self) -> bool:
        """Anything to keep from a turn: the whole of it, or its timings."""
        return self.enabled or self.latency is not None

    def begin(self, **fields) -> Turn:
        return Turn(channel=self.channel, **fields)

    def write(self, turn: Turn):
        if not self.active:
            return
        # One writer thread: the lines land in the order the turns ended.
        task = asyncio.ensure_future(asyncio.get_running_loop().run_in_executor(self._writer, self.write_now, turn))
        self._writing.add(task)
        task.add_done_callback(self._writing.discard)
        if self.on_written:
            task.add_done_callback(lambda done: done.result() and self.on_written(turn))

    def write_now(self, turn: Turn) -> bool:
        """The turn's timings, then the turn itself when the record is on; True once the turn is on disk."""
        self.timings(turn)
        if not self.enabled:
            return False
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
            return True
        except Exception as e:                                              # noqa: BLE001
            logger.warning(f"turn record: could not write turn {turn.id}: {e}")
            return False

    def timings(self, turn: Turn):
        """One line in logging.latency: milliseconds from the end of speech to the text (stt_ms), the first
        token and the first audio, and the names of the tools called. No words, no audio. A request that
        never reached the brain (nothing heard, dismissed) or has no time at all has none."""
        if (not self.latency or turn.outcome in ("nothing_heard", "dismissed")
                or (turn.stt_ms is None and turn.first_token_ms is None)):
            return
        line = {"ts": round(turn.ts, 3), "channel": self.channel, "stt_ms": turn.stt_ms,
                "first_token_ms": turn.first_token_ms, "first_audio_ms": turn.first_audio_ms,
                "tools": [c["name"] for c in turn.tools]}
        try:
            self.latency.parent.mkdir(parents=True, exist_ok=True)
            with self.latency.open("a") as f:
                f.write(json.dumps(line) + "\n")
        except OSError as e:
            logger.warning(f"latency log: {e}")

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
        self._first = Once()                                # observers see a frame once per pipeline hop
        self._calls: dict[str, dict] = {}

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
    last response has ended, no tool call is running, he has stopped speaking, and `busy` (a state check
    still reading) is false, with nothing new for SETTLE_SECS. Nothing
    here depends on how long a model or a tool takes; a slow one keeps its turn open. It runs while the
    log keeps anything (`TurnLog.active`); the audio is held only when the turn record is on."""

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
        self.last_id: str | None = None                     # the last turn he answered or let pass

    async def on_push_frame(self, data: FramePushed):
        if not self.log.active:
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
            heard = f.result if self.log.enabled and isinstance(f.result, Heard) else None  # audio only when kept
            self.turn.add(f.text.strip(), heard)
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
            if t.markable:
                self.last_id = t.id
            self.log.write(t)


# Marks: a mistake noted on a turn, by voice, on the page or from the command line.

KINDS = ("misheard", "not_addressed", "wrong_action", "too_slow")
_ID = re.compile(r"^[0-9a-f]{12}$")


class TurnStore:
    """Reads the turn record and keeps the marks. A marked turn is copied, line and audio, into
    <root>/marked/ with its mark, which pruning leaves alone, so it stays until it is unmarked. A mark
    holds the kind, and when given: what you actually said (said), what you wanted done (wanted), the
    tools it should call and must not call in the brain trials' terms (expect, forbid), a note."""

    def __init__(self, cfg: WinstonConfig):
        self.enabled, self.root = cfg.logging.turns.enabled, resolve(cfg.logging.turns.dir)
        self.marked_dir = self.root / "marked"

    def days(self) -> list[str]:
        """The days with turns, newest first."""
        if not self.root.is_dir():
            return []
        return sorted((d.name for d in self.root.iterdir() if d.is_dir() and _is_day(d.name)), reverse=True)

    def day(self, day: str) -> list[dict]:
        """A day's turns from every channel, oldest first, each with its mark if it has one."""
        if not _is_day(day):
            raise ValueError(f"not a day: {day!r}")
        marks = {m["id"]: m["mark"] for m in self.marked()}
        rows = [{**t, "day": day, **({"mark": marks[t["id"]]} if t["id"] in marks else {})}
                for t in _read_lines(self.root / day)]
        return sorted(rows, key=lambda t: t["ts"])

    def marked(self) -> list[dict]:
        if not self.marked_dir.is_dir():
            return []
        rows = []
        for f in self.marked_dir.glob("*.json"):
            try:
                rows.append(json.loads(f.read_text()))
            except (OSError, ValueError) as e:
                logger.warning(f"turn record: unreadable mark {f.name}: {e}")
        return sorted(rows, key=lambda t: t["ts"])

    def find(self, turn_id: str) -> dict | None:
        """A turn by its id: a marked one first, else the newest day that has it."""
        _check_id(turn_id)
        if (f := self.marked_dir / f"{turn_id}.json").is_file():
            return json.loads(f.read_text())
        for day in self.days():
            for t in _read_lines(self.root / day):
                if t["id"] == turn_id:
                    return {**t, "day": day}
        return None

    def audio(self, turn_id: str) -> Path | None:
        _check_id(turn_id)
        if (f := self.marked_dir / f"{turn_id}.flac").is_file():
            return f
        t = self.find(turn_id)
        if t and t.get("audio") and (f := self.root / t["day"] / t["audio"]).is_file():
            return f
        return None

    def mark(self, turn_id: str, kind: str, *, said: str | None = None, wanted: str | None = None,
             expect: list[str] | None = None, forbid: list[str] | None = None, note: str | None = None,
             by: str = "page") -> dict:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        t = self.find(turn_id)
        if t is None:
            raise KeyError(f"no turn {turn_id}")
        mark = {"kind": kind, "by": by, "ts": round(time.time(), 3),
                **{k: v for k, v in (("said", said), ("wanted", wanted), ("expect", expect), ("forbid", forbid),
                                     ("note", note)) if v}}
        self.marked_dir.mkdir(parents=True, exist_ok=True)
        src = self.audio(turn_id)
        if src is not None and src.parent != self.marked_dir:
            shutil.copy2(src, self.marked_dir / f"{turn_id}.flac")
        record = {**{k: v for k, v in t.items() if k != "mark"}, "mark": mark}
        (self.marked_dir / f"{turn_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))
        logger.info(f"turn record: {turn_id} marked {kind} ({by}): {t['heard']!r}")
        return record

    def unmark(self, turn_id: str) -> bool:
        _check_id(turn_id)
        found = False
        for f in (self.marked_dir / f"{turn_id}.json", self.marked_dir / f"{turn_id}.flac"):
            if f.is_file():
                f.unlink()
                found = True
        return found


def _is_day(name: str) -> bool:
    try:
        date.fromisoformat(name)
        return True
    except ValueError:
        return False


def _check_id(turn_id: str):
    if not _ID.match(turn_id or ""):
        raise ValueError(f"not a turn id: {turn_id!r}")


def _read_lines(folder: Path) -> list[dict]:
    rows = []
    for f in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
        for line in f.read_text().splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    logger.warning(f"turn record: a broken line in {f}")
    return rows


def make_mark_tool(cfg: WinstonConfig, previous: Callable[[], str | None]):
    """mark_mistake: you tell him his last turn went wrong ("that's not what I said", "I wasn't talking to
    you"), and that turn is marked in the record. `previous` gives the id of this conversation's last
    turn that he answered or let pass, the one before the turn that asks for the mark. He confirms in his
    own words, from the result."""
    store = TurnStore(cfg)

    async def mark_mistake(params: FunctionCallParams):
        a = params.arguments
        kind = {"not_for_you": "not_addressed", "wrong_thing": "wrong_action"}.get(a.get("kind"), a.get("kind"))
        if not store.enabled:
            return await params.result_callback({"error": "turns are not being kept (logging.turns.enabled is "
                                                          "off), so there is nothing to mark"})
        turn_id = previous()
        t = None
        for _ in range(6):                  # the turn before is written as this one starts; give it a moment
            if turn_id and (t := store.find(turn_id)):
                break
            await asyncio.sleep(0.5)
            turn_id = turn_id or previous()
        if not t:
            return await params.result_callback({"error": "there is no earlier turn in this conversation to mark"})
        try:
            store.mark(turn_id, kind, said=_text(a.get("said")), wanted=_text(a.get("wanted")), by="voice")
        except (KeyError, ValueError) as e:
            return await params.result_callback({"error": str(e)})
        await params.result_callback({"marked": kind, "that_turn_heard": t["heard"], "that_turn_said": t["said"]})

    schema = FunctionSchema(
        name="mark_mistake",
        description="Mark your previous turn as a mistake, so it can be looked at and used to test fixes. Use it "
                    "when the user says you misheard them, that they were not talking to you, that you did the "
                    "wrong thing, or that you were too slow. Then say briefly that it is noted. If they say what "
                    "they actually said or wanted, pass it.",
        properties={"kind": {"type": "string", "enum": ["misheard", "not_for_you", "wrong_thing", "too_slow"]},
                    "said": {"type": "string", "description": "What they actually said, if they told you."},
                    "wanted": {"type": "string", "description": "What they said should have happened, in their own "
                                                          "words. Leave it out unless they told you; never your "
                                                          "guess."}},
        required=["kind"])
    return schema, mark_mistake


def _text(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None
