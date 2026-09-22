"""Winston TTS adapter: OpenAI-compatible /v1/audio/speech on top of Chatterbox-Turbo.

Runs in its own venv (voice/tts/chatterbox_server/.venv) so its torch pins never
touch the pipeline venv. Pipecat's OpenAITTSService talks to it with
response_format=pcm and expects 24 kHz mono int16, which is Chatterbox's native rate.

    .venv/bin/python server.py --voice ../reference/vctk/p232_023.wav --port 8880

Voice conditionals are prepared once per reference clip and cached, so a request may
name any clip under --voices-dir (e.g. "vctk/p226_023") to switch voices.
"""
import argparse
import asyncio
import io
import logging
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

log = logging.getLogger("winston.tts")
app = FastAPI(title="Winston TTS (Chatterbox-Turbo)")

_model = None
_lock = threading.Lock()          # one generate at a time on the GPU
_conds: dict[str, object] = {}    # voice name -> prepared Conditionals
_default_voice = ""
_voices_dir = Path(".")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


class SpeechRequest(BaseModel):
    input: str
    model: str | None = None
    voice: str | None = None
    response_format: str = "pcm"      # pcm (24 kHz s16le mono) or wav
    speed: float | None = None        # accepted, ignored
    instructions: str | None = None   # accepted, ignored


_OPENAI_VOICES = {"alloy", "ash", "ballad", "cedar", "coral", "echo", "fable", "marin", "nova", "onyx",
                  "sage", "shimmer", "verse", "default", "winston"}


def _resolve_voice(name: str | None) -> str:
    # OpenAI clients validate voice names against OpenAI's list, so any of those means "the default clip"
    if not name or name in _OPENAI_VOICES:
        return _default_voice
    if name in _conds:
        return name
    path = (_voices_dir / name).with_suffix(".wav")
    if not path.is_file():
        raise HTTPException(400, f"unknown voice {name!r}; no file {path}")
    t0 = time.perf_counter()
    _model.prepare_conditionals(str(path))
    _conds[name] = _model.conds
    log.info("prepared voice %s in %.0f ms", name, (time.perf_counter() - t0) * 1000)
    return name


def _synth(text: str, voice: str) -> np.ndarray:
    """Synthesize one sentence; returns int16 mono at _model.sr."""
    with _lock:
        _model.conds = _conds[voice]
        with torch.inference_mode():
            wav = _model.generate(text)
    if isinstance(wav, torch.Tensor):
        wav = wav.detach().cpu().numpy()
    wav = np.asarray(wav, dtype=np.float32).reshape(-1)
    return (np.clip(wav, -1.0, 1.0) * 32767).astype(np.int16)


def _sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENTENCE_RE.split(text.strip()) if s.strip()]
    return parts or [text.strip()]


@app.get("/health")
async def health():
    return {"status": "ok", "sample_rate": _model.sr, "default_voice": _default_voice,
            "voices_loaded": sorted(_conds)}


@app.post("/v1/audio/speech")
async def speech(req: SpeechRequest):
    if not req.input.strip():
        raise HTTPException(400, "empty input")
    voice = _resolve_voice(req.voice)
    sentences = _sentences(req.input)

    async def pcm_chunks():
        t_req = time.perf_counter()
        for i, s in enumerate(sentences):
            t0 = time.perf_counter()
            audio = await asyncio.to_thread(_synth, s, voice)
            dt = time.perf_counter() - t0
            secs = len(audio) / _model.sr
            log.info("tts %s chunk %d/%d %d chars -> %.2fs audio in %.0f ms (rtf %.2f)%s",
                     voice, i + 1, len(sentences), len(s), secs, dt * 1000, dt / max(secs, 1e-3),
                     f", ttfb {(time.perf_counter() - t_req) * 1000:.0f} ms" if i == 0 else "")
            yield audio.tobytes()

    if req.response_format == "wav":
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(_model.sr)
            async for chunk in pcm_chunks():
                w.writeframes(chunk)
        return Response(buf.getvalue(), media_type="audio/wav")
    if req.response_format != "pcm":
        raise HTTPException(400, "response_format must be pcm or wav")
    return StreamingResponse(pcm_chunks(), media_type="audio/pcm",
                             headers={"X-Sample-Rate": str(_model.sr)})


def main():
    global _model, _default_voice, _voices_dir
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", required=True, help="reference clip (wav) for the default voice")
    ap.add_argument("--voices-dir", default=None, help="dir that request 'voice' names resolve under")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8880)
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from chatterbox.tts_turbo import ChatterboxTurboTTS
    t0 = time.perf_counter()
    _model = ChatterboxTurboTTS.from_pretrained(device=args.device)
    log.info("model loaded in %.1f s, sr=%d", time.perf_counter() - t0, _model.sr)

    voice_path = Path(args.voice).resolve()
    _voices_dir = Path(args.voices_dir).resolve() if args.voices_dir else voice_path.parent
    _default_voice = str(voice_path.relative_to(_voices_dir).with_suffix("")) \
        if voice_path.is_relative_to(_voices_dir) else voice_path.stem
    t0 = time.perf_counter()
    _model.prepare_conditionals(str(voice_path))
    _conds[_default_voice] = _model.conds
    log.info("default voice %s prepared in %.0f ms", _default_voice, (time.perf_counter() - t0) * 1000)

    for s in ("Hello.", "Hello, I'm Winston. How can I help?"):   # warm up kernels
        t0 = time.perf_counter(); a = _synth(s, _default_voice)
        log.info("warmup %r: %.2fs audio in %.0f ms", s, len(a) / _model.sr, (time.perf_counter() - t0) * 1000)
    if args.device == "cuda":
        log.info("gpu memory: %.2f GB allocated, %.2f GB reserved",
                 torch.cuda.memory_allocated() / 1e9, torch.cuda.memory_reserved() / 1e9)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
