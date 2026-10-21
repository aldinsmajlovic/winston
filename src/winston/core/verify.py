"""State check after an action: answer fast, correct afterwards.

Home Assistant answers "done" about 0.2 s after a call whether or not the bulb acted. So the result
of an act tool (turn on, turn off, set a light) goes to the model unchanged and he answers at the
usual speed; a background task then reads the targets' states at about 0.5 s, 1.5 s and wait_secs
after the result, stopping as soon as every target shows what was asked. When one does not, he
says so in a short sentence made by code, never by the model ("The shelf lamp did not respond.
Home Assistant shows it as unavailable."), once his reply has finished.

    v = Verifier(cfg, channel="voice")
    v.wrap(brain.llm, tool_names)             # Brain.start does this
    v.deliver = speak                          # the channel: how a correction reaches the user
    PipelineWorker(..., observers=[v.observer, ...])

A correction is dropped when the user interrupted him after the action, and per entity when a newer
action (a new turn, "try again") targeted the same entity: that newer action has its own check.
Every check is logged to the tool-call log as event "verify"; every correction as "correction"."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
from loguru import logger
from pipecat.frames.frames import (BotStartedSpeakingFrame, BotStoppedSpeakingFrame, FunctionCallCancelFrame,
                                   FunctionCallResultFrame, FunctionCallsStartedFrame, InterruptionFrame,
                                   LLMFullResponseEndFrame, LLMFullResponseStartFrame, TTSStartedFrame, TTSStoppedFrame,
                                   UserStartedSpeakingFrame, UserStoppedSpeakingFrame)
from pipecat.observers.base_observer import BaseObserver, FramePushed

from winston.config import resolve
from winston.config.schema import WinstonConfig
from winston.core.common import Once, entity_names, key, log_event, rewrap
from winston.core.guard import ACT, result_ok, tool_tier
from winston.providers.home_assistant import HomeAssistant

CHECKED_TOOLS = {"hassturnon": "on", "hassturnoff": "off", "hasslightset": "on"}
CHECKED_DOMAINS = {"light", "switch", "fan", "input_boolean"}    # on/off is the whole story for these
FIRST_READS = (0.5, 1.5)             # seconds after the result; the last read is at wait_secs
BRIGHTNESS_SLACK = 3                 # of 255
KELVIN_SLACK = 100
READ_TIMEOUT = 2.0
SETTLE_SECS = 0.4                    # quiet this long after his reply before a correction is spoken
REPLY_DUE_SECS = 2.5                 # after the user stops speaking, his reply is due this long
QUIET_LIMIT_SECS = 60                # a correction still unsaid after this is stale and dropped
DEAD = {"unavailable", "unknown"}
UNREAD = (aiohttp.ClientError, TimeoutError, ValueError, RuntimeError)     # a state not read is no claim

Deliver = Callable[[str], Awaitable[None]]


# ---------------------------------------------------------------- what was asked (pure)


@dataclass
class Expect:
    state: str                       # "on" or "off"
    pct: int | None = None           # brightness percentage asked for
    kelvin: int | None = None        # colour temperature asked for

    @property
    def brightness(self) -> int | None:
        return None if self.pct is None else round(self.pct * 255 / 100)

    def as_dict(self) -> dict:
        return {k: v for k, v in {"state": self.state, "brightness_pct": self.pct, "kelvin": self.kelvin}.items()
                if v is not None}


def _int(v) -> int | None:
    try:
        return round(float(v))
    except (TypeError, ValueError):
        return None


def expectation(tool: str, args: dict | None) -> Expect | None:
    """What the targets should show after this tool, or None for a tool that is not checked."""
    base = tool.split("__")[-1].lower()
    if base not in CHECKED_TOOLS or tool_tier(tool) != ACT:
        return None
    args = args if isinstance(args, dict) else {}
    e = Expect(CHECKED_TOOLS[base])
    if base == "hasslightset":
        e.pct, e.kelvin = _int(args.get("brightness")), _int(args.get("temperature"))
        if e.pct is not None:
            e.pct = max(0, min(100, e.pct))
            if e.pct == 0:
                e.state, e.pct = "off", None
    return e


def parse_result(result) -> dict | None:
    """Home Assistant's intent response as a dict, or None when it is not one that succeeded."""
    if not result_ok(result):
        return None
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    if not isinstance(result, dict) or result.get("response_type") != "action_done":
        return None
    return result


