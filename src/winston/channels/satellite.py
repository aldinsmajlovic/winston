"""Satellite channel: Home Assistant's Voice Preview Edition (or any ESPHome voice satellite) as a room
microphone and speaker. Runs as its own process, started by the supervisor:

    python -m winston.channels.satellite

Winston takes the role Home Assistant normally has: an ESPHome API client (aioesphomeapi) that
subscribes to the box's voice assistant. The box hears its own wake word (microWakeWord: okay_nabu,
hey_jarvis, hey_mycroft on stock firmware) and then streams 16 kHz microphone audio here. Winston
decides when the request is over (Silero VAD), transcribes it, runs the brain with its tools and
the claim guard, and speaks. The box plays replies from a URL, as it does Home Assistant's
streaming TTS: RUN_START carries the URL, INTENT_PROGRESS with tts_start_streaming tells it to start
fetching once the first audio exists, and this process serves that URL as a WAV that grows while
the reply is synthesized. A reply ending in a question sets continue_conversation, so the box
listens again without the wake word.

Only one client can hold a box's voice assistant, first come first served (ESPHome's
voice_assistant.cpp): take the box out of Home Assistant's voice pipeline, or HA wins.
Turn based for now: the stock firmware stops listening while it plays (docs/voice-pe.md)."""
import argparse
import asyncio
import os
import socket
import struct
import time
import uuid
from dataclasses import dataclass, field

import numpy as np
import soxr
from aioesphomeapi import APIClient, ReconnectLogic, VoiceAssistantAudioSettings
from aioesphomeapi import VoiceAssistantEventType as Ev
from aiohttp import web
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (Frame, LLMMessagesAppendFrame, TTSAudioRawFrame, TTSSpeakFrame, TTSStartedFrame,
                                   TTSStoppedFrame, TTSTextFrame)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from winston.channels.text import QUIET_SECS, TurnTracker
from winston.channels.voice import LatencyLog, build_tts
from winston.config import load
from winston.config.registry import get as get_adapter
from winston.config.schema import SatelliteDevice, WinstonConfig
from winston.core.brain import Brain, ToolCallLogObserver
from winston.core.guard import ClaimGuard
from winston.core.live import FeedPoster, LiveEvent, LiveObserver

MIC_RATE = 16000                 # what the box streams
OUT_RATE = 48000                 # what the box's media player is set up for
VAD_FRAME = 512                  # Silero's window at 16 kHz
START_TIMEOUT_SECS = 8.0         # no speech after the wake word: give up
MAX_REQUEST_SECS = 20.0


def wav_header(rate: int) -> bytes:
    """A 16-bit mono WAV header for a stream of unknown length; the box's decoder plays until the
    connection closes."""
    big = 0x7FFFFFF0
    return (b"RIFF" + struct.pack("<I", big + 36) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", big))


def route_address(host: str) -> str:
    """This machine's address on the route to host: what the box can reach us at."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((socket.gethostbyname(host), 9))
        return s.getsockname()[0]


@dataclass
class Run:
    """One request on one box, from the wake word to the end of the reply."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    mic: asyncio.Queue = field(default_factory=asyncio.Queue)          # 16 kHz int16 chunks, None at the end
    out: asyncio.Queue = field(default_factory=asyncio.Queue)          # WAV body chunks, None at the end
    started: float = field(default_factory=time.perf_counter)
    first_audio: asyncio.Event = field(default_factory=asyncio.Event)
    speech_end: float = 0.0                                 # time.time() the request went quiet


class Speaker(FrameProcessor):
    """Where a transport would be: TTS audio goes to the current run's stream, resampled for the box,
    and the spoken text is kept for the TTS_START event."""

    def __init__(self, rate: int):
        super().__init__()
        self.rate, self.run, self.text, self.busy = rate, None, [], False
        self._resampler = soxr.ResampleStream(rate, OUT_RATE, 1, dtype="int16")

    def begin(self, run: Run):
        self.run, self.text = run, []
        self._resampler = soxr.ResampleStream(self.rate, OUT_RATE, 1, dtype="int16")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame) and self.run:
            pcm = self._resampler.resample_chunk(np.frombuffer(frame.audio, dtype="<i2"))
            if len(pcm):
                self.run.out.put_nowait(pcm.astype("<i2").tobytes())
                self.run.first_audio.set()
        elif isinstance(frame, TTSTextFrame) and self.run:
            self.text.append(frame.text)
        elif isinstance(frame, TTSStartedFrame):
            self.busy = True
        elif isinstance(frame, TTSStoppedFrame):
            self.busy = False
        await self.push_frame(frame, direction)


