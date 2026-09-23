"""Claim guard: plan §6 rule 4 as code. A reply that claims an action or a current state must be
backed by a tool call in the same user turn; otherwise the claim is retracted aloud and in the
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
                                   LLMMessagesAppendFrame, LLMTextFrame, TTSSpeakFrame,
                                   UserStartedSpeakingFrame)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from winston.config import resolve
from winston.config.schema import WinstonConfig

READ, ACT = "read", "act"

CORRECTION = {
    ACT: "Correction: I did not actually do that, nothing was changed. Say it again and I will do it.",
    "state": "Correction: I did not actually check that. Ask again and I will look.",
}


def tool_tier(name: str) -> str:
    """Read tools only look (GetLiveContext, GetDateTime, get_sensor_readings); everything else acts."""
    return READ if name.split("__")[-1].lower().startswith("get") else ACT


def result_ok(result) -> bool:
    """HA's MCP server reports failures as a normal result string starting with "Error"."""
    if isinstance(result, dict):
        return not (result.get("error") or result.get("is_error"))
    return not str(result).lstrip().lower().startswith("error")


# Sentences that are questions, refusals or failure reports claim nothing.
_SKIP = re.compile(r"\?|\b(?:would you|do you want|shall i|should i|can't|cannot|couldn't|could not|"
                   r"unable|not able|didn't|did not|failed|not something)\b")
# "I set", "I'll set", "I'm going to set", "setting ... now", "has been set", "turned on", "done".
_ACT_STRONG = re.compile(
    r"\bi(?:'ve| have|'ll| will|'m| am)?(?: just| going to)? "
    r"(?:set|turned?|switch(?:ed)?|dimm?(?:ed)?|changed?|adjust(?:ed)?|boot(?:ed)?|start(?:ed)?|woke|wake|put)\b"
    r"|\b(?:setting|turning|switching|dimming|changing|adjusting|booting|waking)\b"
    r"|\b(?:has|have) been (?:set|turned|switched|dimmed|changed|adjusted|booted)\b"
    r"|\b(?:turned|switched) (?:on|off)\b|\bdone\b")
# "are now on", "now set to warm": a change claim unless a tool of any kind ran this turn.
_ACT_WEAK = re.compile(r"\b(?:is|are|'s|'re) now\b|\bnow (?:set|on|off|at|turned|dimmed|warm|cool|back)\b")
# "is already on", "are currently at": a state claim that needs some tool to have looked.
_STATE = re.compile(r"\b(?:is|are|'s|'re) (?:already|currently|still)\b|\bcurrently\b")


def judge(text: str, tools: list[tuple[str, bool]]) -> str | None:
    """Verdict for a reply given the (tool name, succeeded) calls of the same user turn:
    "act" for a change claim no successful act tool backs, "state" for a state claim no
    successful tool backs, None when the reply is fine."""
    ok = {tool_tier(name) for name, succeeded in tools if succeeded}
    for s in re.split(r"(?<=[.!])\s+", text.lower()):
        if _SKIP.search(s):
            continue
        if ACT not in ok and (_ACT_STRONG.search(s) or (not ok and _ACT_WEAK.search(s))):
            return ACT
        if not ok and _STATE.search(s):
            return "state"
    return None


class ToolTurnObserver(BaseObserver):
    """Tool calls and their outcomes since the current user turn began."""

    def __init__(self):
        super().__init__()
        self.pending: set[str] = set()
        self.calls: list[tuple[str, bool]] = []
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
        elif isinstance(f, UserStartedSpeakingFrame) or (
                isinstance(f, LLMMessagesAppendFrame) and any(_role(m) == "user" for m in f.messages)):
            if f.id != self._last_reset:            # a new user turn: forget the previous turn's tools
                self._last_reset = f.id
                self.pending.clear()
                self.calls.clear()


def _role(message) -> str | None:
    return message.get("role") if isinstance(message, dict) else getattr(message, "role", None)


class ClaimGuard(FrameProcessor):
    """Sits between the LLM and the TTS (or printer). Checks each finished reply against the
    turn's tool calls and, on an unbacked claim, speaks a correction. Over voice the spoken text
    lands in the context through the TTS; over text the correction is appended as an assistant
    message so the next turn sees it too."""

    def __init__(self, cfg: WinstonConfig, channel: str = "voice"):
        super().__init__()
        self.observer = ToolTurnObserver()
        self._channel = channel
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
        verdict = judge(text, obs.calls)
        if verdict is None:
            return
        logger.warning(f"claim guard: {verdict} claim without a backing tool call: {text!r} (tools this turn: {obs.calls})")
        self._log.parent.mkdir(parents=True, exist_ok=True)
        with self._log.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "channel": self._channel, "event": "guard",
                                "verdict": verdict, "text": text, "tools": obs.calls}) + "\n")
        correction = CORRECTION[verdict]
        await self.push_frame(TTSSpeakFrame(text=correction))
        if self._channel != "voice":                # no TTS to carry the words into the context
            await self.push_frame(LLMMessagesAppendFrame(
                messages=[{"role": "assistant", "content": correction}], run_llm=False))
