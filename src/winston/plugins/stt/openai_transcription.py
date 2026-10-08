"""STT adapter: OpenAI's transcription protocol (POST {base_url}/audio/transcriptions), which vLLM serves for
Qwen3-ASR and hosted services speak. Winston runs no model itself: the server is an engine like the brain's.

The hint words go as the request's `prompt`. Qwen3-ASR reads it as context, so a name in it is expected
before it is said; a Whisper-style server reads it as the text that came before."""
import io
import os
import re
import wave

import httpx
import numpy as np
from loguru import logger

# Qwen3-ASR answers "language English<asr_text>..." when a server passes its raw output through.
_LANGUAGE_TAG = re.compile(r"^\s*language\s+\w+\s*<asr_text>", re.IGNORECASE)


class OpenAITranscription:
    """The STT adapter contract, as in parakeet.py, for a server instead of a model in this process."""

    supports_hints = True

    @staticmethod
    def load_model(stt) -> dict:
        if not stt.base_url:
            raise ValueError("stt.base_url is not set: the speech-to-text server's URL, ending in /v1")
        key = os.environ.get(stt.api_key_env, "").strip() if stt.api_key_env else ""
        client = httpx.Client(base_url=stt.base_url.rstrip("/"), timeout=30,
                              headers={"Authorization": f"Bearer {key}"} if key else {})
        logger.info(f"speech to text: {stt.model} at {stt.base_url}")
        return {"client": client, "model": stt.model}

    @staticmethod
    def transcribe(model: dict, pcm: np.ndarray, rate: int, hints: list[str] | None = None) -> str:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(pcm.astype("<i2").tobytes())
        data = {"model": model["model"], "response_format": "json"}
        if hints:
            data["prompt"] = ", ".join(hints)
        try:
            r = model["client"].post("/audio/transcriptions", data=data,
                                     files={"file": ("speech.wav", buf.getvalue(), "audio/wav")})
            r.raise_for_status()
        except httpx.HTTPError as e:            # heard as nothing, so he stays quiet instead of failing the turn
            why = e.response.text[:300] if isinstance(e, httpx.HTTPStatusError) else str(e)
            logger.warning(f"speech to text failed: {why}")
            return ""
        return _LANGUAGE_TAG.sub("", str(r.json().get("text") or "")).strip()
