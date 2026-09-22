"""Type at Winston. Same brain and tools as the voice pipeline, minus STT/TTS.

    .venv/bin/python -m brain.cli                        # interactive
    .venv/bin/python -m brain.cli "lights to 50%" "..."  # scripted, one context
"""
import asyncio
import sys
import time

from loguru import logger
from pipecat.frames.frames import (Frame, FunctionCallResultFrame, FunctionCallsStartedFrame,
                                   LLMFullResponseEndFrame, LLMMessagesAppendFrame, LLMTextFrame)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.workers.runner import WorkerRunner
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from brain.agent import Brain, ToolCallLogObserver

QUIET_SECS = 1.5   # a reply is over when no LLM activity follows its last full-response end


class TurnTracker(BaseObserver):
    """Knows when a reply is really over: no LLM response end for QUIET_SECS and no tool
    call still running. Observers see every frame, including the function-call frames
    that never travel downstream past the LLM, so the tracking lives here, not in Printer."""
    def __init__(self):
        super().__init__(); self.last_end = None; self.pending: set[str] = set()

    async def on_push_frame(self, data: FramePushed):
        f = data.frame
        if isinstance(f, FunctionCallsStartedFrame):
            self.pending.update(c.tool_call_id for c in f.function_calls)
        elif isinstance(f, FunctionCallResultFrame):
            self.pending.discard(f.tool_call_id)
        elif isinstance(f, LLMFullResponseEndFrame):
            self.last_end = time.perf_counter()


class Printer(FrameProcessor):
    def __init__(self, tracker: TurnTracker):
        super().__init__(); self._t = tracker; self.buf = []; self.replies = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            print(frame.text, end="", flush=True); self.buf.append(frame.text)
        await self.push_frame(frame, direction)

    async def wait_reply(self, timeout=90) -> str:
        t0 = time.perf_counter(); self.buf = []; self._t.last_end = None
        while time.perf_counter() - t0 < timeout:
            await asyncio.sleep(0.1)
            t = self._t
            if t.last_end and not t.pending and time.perf_counter() - t.last_end > QUIET_SECS:
                break
        print(); reply = "".join(self.buf).strip(); self.replies.append(reply); return reply


async def run(messages: list[str] | None) -> list[str]:
    brain = Brain(); await brain.start()
    context = brain.new_context(); agg = LLMContextAggregatorPair(context)
    tracker = TurnTracker(); printer = Printer(tracker)
    task = PipelineWorker(Pipeline([agg.user(), brain.llm, printer, agg.assistant()]),
                        params=PipelineParams(enable_metrics=False),
                        observers=[ToolCallLogObserver(channel="cli"), tracker], idle_timeout_secs=None)
    runner = WorkerRunner(handle_sigint=False); run_task = asyncio.create_task(runner.run(task))
    try:
        if messages:
            for m in messages:
                print(f"\n> {m}\nWinston: ", end="", flush=True)
                await task.queue_frames([LLMMessagesAppendFrame(messages=[{"role": "user", "content": m}], run_llm=True)])
                await printer.wait_reply()
        else:
            print("Winston is listening (Ctrl-D to quit).")
            while True:
                try:
                    m = await asyncio.to_thread(input, "\n> ")
                except EOFError:
                    break
                if not m.strip():
                    continue
                print("Winston: ", end="", flush=True)
                await task.queue_frames([LLMMessagesAppendFrame(messages=[{"role": "user", "content": m}], run_llm=True)])
                await printer.wait_reply()
    finally:
        await task.cancel(); await run_task; await brain.close()
    return printer.replies


if __name__ == "__main__":
    logger.remove(); logger.add(sys.stderr, level="WARNING")
    asyncio.run(run(sys.argv[1:] or None))