def targets(result: dict) -> list[tuple[str | None, str | None]]:
    """(entity_id, name) for each entity the action succeeded on. Older Home Assistant versions give
    only the name."""
    items = ((result.get("data") or {}).get("success")) or []
    return [(i.get("id"), i.get("name")) for i in items if isinstance(i, dict) and i.get("type") == "entity"]


def observe(s: dict | None) -> dict:
    """The parts of a state the check compares, for the log."""
    if not s:
        return {"state": "missing"}
    a = s.get("attributes") or {}
    out = {"state": s.get("state")}
    if isinstance(a.get("brightness"), (int, float)):
        out["brightness"] = a["brightness"]
    if isinstance(a.get("color_temp_kelvin"), (int, float)):
        out["kelvin"] = a["color_temp_kelvin"]
    return out


def mismatch(e: Expect, s: dict | None) -> str | None:
    """Why a state is not what was asked: "dead", "state", "brightness", "kelvin", or None when it is.
    A state that could not be read at all is not a mismatch: no claim without a reading."""
    if s is None:
        return None
    state = str(s.get("state"))
    if state in DEAD:
        return "dead"
    if state != e.state:
        return "state"
    if e.state == "on":
        a = s.get("attributes") or {}
        b = a.get("brightness")
        if e.brightness is not None and isinstance(b, (int, float)) and abs(b - e.brightness) > BRIGHTNESS_SLACK:
            return "brightness"
        k = a.get("color_temp_kelvin")
        if e.kelvin is not None and isinstance(k, (int, float)) and abs(k - e.kelvin) > KELVIN_SLACK:
            return "kelvin"
    return None


# ---------------------------------------------------------------- what he says (pure)


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_words(n: int) -> str:
    """0 to 100 as words, for the TTS; anything else as digits."""
    if not 0 <= n <= 100:
        return str(n)
    if n == 100:
        return "one hundred"
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + ("" if n % 10 == 0 else f"-{_ONES[n % 10]}")


def _plural(name: str) -> bool:
    last = name.split()[-1].lower() if name.split() else ""
    return last.endswith("s") and not last.endswith("ss")


def _the(name: str) -> str:
    """"shelf lamp" is a thing and gets "the"; "Hyllan" or "Main room lights" is a name and does not."""
    return f"the {name}" if name[:1].islower() else name


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _pct(s: dict | None) -> int | None:
    b = ((s or {}).get("attributes") or {}).get("brightness")
    return round(b / 255 * 100) if isinstance(b, (int, float)) else None


@dataclass
class Outcome:
    entity: str
    name: str
    domain: str
    why: str | None                  # mismatch(); None: it did what was asked
    state: dict | None


def clause(o: Outcome, e: Expect, alone: bool) -> str:
    """One failure, without the full stop. alone: the whole correction is about this entity."""
    who, be = _the(o.name), "are" if _plural(o.name) else "is"
    if o.why == "dead":
        if alone:
            return f"{who} did not respond. Home Assistant shows {'them' if _plural(o.name) else 'it'} as unavailable"
        return f"{who} did not respond"
    if o.why == "state":
        return f"{who} {be} still {(o.state or {}).get('state')}"
    if o.why == "brightness":
        now = _pct(o.state)
        return (f"{who} {be} at {number_words(now)} percent, not {number_words(e.pct)}" if now is not None
                else f"{who} did not change brightness")
    return f"{who} did not change colour temperature"


def _done(e: Expect, n: int) -> str:
    be = "is" if n == 1 else "are"
    if e.state == "off":
        return f"{be} off"
    if e.pct is not None:
        return f"{be} at {number_words(e.pct)} percent"
    return f"{be} on"


def _noun(outcomes: list[Outcome]) -> str:
    domains = {o.domain for o in outcomes}
    return "lights" if domains == {"light"} else "switches" if domains == {"switch"} else "devices"


