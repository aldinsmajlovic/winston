"""Voice channel: browser mic -> STT -> brain -> TTS -> browser speaker, with VAD-driven barge-in
and per-turn latency logging, on Pipecat's SmallWebRTC transport. Runs as its own process,
started by the supervisor:

    python -m winston.channels.voice --host 127.0.0.1 --port 7860

Every model sits behind an adapter chosen in config (plan §6, rule 1)."""
import asyncio
import json
import time
import uuid
import wave

import numpy as np
from loguru import logger
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (ErrorFrame, FunctionCallResultFrame, FunctionCallsStartedFrame,
                                   LLMFullResponseEndFrame, LLMMessagesAppendFrame, LLMTextFrame, OutputAudioRawFrame,
                                   TranscriptionFrame, TTSAudioRawFrame, TTSSpeakFrame, VADUserStoppedSpeakingFrame)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.observers.loggers.transcription_log_observer import TranscriptionLogObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import (LLMContextAggregatorPair,
                                                                    LLMUserAggregatorParams)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.openai.tts import VALID_VOICES, OpenAITTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from winston.config import load, resolve
from winston.config.registry import get as get_adapter
from winston.config.schema import WinstonConfig
from winston.core.brain import Brain, Clock, ToolCallLogObserver, api_key
from winston.channels.clone import MicTap, make_voice_tools
from winston.core.announce import note, subscribe
from winston.core.guard import ClaimGuard
from winston.core.hints import Hearing
from winston.core.live import FeedPoster, LiveEvent, LiveObserver

CFG: WinstonConfig = load()
_HEARING: Hearing | None = None


def hearing(cfg: WinstonConfig = CFG) -> Hearing:
    """The process's one STT model with the hint words; the model loads once (about 10 s)."""
    global _HEARING
    if _HEARING is None:
        cls = get_adapter("stt", cfg.stt.provider)
        _HEARING = Hearing(cfg, cls, cls.load_model(cfg.stt.model, cfg.stt.quantization))
    return _HEARING


def build_stt(cfg: WinstonConfig = CFG):
    """The pipeline's STT service from the registry, on the shared model and hint words."""
    h = hearing(cfg)
    return h.adapter(model=h.model, model_name=cfg.stt.model, hearing=h)


async def transcribe(pcm: np.ndarray, extra: list[str]) -> str:
    """16 kHz int16 to corrected text outside the pipeline (clone consent), with extra hint words."""
    return await hearing().transcribe(pcm, 16000, label="clone: STT", extra=extra)


class SpeechTTS(OpenAITTSService):
    """OpenAI's speech protocol with any voice name. Pipecat only lets OpenAI's own names through,
    but a local server names its clips by path ("vctk/p226_023", "friends/erik"). Text with nothing
    to say is skipped."""

    async def run_tts(self, text: str, context_id: str):
        if not any(c.isalnum() for c in text):      # the LLM sometimes streams a lone "." before a tool call
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
    if c.provider != "openai_speech":
        raise ValueError(f"unsupported tts provider {c.provider}")
    return SpeechTTS(api_key=api_key(c.api_key_env), base_url=c.base_url, sample_rate=c.sample_rate,
                     settings=SpeechTTS.Settings(model="winston-tts", voice=c.voice))


