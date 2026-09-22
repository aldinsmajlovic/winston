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
from pipecat.workers.runner import WorkerRunner
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from winston.channels import voice as vp  # noqa: E402  (loads config)
from winston.core.brain import Brain, ToolCallLogObserver  # noqa: E402

SR = 16000
CHUNK = SR // 50  # 20 ms


def synth_user_audio(text: str, voice: str = "vctk/p226_023") -> tuple[np.ndarray, np.ndarray]:
    """Returns (16 kHz int16 for the pipeline, original 24 kHz int16 for saving)."""
    pcm = httpx.post(f"{vp.CFG.tts.base_url}/audio/speech",
                     json={"input": text, "voice": voice, "response_format": "pcm"}, timeout=120).content
    a24 = np.frombuffer(pcm, dtype=np.int16)
    a = a24.astype(np.float32)
    n = int(len(a) * SR / 24000)                       # 24 kHz -> 16 kHz, linear interp is fine for STT
    a16 = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a)
    return a16.astype(np.int16), a24


def write_wav(path: Path, pcm: np.ndarray, sr: int = 24000):
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.astype(np.int16).tobytes())


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
        self.t_first_audio = None; self.audio_secs = 0.0; self.audio = bytearray()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame):
            self.t_first_audio = self.t_first_audio or time.perf_counter()
            self.audio_secs += len(frame.audio) / 2 / frame.sample_rate
            self.audio += frame.audio
        elif isinstance(frame, LLMFullResponseEndFrame):
            self._llm_done = True
        elif isinstance(frame, TTSStoppedFrame):
            self._tts_done = True
        if self._llm_done and self._tts_done and self.audio_secs > 0:   # no transport, so no BotStoppedSpeaking
            self._done.set()
        await self.push_frame(frame, direction)


async def one_turn(utterance: str) -> dict:
    audio, user24 = synth_user_audio(utterance)
    speaker, done = Speaker(audio), asyncio.Event()
    stt_tap, llm_tap, listener = Tap(TranscriptionFrame), Tap(LLMTextFrame), Listener(done)
    brain = Brain(vp.CFG, channel="voice"); await brain.start()   # same brain as the real pipeline, tools included
    agg = LLMContextAggregatorPair(brain.new_context())
    pipeline = Pipeline([speaker, vp.build_stt(), stt_tap, agg.user(), brain.llm, llm_tap,
                         vp.build_tts(), listener, agg.assistant()])
    task = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True), idle_timeout_secs=None,
                          observers=[ToolCallLogObserver(vp.CFG, channel="headless")])
    runner = WorkerRunner(handle_sigint=False)
    run = asyncio.create_task(runner.run(task))
    try:
        await asyncio.wait_for(done.wait(), timeout=len(audio) / SR + 30)
    except asyncio.TimeoutError:
        logger.error("timed out waiting for the bot to finish speaking")
    await task.cancel(); await run; await brain.close()
    r = lambda t: None if (t is None or speaker.t_stop is None) else round((t - speaker.t_stop) * 1000)
    return {"utterance": utterance, "heard": " ".join(stt_tap.text), "reply": "".join(llm_tap.text).strip(),
            "stt_ms": r(stt_tap.t_first), "first_text_ms": r(llm_tap.t_first),
            "first_audio_ms": r(listener.t_first_audio), "reply_audio_secs": round(listener.audio_secs, 2),
            "user_pcm": user24, "reply_pcm": np.frombuffer(bytes(listener.audio), dtype=np.int16)}


async def main(utterances, save_dir: Path | None = None):
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
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
        gap, pause = np.zeros(24000 * 8 // 10, np.int16), np.zeros(24000 * 4 // 10, np.int16)
        convo = []
        for i, res in enumerate(results, 1):
            turn = np.concatenate([res["user_pcm"], pause, res["reply_pcm"]])
            write_wav(save_dir / f"turn{i}.wav", turn); convo += [turn, gap]
        write_wav(save_dir / "conversation.wav", np.concatenate(convo))
        print(f"saved {len(results)} turns + conversation.wav to {save_dir}")
    return 0 if ok else 1


if __name__ == "__main__":
    argv = sys.argv[1:]; save = None
    if "--save-dir" in argv:
        i = argv.index("--save-dir"); save = Path(argv[i + 1]); del argv[i:i + 2]
    args = argv or ["What's the capital of Sweden?", "Tell my friend a fun fact.",
                    "Can you dim the lights and turn on the TV?", "What time is it?"]
    sys.exit(asyncio.run(main(args, save)))