def compose(outcomes: list[Outcome], e: Expect) -> str | None:
    """The correction for one action's targets, or None when every target did what was asked.
    "The ceiling lamp is still off." / "Two of the lights are at fifty percent; the shelf lamp did not
    respond." / "None of the four lights responded." """
    bad = [o for o in outcomes if o.why]
    good = [o for o in outcomes if not o.why]
    if not bad:
        return None
    if good:
        n = len(good)
        head = f"{_cap(number_words(n))} of the {_noun(outcomes)} {_done(e, n)}"
        return f"{head}; {', '.join(clause(o, e, False) for o in bad)}."
    if len(bad) >= 3 and all(o.why == "dead" for o in bad):
        return f"None of the {number_words(len(bad))} {_noun(bad)} responded."
    return " ".join(_cap(clause(o, e, len(bad) == 1)) + "." for o in bad)


def spoken_name(cfg: WinstonConfig, eid: str, state: dict | None, said: str | None = None) -> str:
    """What he calls the entity: the name on his list, else its first alias there, else the name the
    action used, else Home Assistant's friendly name. Home Assistant's names are the household's
    (often Swedish) and read badly in his English sentence."""
    ha = cfg.providers.home_assistant
    ent = ha.expose.get(eid) if ha else None
    if ent and ent.name:
        return ent.name
    if ent and ent.aliases:
        return ent.aliases[0]
    friendly = ((state or {}).get("attributes") or {}).get("friendly_name")
    return said or friendly or eid.split(".", 1)[-1].replace("_", " ")


def resolve_names(cfg: WinstonConfig, names: list[str], states: list[dict]) -> dict[str, str]:
    """name -> entity_id for results without ids: the names on his list (common.entity_names), then friendly names."""
    index: dict[str, str] = {}
    for s in states:
        if n := (s.get("attributes") or {}).get("friendly_name"):
            index.setdefault(key(str(n)), s["entity_id"])
    ha = cfg.providers.home_assistant
    for eid, ent in (ha.expose.items() if ha else ()):
        for n in entity_names(eid, ent):
            index[n] = eid
    return {n: index[key(n)] for n in names if key(n) in index}


# ---------------------------------------------------------------- the check


@dataclass(eq=False)
class Check:
    seq: int
    tool: str
    call_id: str
    expect: Expect
    targets: list[tuple[str | None, str | None]]
    t0: float = field(default_factory=time.monotonic)
    outcomes: list[Outcome] = field(default_factory=list)
    interrupted: bool = False


