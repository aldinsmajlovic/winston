"""Text channel: the same brain and tools as voice, minus STT and TTS. Used by `winston talk`,
the Discord channel and the web chat; a satellite box runs the same session with its TTS and speaker
after it (channels/satellite.py)."""
import asyncio
import time
from collections.abc import Awaitable, Callable

import httpx
import openai
from loguru import logger
from pipecat.frames.frames import (ErrorFrame, Frame, FunctionCallResultFrame, FunctionCallsStartedFrame,
                                   LLMFullResponseEndFrame, LLMMessagesAppendFrame, LLMTextFrame)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from winston.config.schema import WinstonConfig
from winston.core.announce import note
from winston.core.brain import SPOKEN, Brain, Clock, ToolCallLogObserver
from winston.core.reply import ReplyFilter
from winston.core.rooms import RoomLink
from winston.core.speak_up import Mentions
from winston.extension_host.client import tools_changed

QUIET_SECS = 1.5   # a reply is over when no LLM activity follows its last full-response end
UNREACHABLE = "He could not reach his brain. It may be stopped or still starting."
FAILED = "Something went wrong while he answered. The log has the details."


def error_text(e: BaseException) -> str:
    """What a typed channel says when he could not answer: the brain unreachable anywhere in the chain of
    causes, or that something failed. The exception itself goes to the log."""
    seen: set[int] = set()
    cur: BaseException | None = e
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, (openai.APIConnectionError, openai.APITimeoutError, httpx.ConnectError)):
            return UNREACHABLE
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return FAILED


class TurnTracker(BaseObserver):
    """Knows when a reply is really over: no LLM response end for QUIET_SECS and no tool call still
    running. Observers see every frame, including the function-call frames that never travel
    downstream past the LLM."""
    def __init__(self):
        super().__init__(); self.last_end = None; self.pending: set[str] = set()
        self.error: BaseException | None = None

    @property
    def over(self) -> bool:
        return bool(self.last_end and not self.pending and time.perf_counter() - self.last_end > QUIET_SECS)

    async def on_push_frame(self, data: FramePushed):
        f = data.frame
        if isinstance(f, FunctionCallsStartedFrame):
            self.pending.update(c.tool_call_id for c in f.function_calls)
        elif isinstance(f, FunctionCallResultFrame):
            self.pending.discard(f.tool_call_id)
            self.last_end = None            # the LLM runs again on the result; a new response is coming
        elif isinstance(f, LLMFullResponseEndFrame):
            self.last_end = time.perf_counter()
        elif isinstance(f, ErrorFrame):     # the LLM's failure travels upstream, past no printer
            self.error = f.exception or RuntimeError(f.error)


class Printer(FrameProcessor):
    def __init__(self, tracker: TurnTracker, echo: bool = True):
        super().__init__(); self._t = tracker; self._echo = echo; self.buf = []; self.replies = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            text = frame.text.lstrip() if not self.buf else frame.text   # thinking mode leaves leading newlines
            if text:
                if self._echo:
                    print(text, end="", flush=True)
                self.buf.append(text)
        await self.push_frame(frame, direction)

    async def wait_reply(self, timeout=90) -> str:
        t0 = time.perf_counter(); self.buf = []; self._t.last_end = None
        while time.perf_counter() - t0 < timeout:
            await asyncio.sleep(0.1)
            if self._t.over or (self._t.error and not self.buf):
                break
        if self._echo:
            print()
        reply = "".join(self.buf).strip(); self.replies.append(reply); return reply


