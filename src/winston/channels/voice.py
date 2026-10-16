"""Voice channel: browser mic -> STT -> brain -> TTS -> browser speaker, with VAD-driven barge-in
and per-turn latency logging, on Pipecat's SmallWebRTC transport. Runs as its own process,
started by the supervisor:

    python -m winston.channels.voice --host 127.0.0.1 --port 7860

A browser set to a room (the offer's request_data {"room": ...}) shares that room's conversation and
pauses its mic while the room's box has it (core/rooms.py).

Every model sits behind an adapter chosen in config (plan §6, rule 1)."""
import asyncio
import time
import uuid
import wave

import httpx
import numpy as np
import soxr
from loguru import logger
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (ErrorFrame, LLMMessagesAppendFrame, OutputAudioRawFrame, TranscriptionFrame,
                                   TTSAudioRawFrame, TTSSpeakFrame)
from pipecat.observers.loggers.transcription_log_observer import TranscriptionLogObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import (LLMContextAggregatorPair,
                                                                    LLMUserAggregatorParams)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.services.openai.tts import VALID_VOICES, OpenAITTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.utils.time import time_now_iso8601
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from winston.config import load, resolve
from winston.config.schema import WinstonConfig
from winston.core.brain import HOME_LINES, Brain, Clock, ToolCallLogObserver, api_key
from winston.channels.clone import MicTap, make_voice_tools
from winston.core.announce import note, nudge_note, subscribe
from winston.core.reply import ReplyFilter, ReplySentences
from winston.core.hints import Hearing
from winston.core.live import FeedPoster, LiveEvent, LiveObserver
from winston.core.rooms import MicGate, RoomLink, area, http_sync, mic_events, wake_phrases
from winston.core.speak_up import WORDING_SECS, Mentions, SpeechWatch
from winston.core.spoken import SpokenText
from winston.core.turns import SpokenTurns, TurnLog, make_mark_tool

CFG: WinstonConfig = load()
_HEARING: Hearing | None = None


def hearing(cfg: WinstonConfig = CFG) -> Hearing:
    """The process's one STT model with the hint words; the model loads once (about 10 s)."""
    global _HEARING
    if _HEARING is None:
        _HEARING = Hearing.load(cfg)
    return _HEARING


class HearingSTT(SegmentedSTTService):
    """The pipeline's speech to text, for any engine. Pipecat cuts the utterance (the VAD); the shared
    Hearing transcribes it with the configured adapter, corrects hint words and logs the STT line; the
    Heard, audio included, rides on the TranscriptionFrame (result) so the turn record keeps this
    session's audio with its words. An adapter only turns audio into text."""

    def __init__(self, hearing: Hearing, sample_rate: int | None = None, **kwargs):
        super().__init__(sample_rate=sample_rate, settings=STTSettings(model=hearing.cfg.stt.model, language=None),
                         **kwargs)
        self._hearing = hearing

    @property
    def wants_wav_segments(self) -> bool:
        return False  # run_stt gets raw int16 PCM, not a WAV container

    def can_generate_metrics(self) -> bool:
        return True

    async def run_stt(self, audio: bytes):
        await self.start_processing_metrics()
        await self.start_ttfb_metrics()
        heard = await self._hearing.hear(np.frombuffer(audio, dtype=np.int16), self.sample_rate)
        await self.stop_ttfb_metrics()
        await self.stop_processing_metrics()
        if heard.text:
            yield TranscriptionFrame(heard.text, self._user_id, time_now_iso8601(), result=heard)


def build_stt(cfg: WinstonConfig = CFG) -> HearingSTT:
    """The pipeline's STT service, on the shared model and hint words."""
    return HearingSTT(hearing(cfg))


async def transcribe(pcm: np.ndarray, extra: list[str]) -> str:
    """16 kHz int16 to corrected text outside the pipeline (clone consent), with extra hint words."""
    return await hearing().transcribe(pcm, 16000, label="clone: STT", extra=extra)