class Verifier:
    """Checks act-tool results against Home Assistant's states and delivers corrections through
    `deliver`, one at a time, once the channel is quiet."""

    def __init__(self, cfg: WinstonConfig, channel: str = "?"):
        self.cfg, self.channel = cfg, channel
        ha = cfg.providers.ha
        self.enabled = bool(ha and ha.verify.enabled)
        self.wait_secs = ha.verify.wait_secs if ha else 3.0
        self.deliver: Deliver | None = None
        self.observer = QuietObserver(self)
        self._log: Path = resolve(cfg.logging.tool_calls)
        self._seq = 0
        self._owner: dict[str, int] = {}             # entity -> the newest check that targets it
        self._running: set[Check] = set()
        self._outbox: list[Check] = []
        self._tasks: set[asyncio.Task] = set()
        self._delivering = False
        self._sender: asyncio.Task | None = None

    # -------------------------------------------------- wiring

    def wrap(self, llm, names: list[str]) -> list[str]:
        """Wrap the handlers of the checked tools among names so each successful result starts a
        check after it has gone to the model. Returns the wrapped names."""
        if not self.enabled:
            return []
        return [name for name in names if expectation(name, {}) is not None and rewrap(llm, name, self._checked)]

    def _checked(self, original):
        async def handler(params):
            deliver = params.result_callback

            async def result_callback(result, *, properties=None):
                await deliver(result, properties=properties)     # the model gets it first, unchanged
                if properties is None or getattr(properties, "is_final", True):
                    self.after(params.function_name, params.tool_call_id, params.arguments, result)
            params.result_callback = result_callback
            await original(params)
        return handler

    def after(self, tool: str, call_id: str, args: dict | None, result: Any) -> Check | None:
        """A tool result has gone to the model: start its check, if it gets one."""
        e = expectation(tool, args)
        r = parse_result(result) if e and self.enabled else None
        found = targets(r) if r else []
        if not found:
            return None
        self._seq += 1
        c = Check(self._seq, tool, call_id, e, found)
        for eid, _ in found:
            if eid:
                self._own(eid, c.seq)
        self._running.add(c)
        self._spawn(self._check(c))
        return c

    def _spawn(self, coro):
        t = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def _own(self, eid: str, seq: int):
        self._owner[eid] = max(seq, self._owner.get(eid, 0))

    def interrupted(self):
        """The user spoke over him: nothing checked so far is worth saying any more."""
        for c in [*self._running, *self._outbox]:
            c.interrupted = True

    @property
    def idle(self) -> bool:
        return not self._running and not self._outbox and not self._delivering

    async def drained(self, timeout: float | None = None):
        """Until every check has finished and its correction was delivered (or timeout)."""
        end = time.monotonic() + (timeout if timeout is not None else self.wait_secs + 5)
        while not self.idle and time.monotonic() < end:
            await asyncio.sleep(0.05)

    async def close(self):
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # -------------------------------------------------- reading Home Assistant

    async def _states(self, ha: HomeAssistant, ids: list[str]) -> dict[str, dict | None]:
        async def one(eid):
            try:
                return eid, await ha.get(f"/api/states/{eid}", READ_TIMEOUT)
            except UNREAD:
                return eid, None
        return dict(await asyncio.gather(*(one(e) for e in ids)))

    async def _check(self, c: Check):
        try:
            await self._run(c)
        except Exception as e:                                          # noqa: BLE001
            logger.warning(f"state check after {c.tool} failed: {e}")
        finally:
            self._running.discard(c)
            if c.outcomes and any(o.why for o in c.outcomes) and c.interrupted:
                self._write(event="correction", id=c.call_id, text=None, dropped="interrupted")
            elif c.outcomes and any(o.why for o in c.outcomes):
                self._outbox.append(c)
                if self._sender is None or self._sender.done():
                    self._sender = self._spawn(self._send())

    async def _run(self, c: Check):
        ha = HomeAssistant(self.cfg)
        ids = [eid for eid, _ in c.targets if eid]
        said = {eid: name for eid, name in c.targets if eid}
        unnamed = [name for eid, name in c.targets if not eid and name]
        if unnamed:
            try:
                everything = await ha.get("/api/states", READ_TIMEOUT)
            except UNREAD:
                everything = []
            for name, eid in resolve_names(self.cfg, unnamed, everything).items():
                if eid not in said:
                    ids.append(eid)
                    said[eid] = name
                    self._own(eid, c.seq)
        reads = sorted({t for t in FIRST_READS if t < self.wait_secs} | {self.wait_secs})
        expanded = False
        states: dict[str, dict | None] = {}
        for at in reads:
            await asyncio.sleep(max(0.0, c.t0 + at - time.monotonic()))
            if c.interrupted:
                for eid in ids:
                    self._write(event="verify", tool=c.tool, id=c.call_id, entity=eid,
                                expected=c.expect.as_dict(), observed=None, ok=None, stopped="interrupted",
                                ms=round((time.monotonic() - c.t0) * 1000))
                return
            states = await self._states(ha, ids)
            if not expanded:                        # a light group: check its members
                expanded = True
                members = []
                for eid in list(ids):
                    m = ((states.get(eid) or {}).get("attributes") or {}).get("entity_id")
                    if isinstance(m, list) and m:
                        ids.remove(eid)
                        members += [x for x in m if isinstance(x, str) and x not in ids]
                if members:
                    ids += members
                    for eid in members:
                        self._own(eid, c.seq)
                    states = await self._states(ha, ids)
            ids = [eid for eid in ids if eid.split(".")[0] in CHECKED_DOMAINS]
            if not ids or all(mismatch(c.expect, states.get(eid)) is None for eid in ids):
                break
        ms = round((time.monotonic() - c.t0) * 1000)
        for eid in ids:
            s = states.get(eid)
            why = mismatch(c.expect, s)
            c.outcomes.append(Outcome(eid, spoken_name(self.cfg, eid, s, said.get(eid)), eid.split(".")[0], why, s))
            self._write(event="verify", tool=c.tool, id=c.call_id, entity=eid, expected=c.expect.as_dict(),
                        observed=observe(s) if s is not None else "unread", ok=why is None, ms=ms)
            if why:
                logger.warning(f"state check: {eid} after {c.tool} expected {c.expect.as_dict()}, "
                               f"observed {observe(s)} ({why})")

    # -------------------------------------------------- saying it

    def _text(self, checks: list[Check]) -> str | None:
        parts = []
        for c in checks:
            if c.interrupted:
                self._write(event="correction", id=c.call_id, text=None, dropped="interrupted")
                continue
            mine = [o for o in c.outcomes if self._owner.get(o.entity, 0) <= c.seq]
            if len(mine) < len(c.outcomes):
                self._write(event="correction", id=c.call_id, dropped="newer action",
                            entities=[o.entity for o in c.outcomes if o not in mine])
            if text := compose(mine, c.expect):
                parts.append(text)
        return " ".join(parts) or None

    async def _send(self):
        while self._outbox:
            if not await self.observer.quiet(SETTLE_SECS, QUIET_LIMIT_SECS):
                for c in self._outbox:
                    self._write(event="correction", id=c.call_id, text=None, dropped="never quiet")
                self._outbox = []
                return
            checks, self._outbox = self._outbox, []
            text = self._text(checks)
            if not text:
                continue
            self._delivering = True
            try:
                logger.info(f"state check correction: {text!r}")
                self._write(event="correction", id=",".join(c.call_id for c in checks), text=text,
                            delivered=self.deliver is not None)
                if self.deliver:
                    await self.deliver(text)
            except Exception as e:                                      # noqa: BLE001
                logger.warning(f"state check correction not delivered: {e}")
            finally:
                self._delivering = False

    def _write(self, **rec):
        log_event(self._log, self.channel, **rec)