class TextSession:
    """One conversation over text: the brain, the reply filter and the tool log, minus STT and TTS.
    `winston talk` runs one; the Discord channel runs one per conversation. A spoken channel passes
    what comes after the printer (`outputs`: a TTS and where its audio goes) and its own `tools`.
    A check or trial passes `mentions=False`, so it does not take the household's
    nudges and briefing (a replay once took the day's briefing, and he never said it to anyone).

        s = TextSession(cfg, channel="discord"); await s.start()
        reply = await s.ask("turn on the lights"); await s.close()"""

    def __init__(self, cfg: WinstonConfig, channel: str = "text", echo: bool = False, observers: list | None = None,
                 room: RoomLink | None = None, area: str | None = None, brain_channel: str = "text",
                 outputs: list | None = None, tools: list | None = None, mentions: bool = True):
        self.cfg, self.channel, self.echo, self.room, self.area = cfg, channel, echo, room, area
        self.mentions = mentions
        self.brain_channel = brain_channel   # whose thinking the brain uses: a replayed spoken turn's own channel
        self._extra = observers or []
        self._outputs, self._tools = outputs or [], tools or []
        self.last_used = time.monotonic()
        self._lock = asyncio.Lock()          # one question at a time; a second waits its turn
        # Where a state-check correction goes besides the context and the terminal: a Discord DM, the
        # Talk page. Set by the channel that owns the session.
        self.notify: Callable[[str], Awaitable[None]] | None = None

    async def start(self):
        cfg = self.cfg
        self.brain = Brain(cfg, channel=self.brain_channel, spoken=self.channel in SPOKEN); await self.brain.start()
        self.brain.verifier.channel = self.channel          # its log says web or discord, not text
        for schema, handler in self._tools:
            self.brain.add_tool(schema, handler)
        agg = LLMContextAggregatorPair(self.brain.new_context())
        self._tracker = TurnTracker(); self.printer = Printer(self._tracker, self.echo)
        before, after = ([self.room.before], [self.room.after]) if self.room else ([], [])
        self._worker = PipelineWorker(Pipeline([agg.user(), Clock(self.area, cfg), *([Mentions(cfg)] if self.mentions else []), *before, self.brain.llm, ReplyFilter(),
                                                self.printer, *self._outputs, agg.assistant(), *after]),
                                      params=PipelineParams(enable_metrics=False),
                                      observers=[self.brain.verifier.observer,
                                                 ToolCallLogObserver(cfg, channel=self.channel),
                                                 self._tracker, *self._extra],
                                      idle_timeout_secs=None)
        self._task = asyncio.create_task(WorkerRunner(handle_sigint=False).run(self._worker))
        self.brain.verifier.deliver = self._correct
        return self

    async def _correct(self, text: str):
        """A state check found a device that did not do what he said (core/verify.py): into the
        context as his own words, so "try again" has it, and out to wherever this session shows replies."""
        await self._worker.queue_frames([LLMMessagesAppendFrame(messages=[{"role": "assistant", "content": text}],
                                                                run_llm=False)])
        if self.echo:
            print(f"\n{self.cfg.assistant.name}: {text}", flush=True)
        if self.notify:
            await self.notify(text)

    async def settle(self):
        """Until the state checks of the last reply are done and their corrections delivered."""
        await self.brain.verifier.drained()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def ask(self, m: str, timeout: float = 90) -> str:
        """The reply. Raises what the pipeline said when there is none (the brain unreachable), so no
        channel shows an empty answer without saying why."""
        async with self._lock:
            self.last_used = time.monotonic()
            self._tracker.error = None
            await self._worker.queue_frames([LLMMessagesAppendFrame(messages=[{"role": "user", "content": m}],
                                                                    run_llm=True)])
            reply = await self.printer.wait_reply(timeout)
            self.last_used = time.monotonic()
            if not reply and self._tracker.error:
                raise self._tracker.error
            return reply

    async def announced(self, text: str, context: dict):
        """An announcement delivered on this channel joins the conversation, so replies to it work."""
        await self.noted(note(text, context), text)

    async def noted(self, house_note: str, said: str):
        """Something he said on his own (an announcement, a nudge) joins the conversation: the house's
        note, then his words."""
        async with self._lock:
            self.last_used = time.monotonic()
            await self._worker.queue_frames([LLMMessagesAppendFrame(messages=[
                {"role": "user", "content": house_note}, {"role": "assistant", "content": said}],
                run_llm=False)])

    async def close(self):
        await self._worker.cancel(); await self._task; await self.brain.close()


async def stale(s: TextSession, idle_secs: float) -> bool:
    """Whether a channel should close this conversation and start a new one: not while it answers, and
    only once it has idled for idle_secs or the extension tools on offer changed (a conversation keeps
    the tools it started with). Checked when the conversation is next used."""
    if s.busy:
        return False
    if time.monotonic() - s.last_used > idle_secs:
        return True
    if changed := await tools_changed(s.brain):
        logger.info(f"{s.channel}: extension tools changed; a new conversation with them")
    return changed


async def run(cfg: WinstonConfig, messages: list[str] | None, echo: bool = True) -> list[str]:
    """Scripted (messages given, one shared context) or interactive (None) chat."""
    s = await TextSession(cfg, echo=echo).start()

    async def ask(m: str):
        try:
            await s.ask(m)
        except Exception as e:                                          # noqa: BLE001
            logger.exception("no reply")
            print(error_text(e), flush=True)
        await s.settle()
    try:
        if messages:
            for m in messages:
                if echo:
                    print(f"\n> {m}\n{cfg.assistant.name}: ", end="", flush=True)
                await ask(m)
        else:
            print(f"{cfg.assistant.name} is listening (Ctrl-D to quit).")
            while True:
                try:
                    m = await asyncio.to_thread(input, "\n> ")
                except EOFError:
                    break
                if m.strip():
                    print(f"{cfg.assistant.name}: ", end="", flush=True)
                    await ask(m)
    finally:
        await s.close()
    return s.printer.replies
