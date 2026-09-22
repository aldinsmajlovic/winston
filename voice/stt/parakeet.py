"""STT adapter: NVIDIA Parakeet TDT 0.6B (ONNX, INT8, CPU) via onnx-asr, as a Pipecat
segmented STT service. Pipecat buffers audio while the VAD says the user is speaking
and hands the whole utterance to run_stt() once they stop; Parakeet then transcribes
it in well under a second on the CPU, keeping the GPU for the LLM and TTS.
"""
import asyncio
import time
from collections.abc import AsyncGenerator

import numpy as np
import onnx_asr
from loguru import logger
from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import SegmentedSTTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601


def load_parakeet(model_name: str = "nemo-parakeet-tdt-0.6b-v3", quantization: str | None = "int8"):
    """Load and warm up the ONNX model once per process; takes ~10 s, so do it at startup."""
    t0 = time.perf_counter()
    model = onnx_asr.load_model(model_name, quantization=quantization)
    model.recognize(np.zeros(16000, dtype=np.float32), sample_rate=16000)
    logger.info(f"Parakeet {model_name} ({quantization}) loaded in {time.perf_counter() - t0:.1f}s")
    return model


class ParakeetSTTService(SegmentedSTTService):
    def __init__(self, *, model=None, model_name: str = "nemo-parakeet-tdt-0.6b-v3",
                 quantization: str | None = "int8", sample_rate: int | None = None, **kwargs):
        super().__init__(sample_rate=sample_rate,
                         settings=STTSettings(model=model_name, language=Language.EN), **kwargs)
        self._model = model if model is not None else load_parakeet(model_name, quantization)

    @property
    def wants_wav_segments(self) -> bool:
        return False  # run_stt gets raw int16 PCM, not a WAV container

    def can_generate_metrics(self) -> bool:
        return True

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        await self.start_processing_metrics()
        await self.start_ttfb_metrics()
        pcm = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        t0 = time.perf_counter()
        text = await asyncio.to_thread(self._model.recognize, pcm, sample_rate=self.sample_rate)
        await self.stop_ttfb_metrics()
        await self.stop_processing_metrics()
        text = (text or "").strip()
        logger.info(f"STT {len(pcm) / self.sample_rate:.2f}s audio -> {text!r} "
                    f"in {(time.perf_counter() - t0) * 1000:.0f} ms")
        if text:
            yield TranscriptionFrame(text, self._user_id, time_now_iso8601(), "en")