class QuietObserver(BaseObserver):
    """Knows when the channel is quiet: no reply being generated or spoken, no tool running and the
    user not speaking. A user who starts speaking while he is busy has interrupted him."""

    def __init__(self, verifier: Verifier):
        super().__init__()
        self._v = verifier
        self._llm = self._tts = self._bot = self._user = False
        self._pending: set[str] = set()
        self._started = Once(256)        # observers see a frame once per pipeline hop
        self._since = 0.0                # monotonic time of the last change of any of the above
        self._heard = 0.0                # the user stopped speaking and his reply has not begun yet

    @property
    def busy(self) -> bool:
        due = self._heard and time.monotonic() - self._heard < REPLY_DUE_SECS
        return self._llm or self._tts or self._bot or self._user or bool(self._pending) or bool(due)

    def _set(self, name: str, value: bool):
        if getattr(self, name) != value:
            setattr(self, name, value)
            self._since = time.monotonic()

    async def quiet(self, settle: float, limit: float = QUIET_LIMIT_SECS) -> bool:
        """Until the channel has been quiet for settle seconds; False when it was not within limit."""
        end = time.monotonic() + limit
        while self.busy or time.monotonic() - self._since < settle:
            if time.monotonic() > end:
                return False
            await asyncio.sleep(0.05)
        return True

    async def on_push_frame(self, data: FramePushed):
        f = data.frame
        if isinstance(f, (UserStartedSpeakingFrame, InterruptionFrame)):
            if self._llm or self._tts or self._bot:
                self._v.interrupted()
            if isinstance(f, UserStartedSpeakingFrame):
                self._set("_user", True)
        elif isinstance(f, UserStoppedSpeakingFrame):
            if self._user:
                self._heard = time.monotonic()
            self._set("_user", False)
        elif isinstance(f, LLMFullResponseStartFrame):
            self._heard = 0.0
            self._set("_llm", True)
        elif isinstance(f, LLMFullResponseEndFrame):
            self._set("_llm", False)
        elif isinstance(f, TTSStartedFrame):
            self._set("_tts", True)
        elif isinstance(f, TTSStoppedFrame):
            self._set("_tts", False)
        elif isinstance(f, BotStartedSpeakingFrame):
            self._set("_bot", True)
        elif isinstance(f, BotStoppedSpeakingFrame):
            self._set("_bot", False)
        elif isinstance(f, FunctionCallsStartedFrame):
            for c in f.function_calls:
                if self._started(c.tool_call_id):      # its first sighting comes before its result
                    self._pending.add(c.tool_call_id)
                    self._since = time.monotonic()
        elif isinstance(f, (FunctionCallResultFrame, FunctionCallCancelFrame)):     # or cancelled
            if f.tool_call_id in self._pending:
                self._pending.discard(f.tool_call_id)
                self._since = time.monotonic()
