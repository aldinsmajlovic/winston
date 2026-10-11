"""Satellite channel: Home Assistant's Voice Preview Edition (or any ESPHome voice satellite) as a room
microphone and speaker. Runs as its own process, started by the supervisor:

    python -m winston.channels.satellite

Winston takes the role Home Assistant normally has: an ESPHome API client (aioesphomeapi) that
subscribes to the box's voice assistant. The box hears its own wake word (microWakeWord: okay_nabu,
hey_jarvis, hey_mycroft on stock firmware) and then streams 16 kHz microphone audio here. Winston
decides when the request is over (Silero VAD, Smart Turn), transcribes it, runs the brain with its tools and
the claim guard, and speaks. The box plays replies from a URL, as it does Home Assistant's
streaming TTS: RUN_START carries the URL, INTENT_PROGRESS with tts_start_streaming tells it to start
fetching once the first sentence goes to the voice, and this process serves that URL as a stream that
grows while the reply is synthesized. Every reply sets continue_conversation, so the box
listens again without the wake word: follow_up_secs for more (8 s after a question); silence then
ends the run as a wake with nothing said does, and so does a dismissal ("stop", "that's all").

A double press on the box's button switches it to button only and back: the wake word goes off, a press
starts a request and he never listens after a reply. The box keeps the choice (its wake words are
saved on it), so an empty wake word list on connect means button only. Holding the button is hold to
talk (Winston's firmware): the box starts the run without silence detection (no USE_VAD flag) and
reports the button, and the request ends when it is let go, however long the pauses in it.

Each box's key in config is its room. The box's conversation is the room's, shared with browser
sessions set to that room, and a wake holds the room's mic until the reply has played, so a browser
in the room neither answers too nor hears his voice as a turn (core/rooms.py).

Announcements and nudges reach the box as announcements it fetches like a reply, the chime first: an
announcement is said as given, a nudge is worded by the box's brain in its conversation (its plain line
if that does not come), and after a nudge the box listens on, so an answer needs no wake word.

Music and other streams from extensions (`ctx.speakers`) play on the box's media player, which is not
the one replies use: this process turns the extension's stream into 48 kHz stereo FLAC (ffmpeg) and
the box fetches it from here. The box turns media down 20 dB while it listens and while he talks, and
its echo cancellation takes out what it plays, through its speaker or its jack.

Only one client can hold a box's voice assistant, first come first served (ESPHome's
voice_assistant.cpp): take the box out of Home Assistant's voice pipeline, or HA wins.
Turn based for now: the stock firmware stops listening while it plays (docs/voice-pe.md)."""
import argparse
import asyncio
import faulthandler
import os
import signal
import socket
import struct
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import numpy as np
import soxr
from aioesphomeapi import (APIClient, BinarySensorInfo, BinarySensorState, Event, EventInfo, MediaPlayerCommand,
                            MediaPlayerInfo, ReconnectLogic, SelectInfo, VoiceAssistantAudioSettings,
                            VoiceAssistantCommandFlag)
from aioesphomeapi import VoiceAssistantEventType as Ev
from aiohttp import web
from loguru import logger
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (Frame, LLMMessagesAppendFrame, TTSAudioRawFrame, TTSSpeakFrame, TTSStartedFrame,
                                   TTSStoppedFrame, TTSTextFrame)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from winston.channels.clone import make_use_voice_tool
from winston.channels.text import TextSession, stale
from winston.channels.voice import build_tts, load_chime, prerender
from winston.config import load
from winston.config.schema import SatelliteDevice, WinstonConfig
from winston.core.hints import Heard, Hearing
from winston.core.live import FeedPoster, LiveEvent, LiveObserver
from winston.core import directory
from winston.core.announce import note, nudge_note, subscribe
from winston.core.brain import persona
from winston.core.rooms import MicLease, RoomLink, area, dismissal, http_sync
from winston.core.speak_up import WORDING_SECS
from winston.core.turns import Turn, TurnLog, TurnObserver, make_mark_tool
from winston.extension_host.client import signature