# Takes of the lines said by code while he thinks about a home request (HOME_LINES), rendered while the
# speech server is idle and played in turn, TAKES per line so the same take does not come round soon. A
# live render shared the GPU with the thinking it covers and slowed it by about 0.4 s. A line in another
# voice (a clone) is still rendered live.
TAKES = 3
_takes: dict[tuple[str, str], list[bytes]] = {}       # (voice, line): 16-bit PCM at the TTS rate
_turn: dict[tuple[str, str], int] = {}


async def prerender(cfg: WinstonConfig = CFG):
    """Fill the takes of HOME_LINES in cfg.tts.voice; filled lines are skipped."""
    c = cfg.tts
    try:
        async with httpx.AsyncClient(base_url=c.base_url, timeout=60) as h:
            for line in HOME_LINES:
                takes = _takes.setdefault((c.voice, line), [])
                while len(takes) < TAKES:
                    r = await h.post("/audio/speech", json={"input": line, "voice": c.voice, "model": "winston-tts",
                                                           "response_format": "pcm"})
                    r.raise_for_status()
                    takes.append(r.content)
    except Exception as e:                                                  # noqa: BLE001
        logger.warning(f"lines for thinking not rendered ahead, they are rendered live: {e}")


class SpeechTTS(OpenAITTSService):
    """OpenAI's speech protocol with any voice name. Pipecat only lets OpenAI's own names through,
    but a local server names its clips by path ("vctk/p226_023", "friends/erik"). Text with nothing
    to say never gets here (SpokenText)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Speaks each sentence the reply filter passes on at once (Pipecat 1.11 takes no aggregator argument).
        self._text_aggregator = ReplySentences(aggregation_type=self._text_aggregation_mode)

    @property
    def chunk_size(self) -> int:
        # 0.24 s, three 80 ms Breeze frames: Pipecat's 0.5 s held the first sound back about 240 ms.
        # Two frames ran dry under a large prefill. Whole samples, so np.frombuffer('<i2') never splits one.
        return int(self.sample_rate * 0.24) * 2

    async def run_tts(self, text: str, context_id: str):
        key = (self._settings.voice, text.strip())
        if takes := _takes.get(key):
            i = _turn.get(key, 0); _turn[key] = i + 1
            pcm = takes[i % len(takes)]
            await self.stop_ttfb_metrics()
            for j in range(0, len(pcm), self.chunk_size):
                yield TTSAudioRawFrame(pcm[j:j + self.chunk_size], self.sample_rate, 1, context_id=context_id)
            return
        if self._settings.voice in VALID_VOICES:
            async for f in super().run_tts(text, context_id):
                yield f
            return
        args = {"input": text, "model": self._settings.model, "voice": self._settings.voice, "response_format": "pcm"}
        async with self._client.audio.speech.with_streaming_response.create(**args) as r:
            if r.status_code != 200:
                yield ErrorFrame(error=f"speech server answered {r.status_code}: {await r.text()}")
                return
            await self.start_tts_usage_metrics(text)
            async for chunk in r.iter_bytes(self.chunk_size):
                if chunk:
                    await self.stop_ttfb_metrics()
                    yield TTSAudioRawFrame(chunk, self.sample_rate, 1, context_id=context_id)


def build_tts(cfg: WinstonConfig = CFG):
    c = cfg.tts
    return SpeechTTS(api_key=api_key(c.api_key_env), base_url=c.base_url, sample_rate=c.sample_rate,
                     settings=SpeechTTS.Settings(model="winston-tts", voice=c.voice),
                     text_filters=[SpokenText(cfg)])      # times in the region's clock, in words


def load_chime(cfg: WinstonConfig = CFG) -> bytes:
    """The announcement chime as 16-bit mono PCM at the TTS sample rate, or b"" for none."""
    if not cfg.announce.chime:
        return b""
    try:
        with wave.open(str(resolve(cfg.announce.chime))) as w:
            if w.getsampwidth() != 2:
                raise ValueError("chime must be 16-bit PCM")
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
            if w.getnchannels() > 1:
                x = x.reshape(-1, w.getnchannels()).mean(axis=1).astype("<i2")
            rate = w.getframerate()
    except (OSError, ValueError, wave.Error) as e:
        logger.warning(f"no announcement chime: {e}")
        return b""
    if rate != cfg.tts.sample_rate:
        x = soxr.resample(x, rate, cfg.tts.sample_rate)     # int16 in and out, so it clips instead of wrapping
    return x.astype("<i2").tobytes()


CHIME = load_chime()
FEED = FeedPoster(CFG)          # what this channel hears and says, for the Talk page
TURNS = TurnLog(CFG, "voice",    # each spoken turn: its timings, and with logging.turns all of it
                on_written=lambda t: FEED.put(LiveEvent(channel="voice", type="turn", call=t.id, text=t.heard,
                                                        room=t.room)))


transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


def room_of(runner_args: RunnerArguments) -> str | None:
    body = runner_args.body if isinstance(runner_args.body, dict) else {}
    room = body.get("room")
    return (" ".join(room.split())[:60] or None) if isinstance(room, str) else None


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    room = room_of(runner_args)
    brain = Brain(CFG, channel="voice"); await brain.start()
    stt, tts = build_stt(), build_tts()
    tap = MicTap()                  # lets clone_voice record a friend without it becoming a turn
    for schema, handler in make_voice_tools(CFG, tap, transcribe):
        brain.add_tool(schema, handler)
    if CFG.logging.turns.enabled:       # "that's not what I said": marks this session's last turn (turns, below)
        brain.add_tool(*make_mark_tool(CFG, lambda: turns.last_id))
    # Pipecat 1.11 runs the VAD in the user aggregator, not the transport; TransportParams has no
    # vad_analyzer field and silently drops one (the browser test never started a turn because of it).
    vad = SileroVADAnalyzer(params=VADParams(stop_secs=CFG.vad.stop_secs))
    # Smart Turn decides whether a pause ends the turn. When it thinks the sentence is unfinished it
    # waits unfinished_stop_secs more; Pipecat's default of 3 s made "Yes." take 3 s to reach the LLM.
    turn_end = LocalSmartTurnAnalyzerV3(params=SmartTurnParams(stop_secs=CFG.vad.unfinished_stop_secs))
    aggregators = LLMContextAggregatorPair(brain.new_context(), user_params=LLMUserAggregatorParams(
        vad_analyzer=vad,
        user_turn_strategies=UserTurnStrategies(stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=turn_end)]),
    ))
    turns = SpokenTurns(TURNS, room, busy=lambda: not brain.verifier.idle)
    watch = SpeechWatch()
    link = RoomLink(room, f"voice:{uuid.uuid4().hex[:8]}", http_sync(CFG)) if room else None
    gate = MicGate(wake_phrases(CFG) if room else None)

    pipeline = Pipeline([
        transport.input(),      # mic audio from the browser
        gate.audio,             # silence while the room's box has the mic
        tap,                    # held while a voice is being cloned
        stt,                    # utterance -> text
        gate.text,              # ...and nothing heard then becomes a turn
        aggregators.user(),     # VAD + turn taking; text -> context
        Clock(area(CFG, room) if room else None, CFG),   # the time, room and what is going on, before each message
        Mentions(CFG),          # nudges for the next conversation and the briefing, after his answer
        *([link.before] if link else []),           # the room's conversation in, this session's out
        brain.llm,              # context -> streamed text, tool calls
        ReplyFilter(),          # sentence at a time, no stray </think>
        tts,                    # sentences -> pcm
        transport.output(),     # speaker audio to the browser
        aggregators.assistant(),
        *([link.after] if link else []),            # the reply out to the room
    ])
    worker = PipelineWorker(
        pipeline, params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[brain.verifier.observer, UserBotLatencyObserver(), TranscriptionLogObserver(),
                   ToolCallLogObserver(CFG, channel="voice"), LiveObserver("voice", FEED.put, spoken=True), watch,
                   turns],
        idle_timeout_secs=None,
    )

    async def correct(text: str):
        """A state check found a device that did not do what he said (core/verify.py). Spoken like an
        announcement, without the chime, once his reply is over; the TTS puts the words in the context."""
        rid = uuid.uuid4().hex[:12]
        FEED.put(LiveEvent(channel="voice", type="reply", reply=rid, text=text))
        FEED.put(LiveEvent(channel="voice", type="reply_end", reply=rid))
        await worker.queue_frames([TTSSpeakFrame(text)])
    brain.verifier.deliver = correct

    async def filler(text: str):
        """An extension tool's own line covers its wait, spoken by code, never kept as said."""
        await worker.queue_frames([TTSSpeakFrame(text, append_to_context=False)])
    brain.on_slow = filler

    async def speak_up(ev: dict):
        """A nudge: the chime, then he words it with this conversation's brain from its note. If he has
        not started within WORDING_SECS, the plain line instead."""
        logger.info(f"nudge {ev['id']}: {ev['say']!r}")
        before = watch.last
        frames = [OutputAudioRawFrame(audio=CHIME, sample_rate=CFG.tts.sample_rate, num_channels=1)] if CHIME else []
        can = any(t.name == "follow_up" for t in brain.tools.standard_tools)
        frames.append(LLMMessagesAppendFrame(messages=[{"role": "user", "content": nudge_note(ev, can)}], run_llm=True))
        await worker.queue_frames(frames)
        t0 = time.monotonic()
        while time.monotonic() - t0 < WORDING_SECS:
            await asyncio.sleep(0.1)
            if watch.last > before:
                return
        logger.warning(f"nudge {ev['id']}: not worded within {WORDING_SECS:g} s; the plain line instead")
        await worker.queue_frames([TTSSpeakFrame(ev["say"])])

    async def announcements():
        """Announcements and nudges from winstond while this browser is connected. An announcement:
        the note first, so a reply has context, then the chime and the text spoken as is."""
        async for ev in subscribe(CFG, "voice", room):
            if ev.get("type") == "nudge":
                await speak_up(ev)
                continue
            logger.info(f"announcement: {ev['text']!r}")
            frames = [LLMMessagesAppendFrame(messages=[{"role": "user", "content": note(ev["text"], ev["context"])}],
                                             run_llm=False)]
            if CHIME:
                frames.append(OutputAudioRawFrame(audio=CHIME, sample_rate=CFG.tts.sample_rate, num_channels=1))
            frames.append(TTSSpeakFrame(ev["text"]))
            await worker.queue_frames(frames)

    async def room_mic():
        """Follow the room's mic: closed while its box has it."""
        async for ev in mic_events(CFG, room):
            if gate.set(ev.get("held_by")):
                logger.info(f"{room}: mic {'paused for the ' + ev['held_by'] if gate.closed else 'back'}")

    listener: asyncio.Task | None = None
    follower = asyncio.create_task(room_mic()) if room else None
    warming: set[asyncio.Task] = set()          # held so the warm-up task is not collected mid-way

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        nonlocal listener
        logger.info(f"client connected{f' in {room}' if room else ''}")
        # Not in the conversation: a panel that reconnected ten times left ten of these in the room's
        # history, and the model took "Winston online." for an answer (2026-10-01).
        await worker.queue_frames([TTSSpeakFrame(f"{CFG.assistant.name} online.", append_to_context=False)])
        warming.add(t := asyncio.create_task(brain.warm())); t.add_done_callback(warming.discard)
        warming.add(t := asyncio.create_task(prerender())); t.add_done_callback(warming.discard)
        if CFG.announce.enabled:
            listener = asyncio.create_task(announcements())

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("client disconnected")
        if listener:
            listener.cancel()
        if follower:
            follower.cancel()
        FEED.put(LiveEvent(channel="voice", type="state", state="idle"))
        turns.flush()
        await worker.cancel()
        await brain.close()

    await WorkerRunner(handle_sigint=runner_args.handle_sigint).run(worker)


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    build_stt()                                   # load STT before serving, not on first connection
    from pipecat.runner.run import main
    main()
