"""STT adapter: NVIDIA Parakeet TDT 0.6B (ONNX, INT8, CPU) via onnx-asr. It transcribes an utterance
in well under a second on the CPU, keeping the GPU for the LLM and TTS."""
import time

import numpy as np
import onnx_asr
import onnxruntime
from loguru import logger


class Parakeet:
    """The STT adapter contract (docs/architecture.md): `load_model(stt)` takes the stt settings and returns
    a reusable model object, `transcribe(model, pcm, rate, hints=None)` turns int16 audio into text, and
    `supports_hints` says whether the engine can be told words in advance; one that can maps `hints` (a
    list of words, or None) to its own setting: Whisper's initial prompt or hotwords, a cloud engine's
    keyterms.

    That is all an engine does. Cutting utterances in the voice pipeline, the hint-word correction, the
    STT log line and the turn record are Winston's own and the same for every engine (core/hints.py:
    Hearing; channels/voice.py: HearingSTT)."""

    supports_hints = False          # greedy decoding in onnx-asr has no biasing hook

    @staticmethod
    def load_model(stt):
        model_name, quantization = stt.model, stt.quantization
        t0 = time.perf_counter()
        so = onnxruntime.SessionOptions()
        # Idle threads sleep instead of spinning: beside Smart Turn, spinning made both slower (about 30 ms)
        so.add_session_config_entry("session.intra_op.allow_spinning", "0")
        model = onnx_asr.load_model(model_name, quantization=quantization, sess_options=so)
        model.recognize(np.zeros(16000, dtype=np.float32), sample_rate=16000)   # warm up
        logger.info(f"Parakeet {model_name} ({quantization}) loaded in {time.perf_counter() - t0:.1f}s")
        return model

    @staticmethod
    def transcribe(model, pcm: np.ndarray, rate: int, hints: list[str] | None = None) -> str:
        return (model.recognize(pcm.astype(np.float32) / 32768.0, sample_rate=rate) or "").strip()
