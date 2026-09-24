"""Voice channel: browser mic -> STT -> brain -> TTS -> browser speaker, with VAD-driven barge-in
and per-turn latency logging, on Pipecat's SmallWebRTC transport. Runs as its own process,
started by the supervisor:

    python -m winston.channels.voice --host 127.0.0.1 --port 7860

Every model sits behind an adapter chosen in config (plan §6, rule 1)."""
import asyncio
import wave

import numpy as np
from loguru import logger
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (ErrorFrame, LLMMessagesAppendFrame, OutputAudioRawFrame, TTSAudioRawFrame,
                                   TTSSpeakFrame)
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
from winston.core.brain import Brain, ToolCallLogObserver, api_key
from winston.channels.clone import MicTap, make_voice_tools
from winston.core.announce import note, subscribe
from winston.core.guard import ClaimGuard
from winston.core.live import FeedPoster, LiveEvent, LiveObserver

CFG: WinstonConfig = load()
_STT_MODEL = None


def build_stt(cfg: WinstonConfig = CFG):
    """STT adapter from the registry; the model loads once per process (about 10 s)."""
    global _STT_MODEL
    cls = get_adapter("stt", cfg.stt.provider)
    if _STT_MODEL is None:
        _STT_MODEL = cls.load_model(cfg.stt.model, cfg.stt.quantization)
    return cls(model=_STT_MODEL, model_name=cfg.stt.model)


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


CHIME = load_chime()
FEED = FeedPoster(CFG)          # what this channel hears and says, for the Talk page


transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    brain = Brain(CFG, channel="voice"); await brain.start()
    stt, tts = build_stt(), build_tts()
    tap = MicTap()                  # lets clone_voice record a friend without it becoming a turn

    async def transcribe(pcm):
        async for f in stt.run_stt(pcm.tobytes()):
            if f is not None and getattr(f, "text", None):
                yield f.text
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
        brain.llm,              # context -> streamed text, tool calls
        guard,                  # retracts claims no tool call backs
        tts,                    # sentences -> pcm
        transport.output(),     # speaker audio to the browser
        aggregators.assistant(),
    ])
    worker = PipelineWorker(
        pipeline, params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[guard.observer, UserBotLatencyObserver(), TranscriptionLogObserver(),
                   ToolCallLogObserver(CFG, channel="voice"), LiveObserver("voice", FEED.put, spoken=True)],
        idle_timeout_secs=None,
    )

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

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        nonlocal listener
        logger.info("client connected")
        await worker.queue_frames([TTSSpeakFrame(f"{CFG.assistant.name} online.")])
        if CFG.announce.enabled:
            listener = asyncio.create_task(announcements())

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("client disconnected")
        if listener:
            listener.cancel()
        FEED.put(LiveEvent(channel="voice", type="state", state="idle"))
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
