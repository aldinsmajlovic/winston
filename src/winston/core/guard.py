"""Claim guard: plan §6 rule 4 as code. A reply that claims an action or a current state must be
backed by a tool call in the same user turn or the one before. Seen twice on 2026-09-23 with the 9B:
"Setting the lights now" with no call, then "already on at one hundred percent" built on its own
earlier claim.

The guard lets the reply through a sentence at a time (the TTS waits for sentence ends anyway) and
judges each sentence against the calls so far. The model calls tools before it speaks, so a backed
claim passes straight through. An unbacked one is held back and never said, so it never reaches the
context either. When the reply drops a claim, or ends on a promise to act that no call kept ("Let me
turn them on."), the guard asks the model once more with a note in the house convention, at most once
per user turn. Only when that retry fails too does he say a soft line ("Sorry, that didn't go
through. Shall I do it now?").

    guard = ClaimGuard(cfg, channel="voice")
    Pipeline([..., brain.llm, guard, tts, ...]), observers=[guard.observer, ...]

The observer keeps the tool bookkeeping because the function-call frames never travel downstream
past the LLM; the processor sees the reply text. A channel that closes a reply on silence waits
while guard.retrying is set."""
import json
import re
import time
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (Frame, FunctionCallResultFrame, FunctionCallsStartedFrame,
                                   InterruptionFrame, LLMFullResponseEndFrame, LLMFullResponseStartFrame,
                                   LLMMessagesAppendFrame, LLMTextFrame, TranscriptionFrame,
                                   UserStartedSpeakingFrame)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from winston.config import resolve
from winston.config.schema import WinstonConfig

READ, ACT = "read", "act"

STATE = "state"

# Said in his voice when the retry did not help either. No "Correction:": he simply owns it.
SOFT = {
    ACT: "Sorry, that didn't go through. Shall I do it now?",
    STATE: "Actually, I haven't checked that. Want me to look?",
}


READ_TOOLS = {"web_search", "ask_claude"}
# Tools that say their own tier (extensions declare one per tool), by the name the brain sees.
DECLARED: dict[str, str] = {}


def set_tier(name: str, tier: str) -> None:
    DECLARED[name] = tier


def tool_tier(name: str) -> str:
    """A tool's declared tier when it has one (an extension's); otherwise read tools only look
    (GetLiveContext, GetDateTime, get_sensor_readings, web_search, ask_claude) and everything else acts."""
    if name in DECLARED:
        return DECLARED[name]
    base = name.split("__")[-1].lower()
    return READ if base.startswith("get") or base in READ_TOOLS else ACT


def result_ok(result) -> bool:
    """HA's MCP server reports failures as a normal result string starting with "Error"."""
    if isinstance(result, dict):
        return not (result.get("error") or result.get("is_error"))
    return not str(result).lstrip().lower().startswith("error")


# Sentences that are questions, refusals, negations, failure reports or talk about what he can do
# ("I can turn lights on and off") claim nothing.
_SKIP = re.compile(r"\?|\b(?:would you|do you want|shall i|should i|can't|cannot|couldn't|could not|"
                   r"unable|not able|didn't|did not|failed|not something|not|never|won't|i can|i could)\b|n't\b")
# Firm claims: "I set", "I'll set", "I'm going to set", "has been set", "turned on", "done" (a
# sentence that starts with it; "flip once, done" in a recipe claims nothing).
_ACT_STRONG = re.compile(
    r"\bi(?:'ve| have|'ll| will|'m| am)?(?: just| going to)? "
    r"(?:set|turned?|switch(?:ed)?|dimm?(?:ed)?|changed?|adjust(?:ed)?|boot(?:ed)?|start(?:ed)?|woke|wake|put|"
    r"sen[dt]|messag(?:e|ed))\b"
    r"|\b(?:has|have) been (?:set|turned|switched|dimmed|changed|adjusted|booted|sent)\b"
    r"|\b(?:turned|switched) (?:on|off)\b|^(?:(?:it'?s|it is|that'?s|that is|all) )?done\b")
# "I'll remember that", "I've noted it", "I've forgotten that": a memory claim, backed only by the memory
# tools (remember, forget). Bare "I remember" is recall from the prompt, not a claim. A bare "Noted." is
# also what he says to a nudge put off ("Noted, sir. I'll remind you tomorrow."), so follow_up backs it too.
MEMORY_TOOLS = {"remember", "forget"}
_REMEMBER = re.compile(r"\bi(?:'ll| will|'ve| have)(?: just)? (?:remember|noted?|made a note)\b"
                       r"|\bi'll keep (?:that|it|this) in mind\b"
                       r"|\bi(?:'ve| have)(?: just)? forgotten\b")
