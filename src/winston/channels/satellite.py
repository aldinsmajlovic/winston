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
the reply is synthesized. Every reply sets continue_conversation, so the box
listens again without the wake word: follow_up_secs for more (8 s after a question); silence then
ends the run as a wake with nothing said does, and so does a dismissal ("stop", "that's all").

A long press on the box's button switches it to button only and back: the wake word goes off, a press
starts a request and he never listens after a reply. The box keeps the choice (its wake words are
saved on it), so an empty wake word list on connect means button only.

Each box's key in config is its room. The box's conversation is the room's, shared with browser
sessions set to that room, and a wake holds the room's mic until the reply has played, so a browser
in the room neither answers too nor hears his voice as a turn (core/rooms.py).

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
from aioesphomeapi import APIClient, Event, EventInfo, ReconnectLogic, SelectInfo, VoiceAssistantAudioSettings
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

from winston.channels.clone import make_use_voice_tool
from winston.channels.text import TurnTracker
from winston.channels.voice import LatencyLog, build_tts
from winston.config import load
from winston.config.registry import get as get_adapter
from winston.config.schema import SatelliteDevice, WinstonConfig
from winston.core.brain import Brain, Clock, ToolCallLogObserver
from winston.core.guard import ClaimGuard
from winston.core.hints import Hearing
from winston.core.live import FeedPoster, LiveEvent, LiveObserver
from winston.core.rooms import MicLease, RoomLink, area, dismissal, http_sync