MIC_RATE = 16000                 # what the box streams
OUT_RATE = 48000                 # what the box's media player is set up for
VAD_FRAME = 512                  # Silero's window at 16 kHz
GUESS_SECS = 0.3                 # a pause this long starts transcribing, in case it is the end
START_TIMEOUT_SECS = 8.0         # no speech after the wake word: give up (also the least after a question)
FOLLOW_WAIT_SECS = 20.0          # a follow-up run must start this soon after the reply has played
MAX_REQUEST_SECS = 20.0
HOLD_SECS = 15.0                 # the room's mic lease while a run is on, renewed every RENEW_SECS
RENEW_SECS = 5.0
TAIL_SECS = 1.5                  # after the reply has played: the box's speaker and the room settle
FOLLOW_UP_SECS = 4.0             # after a question: until the box starts listening again
RELEASE_TAIL_SECS = 0.3          # hold to talk: audio kept after the button is let go, for a last word cut early
PREWARM_SECS = 60.0              # how often an idle box checks whether a new conversation's prompt changed
# Said on the box after a double press switched how it listens.
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
    tts_started: asyncio.Event = field(default_factory=asyncio.Event)  # the first sentence went to the voice
    speech_end: float = 0.0                                 # time.time() the request went quiet
    audio_secs: float = 0.0                                 # reply audio sent to the box so far
    audio_start: float = 0.0                                # perf_counter() of the first of it
    follow_up: bool = False                                 # started by the box after a reply, no wake word
    hold: bool = False                                      # hold to talk: ends when the button is let go
    start: str = "wake"                                     # wake, follow_up, button or hold, for the turn record
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
            if self.run:
                self.run.tts_started.set()
        elif isinstance(frame, TTSStoppedFrame):
            self.busy = False
        await self.push_frame(frame, direction)


class Conversation(TextSession):
    """One box's conversation: a TextSession that speaks, through the TTS and the Speaker after the claim
    guard, kept between wake-ups until it goes stale (text.stale)."""

    def __init__(self, cfg: WinstonConfig, room: str, feed: FeedPoster, previous: Callable[[], str | None] | None = None):
        self.feed = feed
        self.speaker = Speaker(cfg.tts.sample_rate)
        self.turns = TurnObserver()                   # fills in the open turn record
        tools = [make_use_voice_tool(cfg, channel="satellite")]     # a voice cloned in the browser
        if previous and cfg.logging.turns.enabled:
            tools.append(make_mark_tool(cfg, previous))   # "that's not what I said"
        super().__init__(cfg, channel="satellite", brain_channel="satellite", spoken=True, tools=tools,
                         room=RoomLink(room, f"box:{room}:{uuid.uuid4().hex[:6]}", http_sync(cfg)),
                         area=area(cfg, room), outputs=[build_tts(cfg), self.speaker],
                         observers=[LiveObserver("satellite", feed.put, spoken=True), self.turns])
        self.used = False       # handed to a request or an announcement (Satellite._conversation)

    def key(self) -> tuple[str, str | None]:
        """What sets the prompt llama.cpp caches for it: the system prompt and the extension tools (the
        other tools come from config and Home Assistant's MCP server)."""
        return self.brain.llm._settings.system_instruction, self.brain.tools_signature

    async def start(self):
        await super().start()
        self.brain.on_slow = lambda text: self._worker.queue_frames([TTSSpeakFrame(text, append_to_context=False)])
        return self

    async def _correct(self, text: str):
        """A state check found a device that did not do what he said (core/verify.py). Spoken into the
        reply that is still open, after what he said, without a chime. Without a transport the spoken
        text never reaches the context, so it goes in as his own words."""
        rid = uuid.uuid4().hex[:12]
        self.feed.put(LiveEvent(channel="satellite", type="reply", reply=rid, text=text))
        self.feed.put(LiveEvent(channel="satellite", type="reply_end", reply=rid))
        if self.speaker.run is None:
            logger.warning(f"{self.room.room}: correction after the reply closed, not spoken: {text!r}")
        await self._worker.queue_frames([
            TTSSpeakFrame(text, append_to_context=False),
            LLMMessagesAppendFrame(messages=[{"role": "assistant", "content": text}], run_llm=False)])
        t0 = time.perf_counter()
        while not self.speaker.busy and time.perf_counter() - t0 < 5:       # wait for it to start...
            await asyncio.sleep(0.05)
        while self.speaker.busy and time.perf_counter() - t0 < 30:          # ...and to be spoken
            await asyncio.sleep(0.05)

    async def reply(self, text: str, run: Run, timeout: float = 90) -> str:
        """Speak the reply to text into run.out; returns what was said once the turn is over."""
        self.speaker.begin(run)
        await self.ask(text, timeout)
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


