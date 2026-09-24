"""Claim guard: plan §6 rule 4 as code. A reply that claims an action or a current state must be
backed by a tool call in the same user turn or the one before; otherwise the claim is retracted aloud and in the
context, so the model cannot build on it. Seen twice on 2026-09-23 with the 9B: "Setting the
lights now" with no call, then "already on at one hundred percent" built on its own earlier claim.

    guard = ClaimGuard(cfg, channel="voice")
    Pipeline([..., brain.llm, guard, tts, ...]), observers=[guard.observer, ...]

The observer keeps the tool bookkeeping because the function-call frames never travel downstream
past the LLM; the processor sees the reply text and speaks the correction."""
import json
import re
import time
from pathlib import Path

from loguru import logger
from pipecat.frames.frames import (Frame, FunctionCallResultFrame, FunctionCallsStartedFrame,
                                   LLMFullResponseEndFrame, LLMFullResponseStartFrame,
                                   LLMMessagesAppendFrame, LLMTextFrame, TranscriptionFrame,
                                   TTSSpeakFrame, UserStartedSpeakingFrame)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from winston.config import resolve
from winston.config.schema import WinstonConfig

READ, ACT = "read", "act"

CORRECTION = {
    ACT: "Correction: I did not actually do that, nothing was changed. Say it again and I will do it.",
    "state": "Correction: I did not actually check that. Ask again and I will look.",
}


READ_TOOLS = {"web_search", "ask_claude"}


def tool_tier(name: str) -> str:
    """Read tools only look (GetLiveContext, GetDateTime, get_sensor_readings, web_search,
    ask_claude); everything else acts."""
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
# "I'll remember that", "noted", "I've forgotten that", "I've updated it": a memory claim, which needs
# the memory tools (act tools). Bare "I remember" is recall from the prompt, not a claim.
_REMEMBER = re.compile(r"\bi(?:'ll| will|'ve| have)(?: just)? (?:remember|noted?|saved?|stored?|made a note)\b"
                       r"|\bi'll keep (?:that|it|this) in mind\b|^(?:got it, )?noted\b"
                       r"|\bi(?:'ve| have)(?: just)? forgotten\b"
                       r"|\bi(?:'ve| have)?(?: just)? (?:removed|deleted|updated)\b")
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
    "act" for a change claim no successful act tool backs, "state" for a state claim no
    successful tool backs, None when the reply is fine. names is targets(cfg)."""
    ok = {tool_tier(name) for name, succeeded in tools if succeeded}
    for s in re.split(r"(?<=[.!])\s+", text.lower()):
        if _SKIP.search(s):
            continue
        named = bool(names.search(s))
        loose = _REMEMBER_PASSIVE.search(s) or (named and _ACT_WEAK.search(s))
        if ACT not in ok and (_ACT_STRONG.search(s) or _REMEMBER.search(s) or (named and _ACT_ING.search(s))
                              or (not ok and loose)):
            return ACT
        if named and not ok and _STATE.search(s):
            return "state"
    return None


class ToolTurnObserver(BaseObserver):
    """Tool calls and their outcomes since the current user turn began, and those of the turn
    before. A reply may rest on the turn before: "What?" said while the lights were changing, or
    "is it done?" after a clone, gets an answer about calls that ran just before it."""

    def __init__(self):
        super().__init__()
        self.pending: set[str] = set()
        self.calls: list[tuple[str, bool]] = []
        self.previous: list[tuple[str, bool]] = []
        self.heard = ""                             # the last thing the user said, for the log
        self._new_turn = True
        self._seen: set[tuple[str, str]] = set()   # observers see a frame once per pipeline hop
        self._last_reset: int | None = None

    async def on_push_frame(self, data: FramePushed):
        f = data.frame
        if isinstance(f, FunctionCallsStartedFrame):
            for c in f.function_calls:
                if ("call", c.tool_call_id) not in self._seen:
                    self._seen.add(("call", c.tool_call_id))
                    self.pending.add(c.tool_call_id)
        elif isinstance(f, FunctionCallResultFrame):
            if ("result", f.tool_call_id) in self._seen:
                return
            self._seen.add(("result", f.tool_call_id))
            self.pending.discard(f.tool_call_id)
            self.calls.append((f.function_name, result_ok(f.result)))
        elif isinstance(f, TranscriptionFrame):
            if ("heard", f.id) in self._seen:
                return
            self._seen.add(("heard", f.id))
            self.heard = f.text if self._new_turn else f"{self.heard} {f.text}"
            self._new_turn = False
        elif isinstance(f, UserStartedSpeakingFrame) or (
                isinstance(f, LLMMessagesAppendFrame) and any(_role(m) == "user" for m in f.messages)):
            if f.id != self._last_reset:            # a new user turn: forget the previous turn's tools
                self._last_reset = f.id
                self.pending.clear()
                self.previous, self.calls = self.calls, []
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


class ClaimGuard(FrameProcessor):
    """Sits between the LLM and the TTS (or printer). Checks each finished reply against the
    turn's tool calls and, on an unbacked claim, speaks a correction. Over voice the spoken text
    lands in the context through the TTS; over text the correction is appended as an assistant
    message so the next turn sees it too."""

    def __init__(self, cfg: WinstonConfig, channel: str = "voice"):
        super().__init__()
        self.observer = ToolTurnObserver()
        self._channel = channel
        self._names = targets(cfg)
        self._log: Path = resolve(cfg.logging.tool_calls)
        self._text: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self._text = []
        elif isinstance(frame, LLMTextFrame):
            self._text.append(frame.text)
        await self.push_frame(frame, direction)
        if isinstance(frame, LLMFullResponseEndFrame):
            await self._check()

    async def _check(self):
        text = "".join(self._text).strip()
        self._text = []
        obs = self.observer
        if not text or obs.pending:                 # a call is still running: the reply is not final
            return
        verdict = judge(text, obs.calls + obs.previous, self._names)
        if verdict is None:
            return
        logger.warning(f"claim guard: {verdict} claim without a backing tool call: {text!r} "
                       f"(tools this turn: {obs.calls}, the turn before: {obs.previous})")
        self._log.parent.mkdir(parents=True, exist_ok=True)
        with self._log.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "channel": self._channel, "event": "guard",
                                "verdict": verdict, "text": text, "heard": obs.heard[:200],
                                "tools": obs.calls, "previous": obs.previous}) + "\n")
        correction = CORRECTION[verdict]
        await self.push_frame(TTSSpeakFrame(text=correction))
        if self._channel != "voice":                # no TTS to carry the words into the context
            await self.push_frame(LLMMessagesAppendFrame(
                messages=[{"role": "assistant", "content": correction}], run_llm=False))