MIC_RATE = 16000                 # what the box streams
OUT_RATE = 48000                 # what the box's media player is set up for
VAD_FRAME = 512                  # Silero's window at 16 kHz
START_TIMEOUT_SECS = 8.0         # no speech after the wake word: give up (also the least after a question)
FOLLOW_WAIT_SECS = 20.0          # a follow-up run must start this soon after the reply has played
MAX_REQUEST_SECS = 20.0
HOLD_SECS = 15.0                 # the room's mic lease while a run is on, renewed every RENEW_SECS
RENEW_SECS = 5.0
TAIL_SECS = 1.5                  # after the reply has played: the box's speaker and the room settle
FOLLOW_UP_SECS = 4.0             # after a question: until the box starts listening again
# Said on the box after a long press switched how it listens.
BUTTON_ONLY_LINE = "Very well. I shall listen only when you press the button."
WAKE_WORD_LINE = "Very well. I am listening for my name again."


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
    audio_secs: float = 0.0                                 # reply audio sent to the box so far
    audio_start: float = 0.0                                # perf_counter() of the first of it
    follow_up: bool = False                                 # started by the box after a reply, no wake word
    start_timeout: float = START_TIMEOUT_SECS               # no speech this long after the start: give up

    def playing_for(self) -> float:
        """Seconds of the reply the box has yet to play, if it plays from its first audio in real time."""
        return max(0.0, self.audio_secs - (time.perf_counter() - self.audio_start)) if self.audio_start else 0.0


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
                if not self.run.first_audio.is_set():
                    self.run.audio_start = time.perf_counter()
                    self.run.first_audio.set()
                self.run.audio_secs += len(pcm) / OUT_RATE
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
        self.link = RoomLink(room, f"box:{room}:{uuid.uuid4().hex[:6]}", http_sync(cfg))

    async def start(self):
        cfg = self.cfg
        self.brain = Brain(cfg, channel="satellite"); await self.brain.start()
        self.brain.add_tool(*make_use_voice_tool(cfg, channel="satellite"))   # a voice cloned in the browser
        agg = LLMContextAggregatorPair(self.brain.new_context())
        guard = ClaimGuard(cfg, channel="satellite")
        self.tracker = TurnTracker(guard)             # a reply is not over while the guard retries
        self.speaker = Speaker(cfg.tts.sample_rate)
        self.latency = LatencyLog(cfg, "satellite")
        self.worker = PipelineWorker(
            Pipeline([agg.user(), Clock(area(cfg, self.room)), self.link.before, self.brain.llm, guard, build_tts(cfg),
                      self.speaker, agg.assistant(), self.link.after]),
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
        text never reaches the context, so it goes in as his own words."""
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
            if self.tracker.over:
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
                run.audio_secs += 0.1
            await asyncio.sleep(0.1)

    async def close(self):
        await self.worker.cancel(); await self._task; await self.brain.close()


class Satellite:
    """One box: the API connection, its voice assistant subscription and its runs."""

    def __init__(self, cfg: WinstonConfig, room: str, dev: SatelliteDevice, audio: "AudioServer",
                 stt: Hearing, feed: FeedPoster):
        self.cfg, self.room, self.dev, self.audio, self.stt, self.feed = cfg, room, dev, audio, stt, feed
        key = os.environ.get(dev.key_env, "").strip() if dev.key_env else ""
        if dev.key_env and not key:
            raise RuntimeError(f"{dev.key_env} is not set (data/.env)")
        self.client = APIClient(dev.host, dev.port, None, noise_psk=key or None, client_info="Winston")
        self.run: Run | None = None
        self._follow_until = 0.0            # monotonic time until which a start without a wake word is a follow-up
        self._follow_secs = START_TIMEOUT_SECS
        self.task: asyncio.Task | None = None
        self.conversation: Conversation | None = None
        self._unsub = None
        self._vad = SileroVADAnalyzer(); self._vad.set_sample_rate(MIC_RATE)
        self._lock = asyncio.Lock()
        self.mic = MicLease(cfg, room)
        self.button_only = False            # no wake word on the box: a press starts a request, no follow-ups
        self._wake_words: list[str] = []    # what a long press switches back on
        self._long_press: int | None = None  # key of the box's button event entity
        self._switching: asyncio.Task | None = None

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
        if self.conversation is None:            # ready before the first wake word, prompt already read
            self.conversation = await Conversation(self.cfg, self.room, self.feed).start()
            await self.conversation.brain.warm()
        self._unsub = self.client.subscribe_voice_assistant(
            handle_start=self._handle_start, handle_stop=self._handle_stop, handle_audio=self._handle_audio,
            handle_announcement_finished=self._handle_announcement_finished)
        try:                                     # after subscribing: the box answers only its voice client
            conf = await self.client.get_voice_assistant_configuration(5)
        except Exception as e:                                              # noqa: BLE001
            logger.warning(f"{self.room}: could not read the wake word configuration: {e}")
            conf = None
        if conf is not None:
            ids = [w.id for w in conf.available_wake_words]
            wanted = [w.strip() for w in (self.dev.wake_word or "").split(",") if w.strip()]
            if missing := [w for w in wanted if w not in ids]:
                logger.warning(f"{self.room}: no wake word {', '.join(missing)} on the box; it has {ids}")
            have = [w for w in wanted if w in ids]
            if len(have) > (most := max(conf.max_active_wake_words, 1)):
                logger.warning(f"{self.room}: the box runs {most} wake word(s) at a time; "
                               f"not {', '.join(have[most:])}")
            use = have[:most]
            self._wake_words = use or list(conf.active_wake_words) or ids[:1]
            self.button_only = not conf.active_wake_words
            if self.button_only:
                logger.info(f"{self.room}: button only (no wake word on); a long press switches the wake word on")
            elif use:
                await self.client.set_voice_assistant_configuration(use)
                logger.info(f"{self.room}: wake word {', '.join(use)}")
        try:
            ents, _ = await self.client.list_entities_services()
            self._long_press = next((e.key for e in ents if isinstance(e, EventInfo) and "long_press" in e.event_types),
                                    None)
            if self._long_press is not None:
                self.client.subscribe_states(self._on_state)
            if want := self.dev.wake_sensitivity:
                sel = next((e for e in ents if isinstance(e, SelectInfo) and e.object_id == "wake_word_sensitivity"),
                           None)
                if sel is None or want not in sel.options:
                    logger.warning(f"{self.room}: the box has no wake word sensitivity {want!r}")
                else:
                    self.client.select_command(sel.key, want)
                    logger.info(f"{self.room}: wake word sensitivity {want.lower()}")
        except Exception as e:                                              # noqa: BLE001
            logger.warning(f"{self.room}: no button events or sensitivity: {e}")

    def _on_state(self, state):
        long_press = isinstance(state, Event) and state.key == self._long_press and state.event_type == "long_press"
        if long_press and (self._switching is None or self._switching.done()):
            self._switching = asyncio.create_task(self.switch_listening())

    async def switch_listening(self):
        """A long press: the wake word off (button only) or back on, and a line on the box to say which."""
        if self.button_only and not self._wake_words:
            logger.warning(f"{self.room}: long press, but no wake word to switch on")
            return
        self.button_only = not self.button_only
        self._follow_until = 0.0
        try:
            await self.client.set_voice_assistant_configuration([] if self.button_only else self._wake_words)
        except Exception as e:                                              # noqa: BLE001
            logger.warning(f"{self.room}: could not switch the wake word: {e}")
            self.button_only = not self.button_only
            return
        logger.info(f"{self.room}: long press, " + ("button only" if self.button_only else
                                                    f"wake word {', '.join(self._wake_words)} on"))
        await self.say(BUTTON_ONLY_LINE if self.button_only else WAKE_WORD_LINE)

    async def say(self, text: str):
        """Speak a line on the box outside a request, as an announcement it fetches like a reply. Kept out of
        the conversation."""
        async with self._lock:
            run = Run()
            self.audio.runs[run.id] = run
            url = f"{self.audio.base_url(self.dev.host)}/reply/{run.id}.flac"
            self.mic.hold(HOLD_SECS)
            try:
                conv = await self._conversation()
                conv.speaker.begin(run)
                await conv.worker.queue_frames([TTSSpeakFrame(text, append_to_context=False)])
                await asyncio.wait_for(run.first_audio.wait(), 15)
                played = asyncio.create_task(self.client.send_voice_assistant_announcement_await_response(
                    url, 30, text=text))
                t0 = time.perf_counter()
                while conv.speaker.busy and time.perf_counter() - t0 < 20:
                    await asyncio.sleep(0.05)
                conv.speaker.run = None
                run.out.put_nowait(None)
                await played
            except Exception as e:                                          # noqa: BLE001
                logger.warning(f"{self.room}: could not say {text!r}: {e}")
                run.out.put_nowait(None)
            finally:
                self.mic.hold(run.playing_for() + TAIL_SECS)
                self.audio.forget(run.id, after=120)

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
        # The box listening on after a reply; it names the last wake word then too, so only the time tells.
        if time.monotonic() < self._follow_until:
            self.run.follow_up, self.run.start_timeout = True, self._follow_secs
        self._follow_until = 0.0
        self.mic.hold(HOLD_SECS)                # a browser in the room stops listening before the request
        self.audio.runs[self.run.id] = self.run
        how = "follow-up" if self.run.follow_up else f"wake word {wake_word!r}" if wake_word else "button"
        logger.info(f"{self.room}: {how}, run {self.run.id}")
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
            # The box streams silence too, so the limit is checked on every chunk, not only when none comes.
            timeout = (run.start_timeout if not any(voiced) else MAX_REQUEST_SECS) - (time.perf_counter() - t0)
            if timeout <= 0:
                break
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
        return await self.stt.transcribe(pcm, MIC_RATE, label=f"{self.room}: STT")

    async def _conversation(self) -> Conversation:
        c = self.conversation
        if c and time.monotonic() - c.last_used > self.cfg.channels.satellite.session_idle_mins * 60:
            await c.close(); c = None
        if c is None:
            c = self.conversation = await Conversation(self.cfg, self.room, self.feed).start()
        return c

    async def _hold(self):
        while True:
            await asyncio.sleep(RENEW_SECS)
            self.mic.hold(HOLD_SECS)

    async def _serve(self, run: Run):
        url = f"{self.audio.base_url(self.dev.host)}/reply/{run.id}.flac"
        holding = asyncio.create_task(self._hold())
        follow_up = False
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
                if heard and dismissal(heard, self.cfg.assistant.name):
                    logger.info(f"{self.room}: dismissed, the conversation ends")
                    heard = ""
                elif not heard and run.follow_up:
                    logger.info(f"{self.room}: follow-up closed, nothing said")
                if not heard:
                    # Always this error, which the firmware shows and plays nothing for: it is what takes the
                    # box back to idle. RUN_END alone arrives while the mic is still stopping and is ignored,
                    # leaving the box waiting for a reply (blinking) until it restarts.
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
                conv.brain.llm.ended = False
                said = await conv.reply(heard, run)
                conv.latency.flush()
                progress.cancel()
                run.out.put_nowait(None)
                question = said.rstrip().endswith("?")
                wait = max(START_TIMEOUT_SECS, self.cfg.channels.satellite.follow_up_secs) if question \
                    else self.cfg.channels.satellite.follow_up_secs
                ended = conv.brain.llm.ended            # a scene that speaks for itself (quiet_after)
                follow_up = bool(said.strip()) and wait > 0 and not self.button_only and not ended
                if follow_up:
                    self._follow_secs = wait
                    self._follow_until = time.monotonic() + run.playing_for() + FOLLOW_WAIT_SECS
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
            holding.cancel()
            if self.run is run:                 # a run cut short by a new wake word leaves the lease to that one
                self.mic.hold(run.playing_for() + (FOLLOW_UP_SECS if follow_up else TAIL_SECS))
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
        """The reply as WAV, for the fake box and firmware that still decodes it."""
        run = self.runs.get(request.match_info["run"])
        if run is None:
            raise web.HTTPNotFound()
        logger.info(f"reply {run.id} fetched by {request.remote} (wav)")
        resp = web.StreamResponse(headers={"Content-Type": "audio/wav", "Cache-Control": "no-store"})
        await resp.prepare(request)
        await resp.write(wav_header(OUT_RATE))
        while (chunk := await run.out.get()) is not None:
            await resp.write(chunk)
        run.out.put_nowait(None)           # a second fetch of the same URL ends too
        return resp

    async def reply_flac(self, request: web.Request) -> web.StreamResponse:
        """The reply as FLAC, encoded by ffmpeg while he speaks. Voice PE firmware 26.9.0 builds its
        decoder with FLAC and MP3 only: a WAV reply was fetched, refused ("Could not determine audio
        file type") and never played. FLAC is also what Home Assistant streams to the box."""
        run = self.runs.get(request.match_info["run"])
        if run is None:
            raise web.HTTPNotFound()
        logger.info(f"reply {run.id} fetched by {request.remote} (flac)")
        enc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(OUT_RATE), "-ac", "1",
            "-i", "pipe:0", "-c:a", "flac", "-compression_level", "0", "-frame_size", "2048",
            "-flush_packets", "1", "-f", "flac", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)

        async def feed():
            try:
                while (chunk := await run.out.get()) is not None:
                    enc.stdin.write(chunk)
                    await enc.stdin.drain()
                run.out.put_nowait(None)   # a second fetch of the same URL ends too
            finally:
                enc.stdin.close()

        feeder = asyncio.create_task(feed())
        resp = web.StreamResponse(headers={"Content-Type": "audio/flac", "Cache-Control": "no-store"})
        await resp.prepare(request)
        try:
            while part := await enc.stdout.read(4096):
                await resp.write(part)
        finally:
            feeder.cancel()
            if enc.returncode is None:
                enc.kill()
            await enc.wait()
        return resp

    async def start(self):
        app = web.Application()
        app.router.add_get("/health", lambda r: web.json_response({"ok": True}))
        app.router.add_get("/reply/{run}.wav", self.reply)
        app.router.add_get("/reply/{run}.flac", self.reply_flac)
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
    stt = Hearing(cfg, cls, cls.load_model(cfg.stt.model, cfg.stt.quantization))
    sats = [Satellite(cfg, room, dev, audio, stt, feed) for room, dev in s.devices.items()]
    for sat in sats:
        await sat.connect()
    logger.info(f"satellites: {', '.join(s.devices)}; replies served on :{s.http_port}")
    await asyncio.Event().wait()


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    asyncio.run(main())