class Satellite:
    """One box: the API connection, its voice assistant subscription and its runs."""

    def __init__(self, cfg: WinstonConfig, room: str, dev: SatelliteDevice, audio: "AudioServer",
                 stt: Hearing, feed: FeedPoster, turns: TurnLog | None = None):
        self.cfg, self.room, self.dev, self.audio, self.stt, self.feed = cfg, room, dev, audio, stt, feed
        self.turns = turns                  # the turn record, shared by the process's boxes (core/turns.py)
        self._last_turn: str | None = None   # the last turn he answered or let pass, for a mark by voice
        key = os.environ.get(dev.key_env, "").strip() if dev.key_env else ""
        if dev.key_env and not key:
            raise RuntimeError(f"{dev.key_env} is not set (data/.env)")
        self.client = APIClient(dev.host, dev.port, None, noise_psk=key or None, client_info="Winston")
        self.run: Run | None = None
        self._follow_until = 0.0            # monotonic time until which a start without a wake word is a follow-up
        self._follow_secs = START_TIMEOUT_SECS
        self.task: asyncio.Task | None = None
        self.conversation: Conversation | None = None
        self._building: asyncio.Task | None = None          # bringing the conversation up to date, one at a time
        self._uses = 0                                      # conversations handed out, so the prewarm sees a use
        self._warmed: tuple | None = None                   # the key of the last conversation warmed (Conversation.key)
        self._prewarming: asyncio.Task | None = None
        self._unsub = None
        self._vad = SileroVADAnalyzer(); self._vad.set_sample_rate(MIC_RATE)
        self._turn_end = LocalSmartTurnAnalyzerV3(sample_rate=MIC_RATE)
        self._lock = asyncio.Lock()
        self.mic = MicLease(cfg, room)
        self.button_only = False            # no wake word on the box: a press starts a request, no follow-ups
        self._wake_words: list[str] = []    # what a double press switches back on
        self._button: int | None = None     # key of the box's button event entity
        self._center: int | None = None     # key of the button itself (Winston's firmware), for hold to talk
        self._switching: asyncio.Task | None = None
        self._listener: asyncio.Task | None = None          # announcements and nudges while connected
        self._media_key: int | None = None                  # the box's media player (not the one replies use)
        self._media: dict | None = None                     # what plays on it now: {"id", "ext", "title"}

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
            await asyncio.shield(self._build(warm=True))
        self._prerender = asyncio.create_task(prerender(self.cfg))     # the lines he says while he thinks
        if self._prewarming is None or self._prewarming.done():
            self._prewarming = asyncio.create_task(self._prewarm())
        self._unsub = self.client.subscribe_voice_assistant(
            handle_start=self._handle_start, handle_stop=self._handle_stop, handle_audio=self._handle_audio)
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
                logger.info(f"{self.room}: button only (no wake word on); a double press switches the wake word on")
            elif use:
                await self.client.set_voice_assistant_configuration(use)
                logger.info(f"{self.room}: wake word {', '.join(use)}")
        try:
            ents, _ = await self.client.list_entities_services()
            self._media_key = next((e.key for e in ents if isinstance(e, MediaPlayerInfo)), None)
            self._button = next((e.key for e in ents if isinstance(e, EventInfo) and "double_press" in e.event_types),
                                None)
            self._center = next((e.key for e in ents if isinstance(e, BinarySensorInfo)
                                 and e.object_id == "center_button"), None)
            if self._button is not None or self._center is not None:
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
        # Last, once the media player is known: winstond sends a stream that was playing before a restart
        # as soon as this subscribes.
        if self.cfg.announce.enabled and (self._listener is None or self._listener.done()):
            self._listener = asyncio.create_task(self._speak_up())

    def _on_state(self, state):
        double = isinstance(state, Event) and state.key == self._button and state.event_type == "double_press"
        if double and (self._switching is None or self._switching.done()):
            self._switching = asyncio.create_task(self.switch_listening())
        let_go = isinstance(state, BinarySensorState) and state.key == self._center and not state.state
        if let_go and self.run and self.run.hold:
            asyncio.get_running_loop().call_later(RELEASE_TAIL_SECS, self.run.mic.put_nowait, None)

    async def switch_listening(self):
        """A double press: the wake word off (button only) or back on, and a line on the box to say which."""
        if self.button_only and not self._wake_words:
            logger.warning(f"{self.room}: double press, but no wake word to switch on")
            return
        self.button_only = not self.button_only
        self._follow_until = 0.0
        try:
            await self.client.set_voice_assistant_configuration([] if self.button_only else self._wake_words)
        except Exception as e:                                              # noqa: BLE001
            logger.warning(f"{self.room}: could not switch the wake word: {e}")
            self.button_only = not self.button_only
            return
        logger.info(f"{self.room}: double press, " + ("button only" if self.button_only else
                                                    f"wake word {', '.join(self._wake_words)} on"))
        await self.speak({"type": "line", "text": BUTTON_ONLY_LINE if self.button_only else WAKE_WORD_LINE},
                         chime=False)

    async def _speak_up(self):
        async for ev in subscribe(self.cfg, "satellite", self.room):
            try:
                if ev.get("type") == "media":
                    self.media(ev)                  # never waits behind a reply
                else:
                    await self.speak(ev)
            except Exception as e:                                          # noqa: BLE001
                logger.warning(f"{self.room}: could not deliver {ev.get('type', 'announcement')}: {e}")

    def media(self, ev: dict):
        """A stream from an extension on the box's media player, or its end. A new stream replaces what
        plays; an extension stops only its own."""
        if self._media_key is None:
            logger.warning(f"{self.room}: the box has no media player; {ev.get('title') or 'the stream'} not played")
            return
        if ev["action"] == "play":
            if self._media:
                self.audio.stop_media(self._media["id"])
            self.audio.media[ev["id"]] = ev["url"]
            self._media = {"id": ev["id"], "ext": ev["ext"], "title": ev.get("title")}
            url = f"{self.audio.base_url(self.dev.host)}/media/{ev['id']}.flac"
            self.client.media_player_command(self._media_key, media_url=url, announcement=False)
            logger.info(f"{self.room}: playing {ev.get('title') or ev['url']} for {ev['ext']}")
        elif ev["action"] == "stop" and self._media and self._media["ext"] == ev.get("ext"):
            self.client.media_player_command(self._media_key, command=MediaPlayerCommand.STOP)
            self.audio.stop_media(self._media["id"])
            logger.info(f"{self.room}: stopped {self._media.get('title') or 'the stream'} for {ev['ext']}")
            self._media = None

    async def speak(self, ev: dict, chime: bool = True):
        """An announcement (said as given) or a nudge (worded by the brain, its plain line otherwise) on the
        box, after the chime, as an announcement it fetches like a reply. After a nudge the box listens on.
        A line of his own (type "line", a long press) is said as given and kept out of the conversation."""
        nudge = ev.get("type") == "nudge"
        text = ev["say"] if nudge else ev["text"]
        async with self._lock:
            run = Run()
            self.audio.runs[run.id] = run
            url = f"{self.audio.base_url(self.dev.host)}/reply/{run.id}.flac"
            self.mic.hold(HOLD_SECS)
            try:
                conv = await self._conversation()
                if chime and (sound := self.audio.chime()):
                    run.out.put_nowait(sound)
                    run.audio_secs += len(sound) / 2 / OUT_RATE
                if nudge:
                    logger.info(f"{self.room}: nudge {ev['id']}: {text!r}")
                    can = any(t.name == "follow_up" for t in conv.brain.tools.standard_tools)
                    replying = asyncio.create_task(conv.reply(nudge_note(ev, can), run, timeout=60))
                    try:
                        await asyncio.wait_for(run.first_audio.wait(), WORDING_SECS)
                    except TimeoutError:
                        logger.warning(f"{self.room}: nudge not worded within {WORDING_SECS:g} s; the plain line")
                        await conv._worker.queue_frames([
                            TTSSpeakFrame(text, append_to_context=False),
                            LLMMessagesAppendFrame(messages=[{"role": "assistant", "content": text}], run_llm=False)])
                        await asyncio.wait_for(run.first_audio.wait(), 15)
                else:
                    logger.info(f"{self.room}: {ev.get('type', 'announcement')} {text!r}")
                    conv.speaker.begin(run)
                    frames = [TTSSpeakFrame(text, append_to_context=False)]
                    if ev.get("type") != "line":
                        frames.insert(0, LLMMessagesAppendFrame(messages=[
                            {"role": "user", "content": note(text, ev.get("context") or {})},
                            {"role": "assistant", "content": text}], run_llm=False))
                    await conv._worker.queue_frames(frames)
                    await asyncio.wait_for(run.first_audio.wait(), 15)
                listen = nudge and not self.button_only and self.cfg.channels.satellite.follow_up_secs > 0
                played = asyncio.create_task(self.client.send_voice_assistant_announcement_await_response(
                    url, 60, text=text, start_conversation=listen))
                if nudge:
                    logger.info(f"{self.room}: said {await replying!r}")
                else:
                    t0 = time.perf_counter()
                    while conv.speaker.busy and time.perf_counter() - t0 < 30:
                        await asyncio.sleep(0.05)
                    conv.speaker.run = None
                run.out.put_nowait(None)
                if listen:          # the box starts listening once it has played; no wake word for that run
                    self._follow_secs = max(START_TIMEOUT_SECS, self.cfg.channels.satellite.follow_up_secs)
                    self._follow_until = time.monotonic() + run.playing_for() + FOLLOW_WAIT_SECS
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
        if self._listener:
            self._listener.cancel()
            self._listener = None
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
        # A follow-up reuses the last start's silence detection on the box, so after a hold it has no flag either.
        self.run.hold = not flags & VoiceAssistantCommandFlag.USE_VAD and not self.run.follow_up
        self.run.start = ("follow_up" if self.run.follow_up else "wake" if wake_word else
                          "hold" if self.run.hold else "button")
        self._follow_until = 0.0
        self.mic.hold(HOLD_SECS)                # a browser in the room stops listening before the request
        self.audio.runs[self.run.id] = self.run
        how = ("follow-up" if self.run.follow_up else f"wake word {wake_word!r}" if wake_word else
               "hold to talk" if self.run.hold else "button")
        logger.info(f"{self.room}: {how}, run {self.run.id}")
        self.task = asyncio.create_task(self._serve(self.run))
        return 0                      # 0: audio over the API connection, not UDP

    async def _handle_audio(self, data: bytes, data2: bytes | None):
        if self.run:
            self.run.mic.put_nowait(np.frombuffer(data, dtype="<i2"))

    async def _handle_stop(self, abort: bool):
        logger.info(f"{self.room}: the box {'aborted' if abort else 'ended'} the request"
                    f"{'' if self.task and not self.task.done() else ' (no run)'}")
        if abort and self.task:
            self.task.cancel()
        elif self.run:
            self.run.mic.put_nowait(None)

    async def _listen(self, run: Run) -> tuple[np.ndarray, asyncio.Task | None] | None:
        """Microphone audio until the request is over. At the first GUESS_SECS of a pause the audio so far
        is transcribed, so the text is ready if the pause turns out to be the end, and Smart Turn judges
        whether it sounds finished: end_silence_secs of quiet then ends a finished one, an unfinished one
        ("Can you tell me what to have in my living room because") gets unfinished_silence_secs. Speech
        after the pause throws both away. Returns the audio and that transcription."""
        buf, voiced = np.zeros(0, np.int16), []
        sat = self.cfg.channels.satellite
        end = int(sat.end_silence_secs * MIC_RATE / VAD_FRAME)
        unfinished = max(end, int(sat.unfinished_silence_secs * MIC_RATE / VAD_FRAME))     # longer, never shorter
        guess_at = int(GUESS_SECS * MIC_RATE / VAD_FRAME)
        guess: asyncio.Task | None = None
        finished: asyncio.Task | None = None
        t0 = time.perf_counter()
        try:
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
                    if voiced[-1] and guess:            # more speech: the guess was not the whole request
                        guess.cancel(); guess = finished = None
                if any(voiced):
                    quiet = len(voiced) - 1 - max(i for i, v in enumerate(voiced) if v)
                    if quiet >= guess_at and guess is None:
                        pcm = buf[:len(voiced) * VAD_FRAME].copy()
                        guess = asyncio.create_task(self._transcribe(pcm))
                        finished = asyncio.create_task(asyncio.to_thread(self._sounds_finished, pcm))
                    # Until Smart Turn has answered (about 80 ms) the pause counts as unfinished.
                    # A hold to talk ends only when the button is let go (_on_state), whatever the pause.
                    if not run.hold and quiet >= (end if finished and finished.done() and finished.result()
                                                  else unfinished):
                        break
        except BaseException:               # the run was stopped: nobody will use the guess
            if guess:
                guess.cancel()
            raise
        if sum(voiced) * VAD_FRAME / MIC_RATE < 0.25:
            if guess:
                guess.cancel()
            return None
        last = max(i for i, v in enumerate(voiced) if v)
        run.speech_end = time.time() - (len(buf) - (last + 1) * VAD_FRAME) / MIC_RATE
        return buf, guess

    def _sounds_finished(self, pcm: np.ndarray) -> bool:
        """Smart Turn's verdict on the request so far, ending in a pause (it reads the last 8 s)."""
        try:
            # ponytail: Pipecat's private predict call; its public API keeps its own clock and silence count
            p = self._turn_end._predict_endpoint(pcm.astype(np.float32) / 32768)["probability"]
        except Exception:                                                   # noqa: BLE001
            logger.exception(f"{self.room}: Smart Turn failed; the pause counts as the end")
            return True
        logger.info(f"{self.room}: sounds finished {p:.2f}")
        return p > 0.5

    async def _transcribe(self, pcm: np.ndarray) -> Heard:
        return await self.stt.hear(pcm, MIC_RATE, label=f"{self.room}: STT")

    def _record(self, turn: Turn | None, outcome: str | None = None):
        if turn is not None and self.turns is not None:
            turn.outcome = outcome or turn.outcome
            if turn.markable:
                self._last_turn = turn.id
            self.turns.write(turn)

    def _build(self, warm: bool = False) -> asyncio.Task:
        """The task that brings the box's conversation up to date: a stale one is closed and a new one built,
        and warmed on the wake path. One at a time: a wake, an announcement and the prewarm all come here and
        join a build in flight. A task, so a run cut short leaves no half-built conversation."""
        if self._building is None or self._building.done():
            self._building = asyncio.create_task(self._rebuild(warm))
            self._building.add_done_callback(self._build_failed)
        return self._building

    def _build_failed(self, t: asyncio.Task):
        if not t.cancelled() and t.exception():     # read here, so a wake that heard nothing still logs it
            logger.warning(f"{self.room}: building the conversation failed: {t.exception()}")

    async def _rebuild(self, warm: bool) -> Conversation:
        c = self.conversation
        if c and await stale(c, self.cfg.channels.satellite.session_idle_mins * 60):
            self.conversation = None
            await c.close(); c = None
        if c is None:
            c = self.conversation = await self._new(warm)
        return c

    async def _new(self, warm: bool) -> Conversation:
        c = await Conversation(self.cfg, self.room, self.feed, lambda: self._last_turn).start()
        if warm:        # awaited before the real request, which then lands on the slot that holds the prompt
            await c.brain.warm()
            self._warmed = c.key()
        return c

    async def _conversation(self, building: asyncio.Task | None = None) -> Conversation:
        """The conversation for a request or an announcement: from the build its wake started (once more if
        that failed: Home Assistant may be back by the end of the request), or a new one."""
        try:
            c = await asyncio.shield(building or self._build())
        except Exception:
            if building is None:
                raise
            c = await asyncio.shield(self._build())
        c.used = True
        self._uses += 1
        return c

    def _busy(self) -> bool:
        """A request, an announcement or a build is under way."""
        return (self._lock.locked() or bool(self.task and not self.task.done())
                or bool(self._building and not self._building.done()))

    async def _next_key(self) -> tuple[str, str] | None:
        """Conversation.key of a conversation built now, from what Brain.start builds it with (Home Assistant
        is read at most every ten minutes, directory.home). None while a conversation runs on any channel
        (winstond's talking): a prewarm's prefill would slow it. Raises when winstond does not answer, where
        fetch_tools would say "no tools" and the prewarm would build a conversation without them."""
        async with httpx.AsyncClient(base_url=self.cfg.control.url, timeout=3) as h:
            if (await h.get("/api/status")).json().get("talking"):
                return None
            tools, kinds = await h.get("/api/extensions/tools"), await h.get("/api/nudges/kinds")
            tools.raise_for_status(); kinds.raise_for_status()
        house = directory.house_map(self.cfg, await directory.home(self.cfg))
        return persona(self.cfg, house, "satellite"), signature(tools.json(), kinds.json().get("kinds", 0) > 0)

    async def _prewarm(self):
        """Every PREWARM_SECS while the box is idle: when the prompt a new conversation would get has changed
        (the diary after midnight, a remembered fact, an extension's notes or tools), build and warm that
        conversation now, so the first wake after it does not wait while llama.cpp reads ~9.4k tokens (7.5 to
        10.6 s). Never while a conversation is in use and not stale: its cached prompt must stay. Only a
        change starts one, never idling, so an unused conversation still goes stale at the wake as before
        (a fresh Home Assistant session) and is rebuilt there from the cache."""
        idle = self.cfg.channels.satellite.session_idle_mins * 60
        while True:
            await asyncio.sleep(PREWARM_SECS)
            try:
                c, uses = self.conversation, self._uses
                if self._busy() or (c and c.used and not await stale(c, idle)):
                    continue
                key = await self._next_key()
                if key is None or key == self._warmed or self._busy() or self._uses != uses:
                    continue
                t0 = time.perf_counter()
                # Built on the side, not as self._building: a wake meanwhile keeps the conversation it has
                # instead of waiting out this prefill, or failing with it.
                new = await self._new(warm=True)
                if self._busy() or self._uses != uses or self.conversation is not c:
                    await new.close()           # the box was used meanwhile; the prompt is cached all the same
                    continue
                self.conversation = new         # no await since the check: nothing can take c in between
                if c:
                    await c.close()
                logger.info(f"{self.room}: next conversation prewarmed in {(time.perf_counter() - t0) * 1000:.0f} ms")
            except Exception as e:                                          # noqa: BLE001
                logger.warning(f"{self.room}: prewarm failed: {e}")

    async def _hold(self):
        while True:
            await asyncio.sleep(RENEW_SECS)
            self.mic.hold(HOLD_SECS)

    async def _serve(self, run: Run):
        url = f"{self.audio.base_url(self.dev.host)}/reply/{run.id}.flac"
        holding = asyncio.create_task(self._hold())
        follow_up = False
        turn: Turn | None = None
        conv: Conversation | None = None
        try:
            async with self._lock:
                building = self._build(warm=True)       # a stale conversation is rebuilt and warmed while he speaks
                self._event(Ev.VOICE_ASSISTANT_RUN_START, {"url": url})
                self._event(Ev.VOICE_ASSISTANT_STT_START)
                self.feed.put(LiveEvent(channel="satellite", type="state", state="listening"))
                listened = await self._listen(run)
                self._event(Ev.VOICE_ASSISTANT_STT_VAD_END)          # the box stops its mic and waits
                pcm, guess = listened or (None, None)
                got = (await guess if guess else await self._transcribe(pcm)) if pcm is not None else None
                heard = got.text if got else ""
                t_heard = time.perf_counter()
                if got is not None and self.turns is not None:
                    turn = self.turns.begin(room=self.room, start=run.start, ts=run.speech_end)
                    turn.add(heard, got)
                    turn.stt_ms = turn.ms_since_end()
                logger.info(f"{self.room}: heard {heard!r}")
                if heard:               # what the box heard, as the browser's words show on the Talk page
                    self.feed.put(LiveEvent(channel="satellite", type="user", text=heard, room=self.room))
                if heard and dismissal(heard, self.cfg.assistant.name):
                    logger.info(f"{self.room}: dismissed, the conversation ends")
                    heard = ""
                    if turn:
                        turn.outcome = "dismissed"
                elif not heard and run.follow_up:
                    logger.info(f"{self.room}: follow-up closed, nothing said")
                if not heard:
                    if turn and not turn.outcome:
                        turn.outcome = "nothing_heard"
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
                conv = await self._conversation(building)

                async def stream_when_ready():
                    # At the first sentence, not its audio: the box connects and ffmpeg starts while it is spoken.
                    await run.tts_started.wait()
                    self._event(Ev.VOICE_ASSISTANT_INTENT_PROGRESS, {"tts_start_streaming": "1"})
                    await run.first_audio.wait()
                    logger.info(f"{self.room}: first audio {(time.perf_counter() - t_heard) * 1000:.0f} ms after "
                                f"the transcript")
                progress = asyncio.create_task(stream_when_ready())
                conv.brain.llm.ended = False
                conv.turns.turn = turn
                said = await conv.reply(heard, run)
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
            logger.info(f"{self.room}: run {run.id} stopped")
            self.feed.put(LiveEvent(channel="satellite", type="state", state="idle"))
            run.out.put_nowait(None)
            if turn:
                turn.outcome = "cut_short"
            raise
        except Exception as e:                                              # noqa: BLE001
            logger.exception(f"{self.room}: run failed")
            if turn:
                turn.outcome = "failed"
            run.out.put_nowait(None)
            self._event(Ev.VOICE_ASSISTANT_ERROR, {"code": "winston-error", "message": str(e)[:200]})
            self._event(Ev.VOICE_ASSISTANT_RUN_END)
        finally:
            holding.cancel()
            if conv is not None and conv.turns.turn is turn:
                conv.turns.turn = None
            self._record(turn)
            if self.run is run:                 # a run cut short by a new wake word leaves the lease to that one
                self.mic.hold(run.playing_for() + (FOLLOW_UP_SECS if follow_up else TAIL_SECS))
            self.audio.forget(run.id, after=120)


