"""Speaking up, the channels' side (docs/extensions.md#speaking-up). winstond's nudge engine decides
whether, when and where; a channel that speaks a nudge words it with its own brain (announce.nudge_note)
and says the plain line when the wording does not come.

- `Mentions` sits after `Clock` in every channel's pipeline: at each user turn it asks winstond for
  nudges delivered at the next conversation and the morning briefing, and adds them to the user's
  message, so he answers first and then mentions them.
- `follow_up` is the one core tool every nudge takes: later, dismiss, mute, why and the extension's
  own actions, all handled in winstond.
- `SpeechWatch` tells a channel whether he started speaking since a moment, for that fallback."""
from __future__ import annotations

import re
import time
from typing import Any

import httpx
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.frames.frames import Frame, LLMContextFrame, TTSStartedFrame
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import FunctionCallParams

from winston.config.schema import WinstonConfig
from winston.core.announce import MENTION_MARK, mention_note
from winston.core.guard import is_note

MENTIONS_TIMEOUT = 0.5          # a turn never waits longer than this for winstond
WORDING_SECS = 12.0             # a nudge he has not started saying by then gets its plain line


def _unstamped(text: str) -> str:
    """Without the clock's stamp, which goes in front of notes too."""
    return re.sub(r"^\[\d\d:\d\d(, room: [^\]]+)?\] ", "", text)


class Mentions(FrameProcessor):
    """Adds what to mention (next-talk nudges, the briefing) to the newest user message. The note stays
    in the history, so a follow-up has it and the prompt cache holds."""

    def __init__(self, cfg: WinstonConfig):
        super().__init__()
        self._url = cfg.control.url + "/api/nudges/mentions"
        self._client: httpx.AsyncClient | None = None
        self._seen: set[int] = set()

    async def _fetch(self, text: str) -> list[dict[str, Any]]:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=MENTIONS_TIMEOUT)
        try:
            r = await self._client.post(self._url, json={"text": text})
            return r.json() if r.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            return []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            msgs = frame.context.get_messages()
            last = msgs[-1] if msgs else None
            if (isinstance(last, dict) and last.get("role") == "user" and isinstance(last.get("content"), str)
                    and MENTION_MARK not in last["content"] and not is_note(_unstamped(last["content"]))
                    and id(last) not in self._seen):
                self._seen = {id(last)}
                t0 = time.perf_counter()
                items = await self._fetch(last["content"])
                if items:
                    last["content"] += mention_note(items)
                    logger.info(f"mentions: {len(items)} nudge(s) in {(time.perf_counter() - t0) * 1000:.0f} ms")
        await self.push_frame(frame, direction)

    async def cleanup(self):
        await super().cleanup()
        if self._client is not None:
            await self._client.aclose()


class SpeechWatch(BaseObserver):
    """When he last started speaking (a TTS run began)."""

    def __init__(self):
        super().__init__()
        self.last = 0.0

    async def on_push_frame(self, data: FramePushed):
        if isinstance(data.frame, TTSStartedFrame):
            self.last = time.monotonic()


FOLLOW_UP = FunctionSchema(
    name="follow_up",
    description="Answer a nudge he got when you spoke up (its note gives the nudge id). action: \"later\" puts it "
                "off (\"remind me after work\", \"snooze ten minutes\"): pass when in his words with the day he "
                "said (\"tomorrow after work\", \"in ten minutes\", \"Monday\", \"when I'm home\"), and reason: "
                "why, in his words. Only when memory says when that is (he finishes work at 17:00) put the clock "
                "time instead (\"tomorrow 17:00\"); never guess one. \"dismiss\": \"I know\", \"done\", "
                "\"thanks\". \"mute\": stop that kind of nudge, until when or for good. \"why\": which extension "
                "and why. Or an action the note names. Tell him the time it answers with.",
    properties={"nudge": {"type": "string", "description": "The nudge id from its note."},
                "action": {"type": "string", "description": "later, dismiss, mute, why, or an action the note names."},
                "when": {"type": "string", "description": "For later and mute: the time as he said it."},
                "reason": {"type": "string", "description": "For later: why, in his words (\"training tomorrow\")."}},
    required=["nudge", "action"])


def make_follow_up_tool(cfg: WinstonConfig, channel: str):
    base = cfg.control.url

    async def follow_up(params: FunctionCallParams):
        a = params.arguments if isinstance(params.arguments, dict) else {}
        nudge = str(a.get("nudge") or "").strip().strip('"')
        body = {"action": str(a.get("action") or "").strip().lower(), "when": a.get("when") or None,
                "reason": a.get("reason") or None, "channel": channel}
        try:
            async with httpx.AsyncClient(base_url=base, timeout=40) as c:
                r = await c.post(f"/api/nudges/{nudge}/follow_up", json=body)
                out = r.json() if r.status_code == 200 else {"error": r.text[:300]}
        except httpx.HTTPError as e:
            out = {"error": f"Speaking up could not be reached: {e}"}
        await params.result_callback(out)

    return FOLLOW_UP, follow_up


async def speaking_up(cfg: WinstonConfig) -> bool:
    """Whether any running extension has kinds of nudges, so the brain gets follow_up."""
    try:
        async with httpx.AsyncClient(base_url=cfg.control.url, timeout=3) as c:
            r = await c.get("/api/nudges/kinds")
            return r.status_code == 200 and r.json().get("kinds", 0) > 0
    except (httpx.HTTPError, ValueError):
        return False
