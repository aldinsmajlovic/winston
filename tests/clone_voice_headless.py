"""Clone a voice by asking, headless: the real voice pipeline (mic tap, STT, brain with its tools,
TTS) with a scripted microphone instead of a browser. Needs the LLM (:8000) and the TTS server (:8880).

    .venv/bin/python tests/clone_voice_headless.py [--keep] [--save-dir DIR]

The user asks in one VCTK voice to clone a friend; the "friend", another VCTK voice, says the
consent line and then talks, as audio only (no VAD frames, as a real mic under the tap sends
none); then the user asks for Winston's own voice back. Passes when the clone and its consent
are stored, Winston's next sentence starts within 2 s of the recording ending, and use_voice
reverts. The test clone is deleted afterwards unless --keep.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
from loguru import logger
from pipecat.frames.frames import (BotStartedSpeakingFrame, BotStoppedSpeakingFrame, Frame, StartFrame,
                                   TTSAudioRawFrame, TTSStoppedFrame, UserAudioRawFrame, UserStartedSpeakingFrame,
                                   UserStoppedSpeakingFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

sys.path.insert(0, str(Path(__file__).parent))
from m2_headless_roundtrip import CHUNK, SR, synth_user_audio, write_wav  # noqa: E402
from winston.channels import voice as vp  # noqa: E402
from winston.channels.clone import MicTap, make_voice_tools  # noqa: E402
from winston.config import resolve  # noqa: E402
from winston.core import voices  # noqa: E402
from winston.core.brain import Brain, ToolCallLogObserver  # noqa: E402

USER_VOICE, FRIEND_VOICE, FRIEND = "vctk/p226_023", "vctk/p243_023", "Maria"
PASSAGE = ("Well, today started slowly. I made coffee, read the news for a while and then walked down to the "
           "harbour, where the wind was cold but the light was lovely. After lunch I fixed the bike, which took "
           "far longer than it should have, and tonight I am cooking pasta for a few friends. That's about it.")


class Mic(FrameProcessor):
    """A microphone that never stops: 20 ms frames of silence, or of whatever say() queued. A user
    turn is framed by VAD frames like the aggregator's analyzer would; the friend's is audio only."""

    def __init__(self):
        super().__init__()
        self._queue: asyncio.Queue = asyncio.Queue()
        self.idle = asyncio.Event(); self.idle.set()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            asyncio.get_running_loop().create_task(self._run())

    def say(self, audio: np.ndarray, turn: bool):
        self.idle.clear(); self._queue.put_nowait((audio, turn))

    async def _run(self):
        silence = np.zeros(CHUNK, np.int16).tobytes()
        while True:
            try:
                audio, turn = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                self.idle.set()
                await self.push_frame(UserAudioRawFrame(silence, SR, 1)); await asyncio.sleep(0.02)
                continue
            if turn:
                await self.push_frame(VADUserStartedSpeakingFrame()); await self.push_frame(UserStartedSpeakingFrame())
            for i in range(0, len(audio), CHUNK):
                chunk = audio[i:i + CHUNK]
                if len(chunk) < CHUNK:
                    chunk = np.pad(chunk, (0, CHUNK - len(chunk)))
                await self.push_frame(UserAudioRawFrame(chunk.tobytes(), SR, 1)); await asyncio.sleep(0.02)
            if turn:
                await self.push_frame(VADUserStoppedSpeakingFrame()); await self.push_frame(UserStoppedSpeakingFrame())


class Speaker(FrameProcessor):
    """Where the transport output would be: plays TTS audio in real time and sends Bot started and
    stopped speaking upstream, which the mic tap waits on."""

    def __init__(self):
        super().__init__()
        self.until = 0.0
        self.speaking = False
        self.audio = bytearray()
        self.first_audio_after: float | None = None
        self.mark = 0.0
        self._stop_task: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame):
            now = time.perf_counter()
            if self.mark and self.first_audio_after is None:
                self.first_audio_after = now
            if not self.speaking:
                self.speaking = True
                await self.push_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
            self.until = max(self.until, now) + len(frame.audio) / 2 / frame.sample_rate
            self.audio += frame.audio
        elif isinstance(frame, TTSStoppedFrame) and self.speaking:
            if self._stop_task:
                self._stop_task.cancel()
            self._stop_task = asyncio.get_running_loop().create_task(self._stopped())
        await self.push_frame(frame, direction)

    async def _stopped(self):
        while (left := self.until - time.perf_counter()) > 0:
            await asyncio.sleep(left)
        self.speaking = False
        await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)


async def wait_for(cond, timeout: float, what: str):
    t0 = time.perf_counter()
    while not cond():
        if time.perf_counter() - t0 > timeout:
            raise TimeoutError(what)
        await asyncio.sleep(0.05)


def tool_calls(since: float) -> list[dict]:
    lines = resolve(vp.CFG.logging.tool_calls).read_text().splitlines()
    return [r for r in map(json.loads, lines) if r["ts"] >= since and r.get("channel") == "clone-test"]


async def main(keep: bool, save_dir: Path | None) -> int:
    ask, _ = synth_user_audio(f"Winston, clone my friend {FRIEND}'s voice.", USER_VOICE)
    consent, _ = synth_user_audio(f"I'm {FRIEND}, and I agree that Winston can copy my voice.", FRIEND_VOICE)
    talk, _ = synth_user_audio(PASSAGE, FRIEND_VOICE)
    back, _ = synth_user_audio("Thanks. Now go back to your own voice, please.", USER_VOICE)
    voices.delete(voices.slug(FRIEND))

    brain = Brain(vp.CFG, channel="voice"); await brain.start()
    stt, tts, tap, mic, speaker = vp.build_stt(), vp.build_tts(), MicTap(), Mic(), Speaker()

    async def transcribe(pcm):
        async for f in stt.run_stt(pcm.tobytes()):
            if f is not None and getattr(f, "text", None):
                yield f.text
    for schema, handler in make_voice_tools(vp.CFG, tap, transcribe):
        brain.add_tool(schema, handler)
    agg = LLMContextAggregatorPair(brain.new_context())
    pipeline = Pipeline([mic, tap, stt, agg.user(), brain.llm, tts, speaker, agg.assistant()])
    worker = PipelineWorker(pipeline, params=PipelineParams(), idle_timeout_secs=None,
                            observers=[ToolCallLogObserver(vp.CFG, channel="clone-test")])
    run = asyncio.create_task(WorkerRunner(handle_sigint=False).run(worker))
    t_start, ok = time.time(), True
    try:
        await asyncio.sleep(1)
        mic.say(ask, turn=True)
        await wait_for(lambda: tap._rec is not None, 30, "the consent recording never started")
        logger.info("test: friend says the consent line")
        mic.say(consent, turn=False)
        await wait_for(lambda: tap._rec is None, 20, "the consent recording never ended")
        await wait_for(lambda: tap._rec is not None, 30, "the talking recording never started")
        logger.info("test: friend talks")
        mic.say(talk, turn=False)
        await wait_for(lambda: tap._rec is None, 60, "the talking recording never ended")
        speaker.mark = time.perf_counter()
        await wait_for(lambda: speaker.first_audio_after is not None, 10, "no speech after the recording")
        first = (speaker.first_audio_after - speaker.mark) * 1000
        named = next((c["args"]["name"] for c in tool_calls(t_start) if c["tool"] == "clone_voice"), FRIEND)
        clone = voices.find(named)                  # the name as the STT heard it
        print(f"\nclone stored: {clone}\nfirst audio after the recording ended: {first:.0f} ms")
        ok &= clone is not None and (voices.friends_dir() / f"{clone.slug}.consent.wav").is_file() and first < 2000
        await wait_for(lambda: not speaker.speaking and not tap.muted, 30, "Winston kept talking")
        await asyncio.sleep(1)
        mic.say(back, turn=True)
        await wait_for(lambda: any(c["tool"] == "use_voice" and c["event"] == "result" for c in tool_calls(t_start)),
                       30, "use_voice was not called")
        await asyncio.sleep(1)
        await wait_for(lambda: not speaker.speaking, 30, "Winston kept talking")
    except TimeoutError as e:
        logger.error(f"test: {e}"); ok = False
    finally:
        await worker.cancel(); await run; await brain.close()
    calls = tool_calls(t_start)
    for c in calls:
        print(f"  {c['event']:6} {c['tool']} {c.get('args') or c.get('result')}")
    reverted = any(c["tool"] == "use_voice" and c["event"] == "call" and "own" in json.dumps(c["args"]).lower()
                   for c in calls)
    ok &= reverted
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
        write_wav(save_dir / "winston.wav", np.frombuffer(bytes(speaker.audio), np.int16))
        print(f"saved Winston's side to {save_dir}/winston.wav")
    if not keep:
        for c in calls:
            if c["tool"] == "clone_voice" and c["event"] == "call":
                voices.delete(voices.slug(c["args"].get("name", "")))
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    argv = sys.argv[1:]
    save = Path(argv[argv.index("--save-dir") + 1]) if "--save-dir" in argv else None
    sys.exit(asyncio.run(main("--keep" in argv, save)))