class AudioServer:
    """Serves each run's reply as a WAV the box fetches while it is still being spoken."""

    def __init__(self, cfg: WinstonConfig):
        self.cfg = cfg
        self.runs: dict[str, Run] = {}
        self._chime: bytes | None = None
        self.media: dict[str, str] = {}                     # media id -> the extension's stream
        self._encoders: dict[str, set] = {}                 # media id -> its ffmpeg processes

    def stop_media(self, media_id: str):
        self.media.pop(media_id, None)
        for enc in self._encoders.pop(media_id, set()):
            if enc.returncode is None:
                enc.kill()

    async def media_flac(self, request: web.Request) -> web.StreamResponse:
        """An extension's stream as the box plays media: 48 kHz stereo FLAC, encoded by ffmpeg from
        whatever the extension serves, for as long as the box listens or until it is stopped."""
        media_id = request.match_info["media"]
        source = self.media.get(media_id)
        if source is None:
            raise web.HTTPNotFound()
        logger.info(f"media {media_id} fetched by {request.remote}")
        return await self._flac(request, ["-i", source, "-ar", str(OUT_RATE), "-ac", "2"],
                                encoders=self._encoders.setdefault(media_id, set()))

    @staticmethod
    async def _flac(request: web.Request, input_args: list[str], feed: Callable | None = None,
                    encoders: set | None = None) -> web.StreamResponse:
        """ffmpeg's FLAC of input_args, streamed as it comes until the input ends or the box hangs up.
        feed(stdin) writes the input when it is a pipe; encoders holds the process while it runs, for
        stop_media."""
        enc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-hide_banner", "-loglevel", "error", *input_args, "-c:a", "flac", "-compression_level", "0",
            "-frame_size", "2048", "-flush_packets", "1", "-f", "flac", "pipe:1",
            stdin=asyncio.subprocess.PIPE if feed else asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE)
        feeder = asyncio.create_task(feed(enc.stdin)) if feed else None
        if encoders is not None:
            encoders.add(enc)
        resp = web.StreamResponse(headers={"Content-Type": "audio/flac", "Cache-Control": "no-store"})
        await resp.prepare(request)
        try:
            while part := await enc.stdout.read(4096):
                await resp.write(part)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            if feeder:
                feeder.cancel()
            if enc.returncode is None:
                enc.kill()
            await enc.wait()
            if encoders is not None:
                encoders.discard(enc)
        return resp

    def chime(self) -> bytes:
        """The announcement chime at the box's rate, b"" for none."""
        if self._chime is None:
            pcm = load_chime(self.cfg)
            self._chime = soxr.resample(np.frombuffer(pcm, dtype="<i2"), self.cfg.tts.sample_rate, OUT_RATE)\
                .astype("<i2").tobytes() if pcm else b""
        return self._chime

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

        async def feed(stdin):
            try:
                while (chunk := await run.out.get()) is not None:
                    stdin.write(chunk)
                    await stdin.drain()
                run.out.put_nowait(None)   # a second fetch of the same URL ends too
            finally:
                stdin.close()

        return await self._flac(request, ["-f", "s16le", "-ar", str(OUT_RATE), "-ac", "1", "-i", "pipe:0"], feed)

    async def start(self):
        app = web.Application()
        app.router.add_get("/health", lambda r: web.json_response({"ok": True}))
        app.router.add_get("/reply/{run}.wav", self.reply)
        app.router.add_get("/reply/{run}.flac", self.reply_flac)
        app.router.add_get("/media/{media}.flac", self.media_flac)
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
    stt = Hearing.load(cfg)
    turns = TurnLog(cfg, "satellite", on_written=lambda t: feed.put(LiveEvent(channel="satellite", type="turn",
                                                                              call=t.id, text=t.heard, room=t.room)))
    sats = [Satellite(cfg, room, dev, audio, stt, feed, turns) for room, dev in s.devices.items()]
    for sat in sats:
        await sat.connect()
    logger.info(f"satellites: {', '.join(s.devices)}; replies served on :{s.http_port}")
    await asyncio.Event().wait()


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    faulthandler.register(signal.SIGUSR1)      # kill -USR1 <pid>: every thread's stack into the log
    asyncio.run(main())
