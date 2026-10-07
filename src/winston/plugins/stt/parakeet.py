"""STT adapter: NVIDIA Parakeet TDT 0.6B (ONNX, INT8, CPU) via onnx-asr. It transcribes an utterance
in well under a second on the CPU, keeping the GPU for the LLM and TTS."""
import time

import numpy as np
import onnx_asr
from loguru import logger


class Parakeet:
    """The STT adapter contract (docs/architecture.md): `load_model(name, quantization)` returns a
    reusable model object, `transcribe(model, pcm, rate, hints=None)` turns int16 audio into text, and
    `supports_hints` says whether the engine can be told words in advance; one that can maps `hints` (a
    list of words, or None) to its own setting: Whisper's initial prompt or hotwords, a cloud engine's
    keyterms.

    That is all an engine does. Cutting utterances in the voice pipeline, the hint-word correction, the
    STT log line and the turn record are Winston's own and the same for every engine (core/hints.py:
    Hearing; channels/voice.py: HearingSTT)."""

    supports_hints = False          # greedy decoding in onnx-asr has no biasing hook

    @staticmethod
    def load_model(model_name: str = "nemo-parakeet-tdt-0.6b-v3", quantization: str | None = "int8"):
        t0 = time.perf_counter()
        model = onnx_asr.load_model(model_name, quantization=quantization)
        model.recognize(np.zeros(16000, dtype=np.float32), sample_rate=16000)   # warm up
        logger.info(f"Parakeet {model_name} ({quantization}) loaded in {time.perf_counter() - t0:.1f}s")
        return model

    @staticmethod
    def transcribe(model, pcm: np.ndarray, rate: int, hints: list[str] | None = None) -> str:
        return (model.recognize(pcm.astype(np.float32) / 32768.0, sample_rate=rate) or "").strip()
