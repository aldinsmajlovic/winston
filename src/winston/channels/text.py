"""Text channel: the same brain and tools as voice, minus STT and TTS. Used by `winston talk`
and, later, Telegram and the web chat."""
import asyncio
import sys
import time

from loguru import logger
from pipecat.frames.frames import (Frame, FunctionCallResultFrame, FunctionCallsStartedFrame,
                                   LLMFullResponseEndFrame, LLMMessagesAppendFrame, LLMTextFrame,
                                   TTSSpeakFrame)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from winston.config.schema import WinstonConfig
from winston.core.brain import Brain, ToolCallLogObserver
from winston.core.guard import ClaimGuard

QUIET_SECS = 1.5   # a reply is over when no LLM activity follows its last full-response end


class TurnTracker(BaseObserver):
    """Knows when a reply is really over: no LLM response end for QUIET_SECS and no tool call
    still running. Observers see every frame, including the function-call frames that never
    travel downstream past the LLM."""
    def __init__(self):
        super().__init__(); self.last_end = None; self.pending: set[str] = set()

    async def on_push_frame(self, data: FramePushed):
        f = data.frame
        if isinstance(f, FunctionCallsStartedFrame):
            self.pending.update(c.tool_call_id for c in f.function_calls)
        elif isinstance(f, FunctionCallResultFrame):
            self.pending.discard(f.tool_call_id)
            self.last_end = None            # the LLM runs again on the result; a new response is coming
        elif isinstance(f, LLMFullResponseEndFrame):
            self.last_end = time.perf_counter()


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
        elif isinstance(frame, TTSSpeakFrame):        # the claim guard's correction
            if self._echo:
                print(f"\n{frame.text}", end="", flush=True)
            self.buf.append(f" {frame.text}")
        await self.push_frame(frame, direction)

    async def wait_reply(self, timeout=90) -> str:
        t0 = time.perf_counter(); self.buf = []; self._t.last_end = None
        while time.perf_counter() - t0 < timeout:
            await asyncio.sleep(0.1)
            t = self._t
            if t.last_end and not t.pending and time.perf_counter() - t.last_end > QUIET_SECS:
                break
        if self._echo:
            print()
        reply = "".join(self.buf).strip(); self.replies.append(reply); return reply


async def run(cfg: WinstonConfig, messages: list[str] | None, echo: bool = True) -> list[str]:
    """Scripted (messages given, one shared context) or interactive (None) chat."""
    brain = Brain(cfg, channel="text"); await brain.start()
    agg = LLMContextAggregatorPair(brain.new_context())
    tracker = TurnTracker(); printer = Printer(tracker, echo); guard = ClaimGuard(cfg, channel="text")
    worker = PipelineWorker(Pipeline([agg.user(), brain.llm, guard, printer, agg.assistant()]),
                            params=PipelineParams(enable_metrics=False),
                            observers=[guard.observer, ToolCallLogObserver(cfg, channel="text"), tracker],
                            idle_timeout_secs=None)
    run_task = asyncio.create_task(WorkerRunner(handle_sigint=False).run(worker))

    async def ask(m: str) -> str:
        await worker.queue_frames([LLMMessagesAppendFrame(messages=[{"role": "user", "content": m}], run_llm=True)])
        return await printer.wait_reply()

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
        await worker.cancel(); await run_task; await brain.close()
    return printer.replies


if __name__ == "__main__":
    from winston.config import load
    logger.remove(); logger.add(sys.stderr, level="WARNING")
    asyncio.run(run(load(), sys.argv[1:] or None))
