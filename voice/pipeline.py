"""Winston voice pipeline (M2): browser mic -> Parakeet STT -> vLLM brain -> Chatterbox-Turbo
TTS -> browser speaker, with VAD-driven barge-in and per-turn latency logging.

Run from the repo root with vLLM (:8000) and the TTS server (:8880) already up:

    .venv/bin/python -m voice.pipeline        # then open http://localhost:7860

Every model sits behind an adapter chosen in config.yaml (plan §6, rule 1).
"""
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.observers.loggers.transcription_log_observer import TranscriptionLogObserver
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.workers.runner import WorkerRunner
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams

from brain.agent import CFG, ROOT, Brain, ToolCallLogObserver, build_llm  # noqa: F401  (build_llm re-exported for tests)
from voice.stt.parakeet import ParakeetSTTService, load_parakeet

STT_MODEL = load_parakeet(CFG["stt"]["model"], CFG["stt"].get("quantization", "int8"))  # once per process

transport_params = {
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True,
        audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=CFG["vad"].get("stop_secs", 0.3))),
    ),
}


def build_stt():
    c = CFG["stt"]
    assert c["provider"] == "parakeet", f"unsupported stt provider {c['provider']}"
    return ParakeetSTTService(model=STT_MODEL, model_name=c["model"])



def build_tts():
    c = CFG["tts"]
    assert c["provider"] == "chatterbox_turbo", f"unsupported tts provider {c['provider']}"
    return OpenAITTSService(api_key="local", base_url=c["server_url"], model="chatterbox-turbo",
                            voice=c.get("voice", "winston"), sample_rate=24000)


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    brain = Brain(); await brain.start()          # LLM + Home Assistant tools
    stt, llm, tts = build_stt(), brain.llm, build_tts()
    context = brain.new_context()
    aggregators = LLMContextAggregatorPair(context)

    pipeline = Pipeline([
        transport.input(),      # mic audio from the browser (+ VAD)
        stt,                    # utterance -> text
        aggregators.user(),     # text -> context
        llm,                    # context -> streamed text
        tts,                    # sentences -> 24 kHz pcm
        transport.output(),     # speaker audio to the browser
        aggregators.assistant(),
    ])
    task = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[UserBotLatencyObserver(), TranscriptionLogObserver(), ToolCallLogObserver(channel="voice")],
        idle_timeout_secs=None,
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("client connected")
        await task.queue_frames([TTSSpeakFrame("Winston online.")])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("client disconnected")
        await task.cancel()
        await brain.close()

    await WorkerRunner(handle_sigint=runner_args.handle_sigint).run(task)


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main
    main()