def load_chime(cfg: WinstonConfig = CFG) -> bytes:
    """The announcement chime as 16-bit mono PCM at the TTS sample rate, or b"" for none."""
    if not cfg.announce.chime:
        return b""
    try:
        with wave.open(str(resolve(cfg.announce.chime))) as w:
            if w.getsampwidth() != 2:
                raise ValueError("chime must be 16-bit PCM")
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32)
            if w.getnchannels() > 1:
                x = x.reshape(-1, w.getnchannels()).mean(axis=1)
            rate = w.getframerate()
    except (OSError, ValueError, wave.Error) as e:
        logger.warning(f"no announcement chime: {e}")
        return b""
    if rate != cfg.tts.sample_rate:                  # linear resampling is plenty for a chime
        n = int(len(x) * cfg.tts.sample_rate / rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return x.astype("<i2").tobytes()


class LatencyLog(BaseObserver):
    """One JSON line per turn in logging.latency: milliseconds from the end of speech to the transcript
    (stt_ms), the first LLM token and the first audio out, and the tools the turn called. The end of
    speech is the VAD's decision minus its stop_secs, the moment the user actually went quiet. A turn
    is written once it has spoken and no tool call is left, or when the next one starts."""

    def __init__(self, cfg: WinstonConfig, channel: str):
        super().__init__()
        self._path = resolve(cfg.logging.latency) if cfg.logging.latency else None
        self._channel = channel
        self._turn: dict | None = None
        self._pending: set[str] = set()       # tool calls still running
        self._calls: set[str] = set()         # every call this turn, so a later hop of the frame is not a new one
        self._stt_frame: int | None = None
        self._done = False

    def begin(self, speech_end: float, stt_ms: float | None = None):
        """A new turn whose speech ended at speech_end (time.time()); a channel without STT frames
        passes its own transcript time."""
        self.flush()
        self._turn = {"ts": speech_end, "stt_ms": stt_ms, "first_token_ms": None, "first_audio_ms": None, "tools": []}
        self._pending, self._calls, self._done = set(), set(), False

    def _ms(self) -> int:
        return round((time.time() - self._turn["ts"]) * 1000)

    def flush(self):
        t, self._turn = self._turn, None
        if not self._path or not t or (t["stt_ms"] is None and t["first_token_ms"] is None):
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a") as f:
                f.write(json.dumps({"ts": round(t["ts"], 3), "channel": self._channel, **{k: v for k, v in t.items()
                                                                                          if k != "ts"}}) + "\n")
        except OSError as e:
            logger.warning(f"latency log: {e}")

    async def on_push_frame(self, data: FramePushed):
        if not self._path:
            return
        f, t = data.frame, self._turn
        if isinstance(f, VADUserStoppedSpeakingFrame):
            # a pause inside one request stops the VAD too; it is a new turn only once the reply began
            if t is None or t["first_token_ms"] is not None:
                self.begin(f.timestamp - f.stop_secs)
            else:
                t["ts"] = f.timestamp - f.stop_secs
        elif t is None:
            return
        elif isinstance(f, TranscriptionFrame) and t["first_token_ms"] is None and f.id != self._stt_frame:
            self._stt_frame = f.id          # observers see a frame once per hop; the first sighting counts
            t["stt_ms"] = self._ms()
        elif isinstance(f, LLMTextFrame) and t["first_token_ms"] is None:
            t["first_token_ms"] = self._ms()
        elif isinstance(f, TTSAudioRawFrame) and t["first_audio_ms"] is None:
            t["first_audio_ms"] = self._ms()
            if self._done and not self._pending:
                self.flush()
        elif isinstance(f, FunctionCallsStartedFrame):
            for c in f.function_calls:
                if c.tool_call_id not in self._calls:
                    self._calls.add(c.tool_call_id); self._pending.add(c.tool_call_id)
                    t["tools"].append(c.function_name)
                    self._done = False
        elif isinstance(f, FunctionCallResultFrame):
            self._pending.discard(f.tool_call_id)
            self._done = False                  # a result starts another LLM hop, which may call more tools
        elif isinstance(f, LLMFullResponseEndFrame):
            self._done = True
            if not self._pending and t["first_audio_ms"] is not None:
                self.flush()


CHIME = load_chime()
FEED = FeedPoster(CFG)          # what this channel hears and says, for the Talk page


transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    brain = Brain(CFG, channel="voice"); await brain.start()
    stt, tts = build_stt(), build_tts()
    tap = MicTap()                  # lets clone_voice record a friend without it becoming a turn
    for schema, handler in make_voice_tools(CFG, tap, transcribe):
        brain.add_tool(schema, handler)
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
    guard = ClaimGuard(CFG, channel="voice")
    latency = LatencyLog(CFG, "voice")

    @brain.llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(service, function_calls):
        # A web search or a question to Claude takes seconds; say so instead of leaving silence. Spoken by code, not the
        # model, so it cannot turn into a claim about the answer.
        names = {c.function_name for c in function_calls}
        if "ask_claude" in names:
            await worker.queue_frames([TTSSpeakFrame("Let me think about that.")])
        elif "web_search" in names:
            await worker.queue_frames([TTSSpeakFrame("Let me look that up.")])

    pipeline = Pipeline([
        transport.input(),      # mic audio from the browser
        tap,                    # held while a voice is being cloned
        stt,                    # utterance -> text
        aggregators.user(),     # VAD + turn taking; text -> context
        Clock(),                # the time in front of the newest user message
        brain.llm,              # context -> streamed text, tool calls
        guard,                  # holds back claims no tool call backs, retries once
        tts,                    # sentences -> pcm
        transport.output(),     # speaker audio to the browser
        aggregators.assistant(),
    ])
    worker = PipelineWorker(
        pipeline, params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[guard.observer, brain.verifier.observer, UserBotLatencyObserver(), TranscriptionLogObserver(),
                   ToolCallLogObserver(CFG, channel="voice"), LiveObserver("voice", FEED.put, spoken=True), latency],
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

    async def announcements():
        """Announcements from winstond while this browser is connected: the note first, so a reply
        has context, then the chime and the text spoken as is."""
        async for ev in subscribe(CFG, "voice"):
            logger.info(f"announcement: {ev['text']!r}")
            frames = [LLMMessagesAppendFrame(messages=[{"role": "user", "content": note(ev["text"], ev["context"])}],
                                             run_llm=False)]
            if CHIME:
                frames.append(OutputAudioRawFrame(audio=CHIME, sample_rate=CFG.tts.sample_rate, num_channels=1))
            frames.append(TTSSpeakFrame(ev["text"]))
            await worker.queue_frames(frames)

    listener: asyncio.Task | None = None
    warming: set[asyncio.Task] = set()          # held so the warm-up task is not collected mid-way

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        nonlocal listener
        logger.info("client connected")
        await worker.queue_frames([TTSSpeakFrame(f"{CFG.assistant.name} online.")])
        warming.add(t := asyncio.create_task(brain.warm())); t.add_done_callback(warming.discard)
        if CFG.announce.enabled:
            listener = asyncio.create_task(announcements())

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("client disconnected")
        if listener:
            listener.cancel()
        FEED.put(LiveEvent(channel="voice", type="state", state="idle"))
        latency.flush()
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