class Conversation:
    """One box's conversation: the brain with its tools, the claim guard and the TTS, kept between
    wake-ups until it idles out."""

    def __init__(self, cfg: WinstonConfig, room: str, feed: FeedPoster):
        self.cfg, self.room, self.feed = cfg, room, feed
        self.last_used = time.monotonic()

    async def start(self):
        cfg = self.cfg
        self.brain = Brain(cfg, channel="satellite"); await self.brain.start()
        agg = LLMContextAggregatorPair(self.brain.new_context())
        self.tracker = TurnTracker()
        self.speaker = Speaker(cfg.tts.sample_rate)
        guard = ClaimGuard(cfg, channel="satellite")
        self.latency = LatencyLog(cfg, "satellite")
        self.worker = PipelineWorker(
            Pipeline([agg.user(), self.brain.llm, guard, build_tts(cfg), self.speaker, agg.assistant()]),
            params=PipelineParams(enable_metrics=False), idle_timeout_secs=None,
            observers=[guard.observer, self.brain.verifier.observer, self.tracker,
                       ToolCallLogObserver(cfg, channel="satellite"),
                       LiveObserver("satellite", self.feed.put, spoken=True), self.latency])
        self._task = asyncio.create_task(WorkerRunner(handle_sigint=False).run(self.worker))
        self.brain.verifier.deliver = self.correct
        return self

    async def correct(self, text: str):
        """A state check found a device that did not do what he said (core/verify.py). Spoken into the
        reply that is still open, after what he said, without a chime. Without a transport the spoken
        text never reaches the context, so it goes in as his own words, as the claim guard's does."""
        rid = uuid.uuid4().hex[:12]
        self.feed.put(LiveEvent(channel="satellite", type="reply", reply=rid, text=text))
        self.feed.put(LiveEvent(channel="satellite", type="reply_end", reply=rid))
        if self.speaker.run is None:
            logger.warning(f"{self.room}: correction after the reply closed, not spoken: {text!r}")
        await self.worker.queue_frames([
            TTSSpeakFrame(text, append_to_context=False),
            LLMMessagesAppendFrame(messages=[{"role": "assistant", "content": text}], run_llm=False)])
        t0 = time.perf_counter()
        while not self.speaker.busy and time.perf_counter() - t0 < 5:       # wait for it to start...
            await asyncio.sleep(0.05)
        while self.speaker.busy and time.perf_counter() - t0 < 30:          # ...and to be spoken
            await asyncio.sleep(0.05)

    async def reply(self, text: str, run: Run, timeout: float = 90) -> str:
        """Speak the reply to text into run.out; returns what was said once the turn is over."""
        self.last_used = time.monotonic()
        self.speaker.begin(run)
        self.tracker.last_end = None
        await self.worker.queue_frames([LLMMessagesAppendFrame(
            messages=[{"role": "user", "content": text}], run_llm=True)])
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            await asyncio.sleep(0.1)
            t = self.tracker
            if t.last_end and not t.pending and time.perf_counter() - t.last_end > QUIET_SECS:
                break
        t1 = time.perf_counter()
        while self.speaker.busy and time.perf_counter() - t1 < 30:     # the last sentence is still being spoken
            await asyncio.sleep(0.05)
        await self._settle(run)
        self.speaker.run = None
        self.last_used = time.monotonic()
        return " ".join(s.strip() for s in self.speaker.text if s.strip())

    async def _settle(self, run: Run):
        """Keep the reply open while a state check is still running, so a correction is spoken into it.
        Only after an action whose device has not shown the change yet; the box hears silence meanwhile."""
        v = self.brain.verifier
        if v.idle:
            return
        silence = b"\0" * (OUT_RATE * 2 // 10)                                # 100 ms at the box's rate
        end = time.perf_counter() + v.wait_secs + 5
        while not v.idle and time.perf_counter() < end:
            if not self.speaker.busy and run.first_audio.is_set():
                run.out.put_nowait(silence)
            await asyncio.sleep(0.1)

    async def close(self):
        await self.worker.cancel(); await self._task; await self.brain.close()


class Satellite:
    """One box: the API connection, its voice assistant subscription and its runs."""

    def __init__(self, cfg: WinstonConfig, room: str, dev: SatelliteDevice, audio: "AudioServer",
                 stt, feed: FeedPoster):
        self.cfg, self.room, self.dev, self.audio, self.stt, self.feed = cfg, room, dev, audio, stt, feed
        key = os.environ.get(dev.key_env, "").strip() if dev.key_env else ""
        if dev.key_env and not key:
            raise RuntimeError(f"{dev.key_env} is not set (data/.env)")
        self.client = APIClient(dev.host, dev.port, None, noise_psk=key or None, client_info="Winston")
        self.run: Run | None = None
        self.task: asyncio.Task | None = None
        self.conversation: Conversation | None = None
        self._unsub = None
        self._vad = SileroVADAnalyzer(); self._vad.set_sample_rate(MIC_RATE)
        self._lock = asyncio.Lock()

    async def connect(self):
        self._reconnect = ReconnectLogic(client=self.client, on_connect=self._on_connect,
                                         on_disconnect=self._on_disconnect, name=self.dev.host)
        await self._reconnect.start()

    async def stop(self):
        await self._reconnect.stop()
        await self.client.disconnect()
        if self.conversation:
            await self.conversation.close()

    async def _on_connect(self):
        info = await self.client.device_info()
        logger.info(f"{self.room}: connected to {info.friendly_name or info.name} ({info.model}, ESPHome "
                    f"{info.esphome_version})")
        if self.dev.wake_word:
            try:
                conf = await self.client.get_voice_assistant_configuration(5)
                ids = [w.id for w in conf.available_wake_words]
                if self.dev.wake_word in ids:
                    await self.client.set_voice_assistant_configuration([self.dev.wake_word])
                    logger.info(f"{self.room}: wake word {self.dev.wake_word}")
                else:
                    logger.warning(f"{self.room}: no wake word {self.dev.wake_word!r} on the box; it has {ids}")
            except Exception as e:                                          # noqa: BLE001
                logger.warning(f"{self.room}: could not set the wake word: {e}")
        if self.conversation is None:            # ready before the first wake word, prompt already read
            self.conversation = await Conversation(self.cfg, self.room, self.feed).start()
            await self.conversation.brain.warm()
        self._unsub = self.client.subscribe_voice_assistant(
            handle_start=self._handle_start, handle_stop=self._handle_stop, handle_audio=self._handle_audio,
            handle_announcement_finished=self._handle_announcement_finished)

    async def _on_disconnect(self, expected: bool):
        logger.info(f"{self.room}: disconnected{'' if expected else ' unexpectedly'}")
        self._unsub = None
        if self.task:
            self.task.cancel()

    def _event(self, ev: Ev, data: dict[str, str] | None = None):
        try:
            self.client.send_voice_assistant_event(ev, data)
        except Exception as e:                                              # noqa: BLE001
            logger.warning(f"{self.room}: could not send {ev.name}: {e}")

    async def _handle_start(self, conversation_id: str, flags: int, settings: VoiceAssistantAudioSettings,
                            wake_word: str | None) -> int | None:
        if self.task and not self.task.done():
            self.task.cancel()
        self.run = Run()
        self.audio.runs[self.run.id] = self.run
        logger.info(f"{self.room}: wake word {wake_word!r}, run {self.run.id}")
        self.task = asyncio.create_task(self._serve(self.run))
        return 0                      # 0: audio over the API connection, not UDP

    async def _handle_audio(self, data: bytes, data2: bytes | None):
        if self.run:
            self.run.mic.put_nowait(np.frombuffer(data, dtype="<i2"))

    async def _handle_stop(self, abort: bool):
        if abort and self.task:
            self.task.cancel()
        elif self.run:
            self.run.mic.put_nowait(None)

    async def _handle_announcement_finished(self, finished):
        pass

    async def _listen(self, run: Run) -> np.ndarray | None:
        """Microphone audio until the request is over: end_silence_secs of quiet after speech."""
        buf, voiced = np.zeros(0, np.int16), []
        end = int(self.cfg.channels.satellite.end_silence_secs * MIC_RATE / VAD_FRAME)
        t0 = time.perf_counter()
        while True:
            timeout = max(0.05, (START_TIMEOUT_SECS if not any(voiced) else MAX_REQUEST_SECS) - (time.perf_counter() - t0))
            try:
                chunk = await asyncio.wait_for(run.mic.get(), timeout)
            except TimeoutError:
                chunk = None
            if chunk is None:
                break
            buf = np.concatenate([buf, chunk])
            while (len(voiced) + 1) * VAD_FRAME <= len(buf):
                i = len(voiced) * VAD_FRAME
                voiced.append(self._vad.voice_confidence(buf[i:i + VAD_FRAME].tobytes()) >= 0.5)
            if any(voiced) and len(voiced) - 1 - max(i for i, v in enumerate(voiced) if v) >= end:
                break
        if sum(voiced) * VAD_FRAME / MIC_RATE < 0.25:
            return None
        last = max(i for i, v in enumerate(voiced) if v)
        run.speech_end = time.time() - (len(buf) - (last + 1) * VAD_FRAME) / MIC_RATE
        return buf

    async def _transcribe(self, pcm: np.ndarray) -> str:
        cls, model = self.stt
        t0 = time.perf_counter()
        text = await asyncio.to_thread(cls.transcribe, model, pcm, MIC_RATE)
        logger.info(f"{self.room}: STT {len(pcm) / MIC_RATE:.2f}s -> {text!r} in {(time.perf_counter() - t0) * 1000:.0f} ms")
        return text

    async def _conversation(self) -> Conversation:
        c = self.conversation
        if c and time.monotonic() - c.last_used > self.cfg.channels.satellite.session_idle_mins * 60:
            await c.close(); c = None
        if c is None:
            c = self.conversation = await Conversation(self.cfg, self.room, self.feed).start()
        return c

    async def _serve(self, run: Run):
        url = f"{self.audio.base_url(self.dev.host)}/reply/{run.id}.wav"
        try:
            async with self._lock:
                self._event(Ev.VOICE_ASSISTANT_RUN_START, {"url": url})
                self._event(Ev.VOICE_ASSISTANT_STT_START)
                self.feed.put(LiveEvent(channel="satellite", type="state", state="listening"))
                pcm = await self._listen(run)
                self._event(Ev.VOICE_ASSISTANT_STT_VAD_END)          # the box stops its mic and waits
                heard = await self._transcribe(pcm) if pcm is not None else ""
                t_heard = time.perf_counter()
                logger.info(f"{self.room}: heard {heard!r}")
                if not heard:
                    self._event(Ev.VOICE_ASSISTANT_ERROR, {"code": "stt-no-text-recognized",
                                                           "message": "No speech recognized"})
                    self._event(Ev.VOICE_ASSISTANT_RUN_END)
                    self.feed.put(LiveEvent(channel="satellite", type="state", state="idle"))
                    return
                self._event(Ev.VOICE_ASSISTANT_STT_END, {"text": heard})
                self._event(Ev.VOICE_ASSISTANT_INTENT_START)
                conv = await self._conversation()
                conv.latency.begin(run.speech_end, round((time.time() - run.speech_end) * 1000))

                async def stream_when_ready():
                    await run.first_audio.wait()
                    self._event(Ev.VOICE_ASSISTANT_INTENT_PROGRESS, {"tts_start_streaming": "1"})
                    logger.info(f"{self.room}: first audio {(time.perf_counter() - t_heard) * 1000:.0f} ms after "
                                f"the transcript")
                progress = asyncio.create_task(stream_when_ready())
                said = await conv.reply(heard, run)
                conv.latency.flush()
                progress.cancel()
                run.out.put_nowait(None)
                follow_up = said.rstrip().endswith("?")
                self._event(Ev.VOICE_ASSISTANT_INTENT_END, {"conversation_id": self.room,
                                                            "continue_conversation": "1" if follow_up else "0"})
                self._event(Ev.VOICE_ASSISTANT_TTS_START, {"text": said or " "})
                self._event(Ev.VOICE_ASSISTANT_TTS_END, {"url": url})
                self._event(Ev.VOICE_ASSISTANT_RUN_END)
                logger.info(f"{self.room}: said {said!r}")
        except asyncio.CancelledError:
            run.out.put_nowait(None)
            raise
        except Exception as e:                                              # noqa: BLE001
            logger.exception(f"{self.room}: run failed")
            run.out.put_nowait(None)
            self._event(Ev.VOICE_ASSISTANT_ERROR, {"code": "winston-error", "message": str(e)[:200]})
            self._event(Ev.VOICE_ASSISTANT_RUN_END)
        finally:
            self.audio.forget(run.id, after=120)


class AudioServer:
    """Serves each run's reply as a WAV the box fetches while it is still being spoken."""

    def __init__(self, cfg: WinstonConfig):
        self.cfg = cfg
        self.runs: dict[str, Run] = {}

    def base_url(self, device_host: str) -> str:
        s = self.cfg.channels.satellite
        return f"http://{s.public_host or route_address(device_host)}:{s.http_port}"

    def forget(self, run_id: str, after: float):
        asyncio.get_running_loop().call_later(after, self.runs.pop, run_id, None)

    async def reply(self, request: web.Request) -> web.StreamResponse:
        run = self.runs.get(request.match_info["run"])
        if run is None:
            raise web.HTTPNotFound()
        resp = web.StreamResponse(headers={"Content-Type": "audio/wav", "Cache-Control": "no-store"})
        await resp.prepare(request)
        await resp.write(wav_header(OUT_RATE))
        while (chunk := await run.out.get()) is not None:
            await resp.write(chunk)
        run.out.put_nowait(None)           # a second fetch of the same URL ends too
        return resp

    async def start(self):
        app = web.Application()
        app.router.add_get("/health", lambda r: web.json_response({"ok": True}))
        app.router.add_get("/reply/{run}.wav", self.reply)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.cfg.channels.satellite.http_port).start()


async def main():
    cfg = load()
    s = cfg.channels.satellite
    if not s.devices:
        raise SystemExit("no satellites in channels.satellite.devices")
    feed = FeedPoster(cfg)
    audio = AudioServer(cfg)
    await audio.start()
    cls = get_adapter("stt", cfg.stt.provider)
    stt = (cls, cls.load_model(cfg.stt.model, cfg.stt.quantization))
    sats = [Satellite(cfg, room, dev, audio, stt, feed) for room, dev in s.devices.items()]
    for sat in sats:
        await sat.connect()
    logger.info(f"satellites: {', '.join(s.devices)}; replies served on :{s.http_port}")
    await asyncio.Event().wait()


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    asyncio.run(main())