_NOTED = re.compile(r"^(?:got it, |very good, |very well, )?noted\b")
NOTING_TOOLS = MEMORY_TOOLS | {"follow_up"}
# "I've updated it", "I removed milk from the list", "I've saved that": a change claim that may be about
# memory or about the home (a to-do list, a voice); any act tool backs it, only a memory tool when the
# sentence says it is about memory ("I removed the note about the dentist").
_CHANGED = re.compile(r"\bi(?:'ll| will|'ve| have)(?: just)? (?:saved?|stored?)\b"
                      r"|\bi(?:'ve| have)?(?: just)? (?:removed|deleted|updated)\b")
_ABOUT_MEMORY = re.compile(r"\b(?:memory|memories|notes?|facts?)\b")
# "it has been removed", "your order has been updated": the same claim without the "I", but chat says
# it too ("the forecast has been updated"), so like the loose patterns it counts only when no tool ran.
_REMEMBER_PASSIVE = re.compile(r"\b(?:has|have) been (?:forgotten|removed|deleted|updated)\b")
# The loose patterns below also match chat ("honey is still edible", "turning to your question"), so
# they count only in a sentence that names something he can act on (see targets()).
# "setting the lights now": a change in progress.
_ACT_ING = re.compile(r"\b(?:setting|turning|switching|dimming|changing|adjusting|booting|waking)\b")
# "are now on", "now set to warm": a change claim unless a tool of any kind ran this turn.
_ACT_WEAK = re.compile(r"\b(?:is|are|'s|'re) now\b|\bnow (?:set|on|off|at|turned|dimmed|warm|cool|back)\b")
# "is already on", "are currently at": a state claim that needs some tool to have looked.
_STATE = re.compile(r"\b(?:is|are|'s|'re) (?:already|currently|still)\b|\bcurrently\b")

# Always a target, whatever Home Assistant exposes.
GENERIC_TARGETS = ("light", "lights", "lamp", "lamps", "brightness")


