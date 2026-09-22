"""M2 headless round trip: push synthetic user speech through the real Pipecat pipeline
(STT -> context -> LLM -> TTS) without a browser, and measure end-of-speech -> first
audio. Needs vLLM (:8000) and the TTS server (:8880); do NOT run the pipeline server at
the same time on the GPU-light services (it is fine, they are shared HTTP servers).

    .venv/bin/python tests/m2_headless_roundtrip.py ["What's the capital of Sweden?" ...]

The "user" audio is produced by the TTS server in a different VCTK voice, resampled to
16 kHz, and fed as 20 ms InputAudioRawFrames between UserStarted/StoppedSpeaking frames,
mirroring what the transport's VAD emits.
"""
import asyncio
import statistics
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from loguru import logger
from pipecat.frames.frames import (Frame, LLMFullResponseEndFrame, LLMTextFrame, StartFrame,
                                   TranscriptionFrame, TTSAudioRawFrame, TTSStoppedFrame,
                                   UserAudioRawFrame, UserStartedSpeakingFrame,
                                   UserStoppedSpeakingFrame, VADUserStartedSpeakingFrame,
                                   VADUserStoppedSpeakingFrame)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voice import pipeline as vp  # noqa: E402  (loads config + STT model)

SR = 16000
CHUNK = SR // 50  # 20 ms


def synth_user_audio(text: str, voice: str = "vctk/p226_023") -> np.ndarray:
    pcm = httpx.post(f"{vp.CFG['tts']['server_url']}/audio/speech",
                     json={"input": text, "voice": voice, "response_format": "pcm"}, timeout=120).content
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n = int(len(a) * SR / 24000)                       # 24 kHz -> 16 kHz, linear interp is fine for STT
    a16 = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a)
    return a16.astype(np.int16)


class Speaker(FrameProcessor):
    """Injects one utterance as if the transport's VAD had segmented it."""
    def __init__(self, audio: np.ndarray):
        super().__init__(); self._audio = audio; self.t_stop = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            asyncio.get_event_loop().create_task(self._speak())

    async def _speak(self):
        await asyncio.sleep(0.5)
        # mirror what BaseInputTransport emits from the VAD: VAD frames, then the plain ones
        await self.push_frame(VADUserStartedSpeakingFrame())
        await self.push_frame(UserStartedSpeakingFrame())
        for i in range(0, len(self._audio), CHUNK):
            chunk = self._audio[i:i + CHUNK]
            await self.push_frame(UserAudioRawFrame(chunk.tobytes(), SR, 1))
            await asyncio.sleep(0.02)                  # real-time pacing like a mic
        self.t_stop = time.perf_counter()
        await self.push_frame(VADUserStoppedSpeakingFrame())
        await self.push_frame(UserStoppedSpeakingFrame())


class Tap(FrameProcessor):
    """Pass-through that records the first frame of a type and collects text.
    One sits after STT (the aggregator swallows transcriptions), one after the LLM
    (the TTS service consumes LLMTextFrames and re-emits them as TTSTextFrames)."""
    def __init__(self, frame_type):
        super().__init__(); self._type = frame_type; self.t_first = None; self.text = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, self._type):
            self.t_first = self.t_first or time.perf_counter(); self.text.append(frame.text)
        await self.push_frame(frame, direction)


class Listener(FrameProcessor):
    """Sits after TTS (where the transport would be) and records what came out and when."""
    def __init__(self, done: asyncio.Event):
        super().__init__(); self._done = done; self._llm_done = self._tts_done = False
        self.t_first_audio = None; self.audio_secs = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame):
            self.t_first_audio = self.t_first_audio or time.perf_counter()
            self.audio_secs += len(frame.audio) / 2 / frame.sample_rate
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._llm_done = True
        elif isinstance(frame, TTSStoppedFrame):
            self._tts_done = True
        if self._llm_done and self._tts_done and self.audio_secs > 0:   # no transport, so no BotStoppedSpeaking
            self._done.set()
        await self.push_frame(frame, direction)


async def one_turn(utterance: str) -> dict:
    audio = synth_user_audio(utterance)
    speaker, done = Speaker(audio), asyncio.Event()
    stt_tap, llm_tap, listener = Tap(TranscriptionFrame), Tap(LLMTextFrame), Listener(done)
    persona = (vp.ROOT / vp.CFG["persona"]["prompt"]).read_text().strip()
    context = LLMContext([{"role": "system", "content": persona}])
    agg = LLMContextAggregatorPair(context)
    pipeline = Pipeline([speaker, vp.build_stt(), stt_tap, agg.user(), vp.build_llm(), llm_tap,
                         vp.build_tts(), listener, agg.assistant()])
    task = PipelineTask(pipeline, params=PipelineParams(enable_metrics=True), idle_timeout_secs=None)
    runner = PipelineRunner(handle_sigint=False)
    run = asyncio.create_task(runner.run(task))
    try:
        await asyncio.wait_for(done.wait(), timeout=len(audio) / SR + 30)
    except asyncio.TimeoutError:
        logger.error("timed out waiting for the bot to finish speaking")
    await task.cancel(); await run
    r = lambda t: None if (t is None or speaker.t_stop is None) else round((t - speaker.t_stop) * 1000)
    return {"utterance": utterance, "heard": " ".join(stt_tap.text), "reply": "".join(llm_tap.text).strip(),
            "stt_ms": r(stt_tap.t_first), "first_text_ms": r(llm_tap.t_first),
            "first_audio_ms": r(listener.t_first_audio), "reply_audio_secs": round(listener.audio_secs, 2)}


async def main(utterances):
    results = []
    for u in utterances:
        res = await one_turn(u); results.append(res)
        print(f"\n> {u}\n  heard: {res['heard']!r}\n  reply: {res['reply']!r}\n  stt {res['stt_ms']} ms | "
              f"first LLM text {res['first_text_ms']} ms | FIRST AUDIO {res['first_audio_ms']} ms | "
              f"reply audio {res['reply_audio_secs']} s", flush=True)
    fa = [r["first_audio_ms"] for r in results if r["first_audio_ms"] is not None]
    if fa:
        print(f"\nend-of-speech -> first audio: median {statistics.median(fa):.0f} ms over {len(fa)} turns "
              f"(min {min(fa)}, max {max(fa)})")
    ok = len(fa) == len(results) and statistics.median(fa) < 2000
    print("PASS (M2 latency target: median < 2000 ms)" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    args = sys.argv[1:] or ["What's the capital of Sweden?", "Tell my friend a fun fact.",
                            "Can you dim the lights and turn on the TV?", "What time is it?"]
    sys.exit(asyncio.run(main(args)))
