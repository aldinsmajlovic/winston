"""Same-voice check for cloning: is the person who said the consent line the person in the voice sample?

A speaker-embedding model turns each recording into a vector of how the voice sounds, whatever is said,
and the cosine similarity of the two vectors is the score. At SAME_VOICE or above the clone is kept
with its score; below, clone_voice keeps nothing.

The model is WeSpeaker's ResNet34 trained on VoxCeleb2 (Wespeaker/wespeaker-voxceleb-resnet34 on
Hugging Face, 26.5 MB ONNX, 256 numbers per voice), run on the CPU through onnx-asr's
WespeakerEmbeddings, which the STT already brings. It loads on first use, downloading to the Hugging
Face cache the first time. It is optional: when it cannot load, compare() returns None and the clone
is stored as not checked rather than refused.

SAME_VOICE was set on the clips in data/voices: a consent-length piece (3 or 5 s) of one recording
against a different recording of the same reader scored 0.61 to 0.91 (52 pairs, Aldin's real consent
and sample 0.79); different speakers scored -0.09 to 0.54 (370 pairs, the highest two older English
audiobook readers). A one-person home cannot measure how close a real second voice comes."""
import threading
import time

import numpy as np
from loguru import logger

MODEL = "Wespeaker/wespeaker-voxceleb-resnet34"
SAME_VOICE = 0.55           # cosine similarity a clone needs; see the module docstring
MIN_SECS = 1.0              # less speech than this says nothing about a voice

_lock = threading.Lock()
_model = None
_failed: str | None = None


def load():
    """The embedding model, loaded once; None when it cannot be (no network on first use, removed files).
    A failure is logged once and not retried until the process restarts."""
    global _model, _failed
    with _lock:
        if _model is None and _failed is None:
            t0 = time.perf_counter()
            try:
                from onnx_asr.loader import Manager
                _model = Manager().create_se(MODEL)
                logger.info(f"speaker: {MODEL} loaded in {time.perf_counter() - t0:.1f}s")
            except Exception as e:  # noqa: BLE001  (any failure means not checked, never a failed clone)
                _failed = f"{type(e).__name__}: {e}"
                logger.warning(f"speaker: {MODEL} not available, clones are stored as not checked ({_failed})")
        return _model


def warm():
    """Start loading in the background, so the model is ready by the time the sample is recorded."""
    threading.Thread(target=load, name="speaker-load", daemon=True).start()


def embed(model, pcm: np.ndarray, rate: int) -> np.ndarray:
    e = model.embedding(pcm.astype(np.float32) / 32768.0, sample_rate=rate)
    return e / max(float(np.linalg.norm(e)), 1e-9)


def compare(consent: np.ndarray, sample: np.ndarray, rate: int = 16000) -> float | None:
    """Cosine similarity of the two voices, -1 to 1, from int16 audio; None when the model is not
    available or either recording is too short to say."""
    if min(len(consent), len(sample)) < MIN_SECS * rate or (model := load()) is None:
        return None
    return round(float(embed(model, consent, rate) @ embed(model, sample, rate)), 3)


def same_voice(score: float | None) -> bool | None:
    return None if score is None else score >= SAME_VOICE