def targets(cfg: WinstonConfig | None = None) -> re.Pattern:
    """The things a loose claim must name: the generic words plus every exposed Home Assistant
    entity's name, aliases and object id ("light.shelf_lamp" is "shelf lamp"), lowercased."""
    names = set(GENERIC_TARGETS)
    ha = cfg.providers.home_assistant if cfg else None
    for entity_id, e in (ha.expose if ha else {}).items():
        names.add(entity_id.split(".", 1)[-1].replace("_", " "))
        names.update(n for n in [e.name, *e.aliases] if n)
    names = {n.strip().lower() for n in names if n.strip()}
    return re.compile(r"\b(?:" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b")


_GENERIC = targets()


def judge(text: str, tools: list[tuple[str, bool]], names: re.Pattern = _GENERIC) -> str | None:
    """Verdict for a reply given the (tool name, succeeded) calls of the same user turn:
    "act" for a change claim no successful act tool backs (a memory claim: no remember or forget),
    "state" for a state claim no successful tool backs, None when the reply is fine. names is
    targets(cfg)."""
    ok = {tool_tier(name) for name, succeeded in tools if succeeded}
    memory_ok = any(succeeded and name.split("__")[-1].lower() in MEMORY_TOOLS for name, succeeded in tools)
    noted_ok = any(succeeded and name.split("__")[-1].lower() in NOTING_TOOLS for name, succeeded in tools)
    for s in re.split(r"(?<=[.!])\s+", text.lower()):
        if _SKIP.search(s):
            continue
        named = bool(names.search(s))
        changed = _CHANGED.search(s)
        if not memory_ok and (_REMEMBER.search(s) or (changed and _ABOUT_MEMORY.search(s))):
            return ACT
        if not noted_ok and _NOTED.search(s):
            return ACT
        loose = _REMEMBER_PASSIVE.search(s) or (named and _ACT_WEAK.search(s))
        if ACT not in ok and (_ACT_STRONG.search(s) or changed or (named and _ACT_ING.search(s))
                              or (not ok and loose)):
            return ACT
        if named and not ok and _STATE.search(s):
            return STATE
    return None


# A reply that ends on a promise to act: "Let me turn them on.", "I'll go ahead and switch it off",
# "Let me check.", "I'll remind you after work." Only verbs that need a tool count: "let me think of
# something" and "let me know" promise nothing a tool keeps. Act verbs want an act tool (a reminder is
# kept by follow_up's later or an extension's act tool); check verbs want any tool.
_PROMISE = re.compile(
    r"\b(?:let me|let's|i'll|i will|i'm going to|i am going to|i'm about to)"
    r"(?: just| quickly| now| go ahead and| try to)? "
    r"(?:(?P<act>turn|switch|set|dim|brighten|change|adjust|boot|start|wake|put|send|message|lock|unlock|open|"
    r"close|add|remove|delete|play|pause|stop|save|update|remind|make (?:it|them|the)|do (?:that|it|this))"
    r"|(?P<check>check|look (?:it |that |this )?(?:up|at|into)|find out|pull up|read))\b")
# A retry that says plainly it cannot, or has not checked, or asks: an answer, not a failure. Bare
# "isn't" or "don't" is not enough: "The lamp isn't on" is a state claim the judge's negation rule lets by.
_DECLINE = re.compile(r"\?|\b(?:can't|cannot|couldn't|could not|unable|not able|no such|not exposed|not available|"
                      r"not something|haven't|have not|don't have|doesn't have|don't see|don't know|not sure)\b")


def promise(sentence: str, tools: list[tuple[str, bool]]) -> str | None:
    """"act" or "state" for a promise to act or to look that no call of the turn keeps, else None.
    Questions and negations promise nothing ("Shall I turn them on?", "I won't switch it off")."""
    s = sentence.lower()
    if _SKIP.search(s) or not (m := _PROMISE.search(s)):
        return None
    if m.group("act"):
        return None if any(ok and tool_tier(n) == ACT for n, ok in tools) else ACT
    return None if tools else STATE


NOTE_MARK = "not said by the user"


def is_note(text: str) -> bool:
    """A user-role message the house wrote, not the user: an announcement or a guard retry."""
    return text.lstrip().startswith("[") and NOTE_MARK in text[:80]


def retry_note(verdict: str, said: str) -> str:
    """The one nudge a reply gets: what he was about to say without a call (held back, so the user
    did not hear it), and what to do about it."""
    quoted = said.strip().replace('"', "'")
    ask = ("Call the tool now, or say plainly that you cannot." if verdict == ACT else
           "Call a tool to check now, or say plainly that you have not checked.")
    return (f"[Automatic check, {NOTE_MARK}: you were about to say \"{quoted}\" but no tool was called, "
            f"so it was not said. {ask} Do not mention this note.]")


class ToolTurnObserver(BaseObserver):
    """Tool calls and their outcomes since the current user turn began, and those of the turn
    before. A reply may rest on the turn before: "What?" said while the lights were changing, or
    "is it done?" after a clone, gets an answer about calls that ran just before it. Notes the house
    adds as user messages (announcements, the guard's retry) do not start a turn."""

    def __init__(self):
        super().__init__()
        self.pending: set[str] = set()
        self.running: dict[str, str] = {}           # call id -> tool name, until its result
        self.calls: list[tuple[str, bool]] = []
        self.previous: list[tuple[str, bool]] = []
        self.started: list[str] = []                # names of the calls started this turn, in order
        self.turn = 0                               # counts user turns
        self.heard = ""                             # the last thing the user said, for the log
        self.responses: dict[int, list[str]] = {}   # response end frame id -> calls started in it
        self._response_start = 0
        self._new_turn = True
        self._seen: set[tuple[str, str]] = set()   # observers see a frame once per pipeline hop
        self._last_reset: int | None = None

    def current(self) -> list[tuple[str, bool]]:
        """The calls of this turn only, running ones included: what keeps a promise made now."""
        return self.calls + [(n, True) for n in self.running.values()]

    def backing(self) -> list[tuple[str, bool]]:
        """The calls a sentence may rest on now: finished ones of this turn and the one before, and
        the ones still running ("Turning off the lamp" while the call runs is true)."""
        return self.calls + self.previous + [(n, True) for n in self.running.values()]

    async def on_push_frame(self, data: FramePushed):
        f = data.frame
        if isinstance(f, FunctionCallsStartedFrame):
            for c in f.function_calls:
                if ("call", c.tool_call_id) not in self._seen:
                    self._seen.add(("call", c.tool_call_id))
                    self.pending.add(c.tool_call_id)
                    self.running[c.tool_call_id] = c.function_name
                    self.started.append(c.function_name)
        elif isinstance(f, FunctionCallResultFrame):
            if ("result", f.tool_call_id) in self._seen:
                return
            self._seen.add(("result", f.tool_call_id))
            self.pending.discard(f.tool_call_id)
            self.running.pop(f.tool_call_id, None)
            self.calls.append((f.function_name, result_ok(f.result)))
        elif isinstance(f, LLMFullResponseStartFrame):
            if ("start", f.id) not in self._seen:
                self._seen.add(("start", f.id))
                self._response_start = len(self.started)
        elif isinstance(f, LLMFullResponseEndFrame):
            if ("end", f.id) not in self._seen:     # seen before the guard processes it
                self._seen.add(("end", f.id))
                self.responses[f.id] = self.started[self._response_start:]
                self._response_start = len(self.started)
        elif isinstance(f, TranscriptionFrame):
            if ("heard", f.id) in self._seen:
                return
            self._seen.add(("heard", f.id))
            self.heard = f.text if self._new_turn else f"{self.heard} {f.text}"
            self._new_turn = False
        elif isinstance(f, UserStartedSpeakingFrame) or (
                isinstance(f, LLMMessagesAppendFrame)
                and any(_role(m) == "user" and not is_note(_content(m)) for m in f.messages)):
            # A new user turn: forget the previous turn's tools. Pipecat broadcasts UserStartedSpeakingFrame
            # as two frames (one each way) with their own ids; the second is the same turn.
            if self._last_reset is None or self._last_reset not in (f.id, f.broadcast_sibling_id):
                self._last_reset = f.id
                self.turn += 1
                self.pending.clear()
                self.running.clear()
                self.previous, self.calls, self.started = self.calls, [], []
                self._response_start = 0
                self._new_turn = True
                if isinstance(f, LLMMessagesAppendFrame):
                    self.heard = " ".join(_content(m) for m in f.messages if _role(m) == "user")
                    self._new_turn = False


def _role(message) -> str | None:
    return message.get("role") if isinstance(message, dict) else getattr(message, "role", None)


def _content(message) -> str:
    """A message's text, whether a plain string or a list of parts."""
    c = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return str(c or "")


# A sentence ends at . ! ? (and any closing quote or bracket) followed by white space, or at a line
# break. "10.6 million" does not end one; the last sentence ends with the response.
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*\s+|\n+")
_THINK_TAG = re.compile(r"</?think>")         # with thinking off the model sometimes writes one


class ClaimGuard(FrameProcessor):
    """Sits between the LLM and the TTS (or printer). Passes the reply on a sentence at a time,
    holds back claims no call backs, retries once per user turn and, when the retry fails too, says
    a soft line as part of the reply. `spoken` pipelines (a TTS and an output transport between the
    guard and the context) get the retry note upstream, so it does not wait for the audio; text
    pipelines get it downstream, behind the reply, so the context keeps its order."""

    def __init__(self, cfg: WinstonConfig, channel: str = "voice", spoken: bool = True):
        super().__init__()
        self.observer = ToolTurnObserver()
        self.retrying = False                      # a retry was asked for and has not answered yet
        self._channel, self._spoken = channel, spoken
        self._names = targets(cfg)
        self._log: Path = resolve(cfg.logging.tool_calls)
        self._buf = ""                             # text since the last sentence end
        self._held: list[str] = []                 # sentences held back in this response, in order
        self._released: list[str] = []            # what this response let through
        self._retried_turn = -1                    # the user turn whose retry is used up
        self._retry: dict | None = None            # the open retry: verdict, text, when, call mark

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self._buf, self._held, self._released = "", [], []
        elif isinstance(frame, LLMTextFrame) and direction == FrameDirection.DOWNSTREAM:
            self._buf += frame.text
            await self._sentences()
            return
        elif isinstance(frame, InterruptionFrame):
            self._interrupted()
        elif isinstance(frame, LLMFullResponseEndFrame):
            await self._end(frame)
            return
        await self.push_frame(frame, direction)

    async def _sentences(self):
        """Judges and passes on every finished sentence in the buffer; the rest waits."""
        while m := _SENTENCE_END.search(self._buf):
            sentence, self._buf = self._buf[:m.end()], self._buf[m.end():]
            await self._one(_THINK_TAG.sub("", sentence))

    async def _one(self, sentence: str):
        # A claim or a promise waits for the end of the response, where the calls it made are known
        # ("Let me turn it off." and then the call). Once one sentence is held the rest wait behind
        # it, so nothing is said out of order.
        obs = self.observer
        if self._held or judge(sentence, obs.backing(), self._names) or promise(sentence, obs.current()):
            self._held.append(sentence)
        else:
            await self._release(sentence)

    async def _release(self, text: str):
        self._released.append(text)
        await self.push_frame(LLMTextFrame(text=text))

    async def _end(self, frame: LLMFullResponseEndFrame):
        """The response is over: settle what is held, then retry, give up softly, or let it be."""
        obs = self.observer
        calls = obs.responses.pop(frame.id, [])
        final = not calls                          # after a call the LLM runs again on its result
        last = _THINK_TAG.sub("", self._buf)
        self._buf = ""
        if last.strip():
            self._held.append(last)
        tools = obs.backing()
        verdict, dropped = None, []
        # Held sentences are judged again with every call the response made; a claim made just
        # before its own call ("I'll switch it off." then the call) is kept. A promise counts only
        # when the response is the last: nothing will run after it.
        for i, s in enumerate(self._held):
            v = judge(s, tools, self._names) or (promise(s, obs.current()) if final else None)
            if v:
                verdict, dropped = v, self._held[i:]
                self._held = self._held[:i]
                break
        for s in self._held:
            await self._release(s)
        self._held = []
        said = "".join(dropped).strip()
        retry = self._retry
        if retry and retry["turn"] != obs.turn:    # the user moved on without interrupting
            self._record(retry["verdict"], retry["text"], retried=True, retry_backed=False, fallback=False,
                         abandoned=True)
            retry, self._retry, self.retrying = None, None, False
        if retry:                                  # this is the answer to the retry
            if not final:
                if verdict:
                    self._record(verdict, said, retried=True, fallback=False, during_retry=True)
                await self.push_frame(frame)
                return
            backed = any(tool_tier(n) == ACT or retry["verdict"] == STATE for n in obs.started[retry["mark"]:])
            text = "".join(self._released).strip()
            ok = verdict is None and (backed or bool(_DECLINE.search(text.lower())))
            if not ok:
                await self._release((" " if text else "") + SOFT[verdict or retry["verdict"]])
            self._record(retry["verdict"], retry["text"], retried=True, retry_backed=backed and verdict is None,
                         fallback=not ok, retry_ms=round((time.perf_counter() - retry["t0"]) * 1000),
                         retry_text=text, retry_dropped="".join(dropped).strip())
            self._retry, self.retrying = None, False
            await self.push_frame(frame)
            return
        if verdict is None:
            await self.push_frame(frame)
            return
        if not final:                              # a claim no call backs, before the calls finish
            self._record(verdict, said, retried=False, fallback=False)
            await self.push_frame(frame)
            return
        if self._retried_turn == obs.turn:         # the one retry of this turn is spent
            text = "".join(self._released).strip()
            await self._release((" " if text else "") + SOFT[verdict])
            self._record(verdict, said, retried=False, fallback=True)
            await self.push_frame(frame)
            return
        self._retried_turn = obs.turn
        self._retry = {"verdict": verdict, "text": said, "t0": time.perf_counter(), "mark": len(obs.started),
                       "turn": obs.turn}
        self.retrying = True                       # before the end goes on, so nothing closes the reply
        logger.warning(f"claim guard: {verdict} claim without a backing tool call, retrying: {said!r} "
                       f"(tools this turn: {obs.calls}, the turn before: {obs.previous})")
        await self.push_frame(frame)
        note = LLMMessagesAppendFrame(messages=[{"role": "user", "content": retry_note(verdict, said)}],
                                      run_llm=True)
        await self.push_frame(note, FrameDirection.UPSTREAM if self._spoken else FrameDirection.DOWNSTREAM)

    def _interrupted(self):
        """The user talked over him: what was held goes, and an open retry is abandoned."""
        self._buf, self._held = "", []
        if self._retry:
            r = self._retry
            self._record(r["verdict"], r["text"], retried=True, retry_backed=False, fallback=False,
                         interrupted=True, retry_ms=round((time.perf_counter() - r["t0"]) * 1000))
        self._retry, self.retrying = None, False

    def _record(self, verdict: str, text: str, **fields):
        obs = self.observer
        self._log.parent.mkdir(parents=True, exist_ok=True)
        with self._log.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "channel": self._channel, "event": "guard",
                                "verdict": verdict, "text": text, "heard": obs.heard[:200],
                                "tools": obs.calls, "previous": obs.previous, **fields}) + "\n")
