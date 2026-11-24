"""STT adapter: NVIDIA Parakeet TDT 0.6B (ONNX, INT8, CPU) via onnx-asr, or a Hugging Face repo in onnx-asr's
layout (a Parakeet fine-tune such as Fluid Speech Pro). It transcribes an utterance in well under a second on the
CPU, keeping the GPU for the LLM and TTS."""
import json
import time
from pathlib import Path

import numpy as np
import onnx_asr
import onnxruntime
from loguru import logger


def local_repo(repo: str, quantization: str | None) -> Path:
    """A Hub repo in onnx-asr's layout as real files, downloaded once. onnx-asr can load such a repo by name, but
    the Hub cache keeps files as links, and onnxruntime refuses weights (.onnx.data) reached through one."""
    from huggingface_hub import constants, snapshot_download
    where = Path(constants.HF_HUB_CACHE).parent / "onnx-asr" / repo.replace("/", "--")
    # ponytail: downloaded once and kept; delete the folder to fetch a newer version of the repo
    if not (where / "config.json").is_file():
        q = f".{quantization}." if quantization else None
        snapshot_download(repo, local_dir=where,
                          allow_patterns=[f"*{q}onnx*" if q else "*.onnx*", "vocab.txt", "config.json"],
                          ignore_patterns=["*clean*"] + ([] if q else ["*.int8.*"]))
    return where


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
        if "/" in model_name:                       # a Hub repo; onnx-asr's own names have no slash
            path = local_repo(model_name, quantization)
            kind = json.loads((path / "config.json").read_text())["model_type"]
            model = onnx_asr.load_model(kind, path, quantization=quantization, sess_options=so)
        else:
            model = onnx_asr.load_model(model_name, quantization=quantization, sess_options=so)
        model.recognize(np.zeros(16000, dtype=np.float32), sample_rate=16000)   # warm up
        logger.info(f"Parakeet {model_name} ({quantization}) loaded in {time.perf_counter() - t0:.1f}s")
        return model

    @staticmethod
    def transcribe(model, pcm: np.ndarray, rate: int, hints: list[str] | None = None) -> str:
        return (model.recognize(pcm.astype(np.float32) / 32768.0, sample_rate=rate) or "").strip()
