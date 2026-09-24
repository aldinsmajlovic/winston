"""Winston TTS adapter: OpenAI-compatible /v1/audio/speech on top of Kyutai Pocket TTS, on the CPU.

Same protocol as the Chatterbox server, so `tts.provider: openai_speech` works unchanged. Pocket
TTS streams audio while it generates (about 70 ms to the first chunk on a desktop CPU, a quarter
of real time on four threads), so each sentence starts playing before it is finished.

    .venv/bin/python server.py --voice george --port 8880                  # a built-in voice
    .venv/bin/python server.py --voice /path/voices/vctk/p254_023.wav --voices-dir /path/voices

Built-in voices need nothing beyond the model download. Cloning from a clip needs the gated
weights: accept the terms at https://huggingface.co/kyutai/pocket-tts, then `uvx hf auth login`.
"""
import argparse
import asyncio
import io
import logging
import queue
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
app = FastAPI(title="Winston TTS (Pocket TTS)")

_model = None
_lock = threading.Lock()          # generate_audio_stream is not thread-safe
_states: dict[str, object] = {}   # voice name -> model state
_default_voice = ""
_voices_dir = Path(".")
_catalog: list[str] = []          # built-in voices
_cloning: bool | None = None      # None until a clip has been tried
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# Names Kyutai ships; refreshed from the package at start when it exposes them.
_FALLBACK_CATALOG = ["alba", "anna", "azelma", "bill_boerst", "caro_davy", "charles", "cosette", "eponine", "estelle",
                     "eve", "fantine", "george", "giovanni", "jane", "javert", "jean", "juergen", "lola", "marius",
                     "mary", "michael", "paul", "peter_yearsley", "rafael", "stuart_bell", "vera"]


class SpeechRequest(BaseModel):
    input: str
    model: str | None = None
    voice: str | None = None
    response_format: str = "pcm"      # pcm (24 kHz s16le mono) or wav
    speed: float | None = None        # accepted, ignored
    instructions: str | None = None   # accepted, ignored


_OPENAI_VOICES = {"alloy", "ash", "ballad", "cedar", "coral", "echo", "fable", "marin", "nova", "onyx",
                  "sage", "shimmer", "verse", "default", "winston"}


def _clip_path(name: str) -> Path:
    """The clip a voice name means, refused (400) unless it stays under --voices-dir."""
    parts = Path(name).parts
    if "\\" in name or "\0" in name or Path(name).is_absolute() or ".." in parts or not parts:
        raise HTTPException(400, f"voice {name!r} is not a clip name under the voices directory")
    return (_voices_dir / name).with_suffix(".wav")


def _load_voice(name: str) -> None:
    """A built-in voice by name, or a clip under --voices-dir (voice cloning)."""
    global _cloning
    if name in _catalog:
        source, clip = name, False
    else:
        path = _clip_path(name)
        if not path.is_file():
            raise HTTPException(400, f"unknown voice {name!r}: not a built-in voice and no file {path}")
        source, clip = str(path), True
    t0 = time.perf_counter()
    try:
        with _lock:
            _states[name] = _model.get_state_for_audio_prompt(source)
    except ValueError as e:               # the cloning weights are gated behind Kyutai's terms
        if clip:
            _cloning = False
        raise HTTPException(400, str(e)) from e
    if clip:
        _cloning = True
    log.info("prepared voice %s in %.0f ms", name, (time.perf_counter() - t0) * 1000)


def _resolve_voice(name: str | None) -> str:
    # OpenAI clients validate voice names against OpenAI's list, so any of those means "the default voice"
    if not name or name in _OPENAI_VOICES:
        return _default_voice
    if name not in _states:
        _load_voice(name)
    return name


def _synth_stream(text: str, voice: str, out: queue.Queue) -> None:
    """Runs in a thread: pushes int16 PCM chunks as they are generated, an exception on failure, then None."""
    try:
        # no torch.inference_mode() here: the model's own decode thread updates the (cloned) state in place,
        # and tensors cloned under inference mode cannot be updated outside it
        with _lock:
            for chunk in _model.generate_audio_stream(_states[voice], text):
                a = np.asarray(chunk.detach().cpu().numpy(), dtype=np.float32).reshape(-1)
                out.put((np.clip(a, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
    except Exception as e:                # noqa: BLE001, reported to the request
        out.put(e)
    finally:
        out.put(None)


def _sentences(text: str) -> list[str]:
    parts = [s.strip() for s in _SENTENCE_RE.split(text.strip()) if s.strip()]
    return parts or [text.strip()]


@app.get("/health")
async def health():
    return {"status": "ok", "sample_rate": _model.sample_rate, "default_voice": _default_voice,
            "voices_loaded": sorted(_states), "builtin_voices": _catalog, "cloning": _cloning}


@app.post("/v1/audio/speech")
async def speech(req: SpeechRequest):
    if not req.input.strip():
        raise HTTPException(400, "empty input")
    voice = _resolve_voice(req.voice)
    sentences = _sentences(req.input)

    async def pcm_chunks():
        t_req = time.perf_counter()
        for i, s in enumerate(sentences):
            q: queue.Queue = queue.Queue()
            threading.Thread(target=_synth_stream, args=(s, voice, q), daemon=True).start()
            t0, first, n = time.perf_counter(), None, 0
            while (item := await asyncio.to_thread(q.get)) is not None:
                if isinstance(item, Exception):
                    raise item
                if first is None:
                    first = time.perf_counter() - t0
                n += len(item) // 2
                yield item
            dt, secs = time.perf_counter() - t0, n / _model.sample_rate
            log.info("tts %s chunk %d/%d %d chars -> %.2fs audio in %.0f ms (rtf %.2f, first %.0f ms)%s",
                     voice, i + 1, len(sentences), len(s), secs, dt * 1000, dt / max(secs, 1e-3),
                     (first or 0) * 1000, f", ttfb {(time.perf_counter() - t_req) * 1000:.0f} ms" if i == 0 else "")

    if req.response_format == "wav":
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(_model.sample_rate)
            async for chunk in pcm_chunks():
                w.writeframes(chunk)
        return Response(buf.getvalue(), media_type="audio/wav")
    if req.response_format != "pcm":
        raise HTTPException(400, "response_format must be pcm or wav")
    return StreamingResponse(pcm_chunks(), media_type="audio/pcm",
                             headers={"X-Sample-Rate": str(_model.sample_rate)})


def main():
    global _model, _default_voice, _voices_dir, _catalog
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="george", help="built-in voice name, or a reference clip (wav) to clone")
    ap.add_argument("--voices-dir", default=None, help="dir that request 'voice' names resolve under")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8880)
    ap.add_argument("--threads", type=int, default=4, help="CPU threads for generation")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from pocket_tts import TTSModel
    from pocket_tts.models import tts_model as _tm
    torch.set_num_threads(args.threads)
    t0 = time.perf_counter()
    _model = TTSModel.load_model()
    _model.eval()
    known = getattr(_tm, "_ORIGINS_OF_PREDEFINED_VOICES", None)
    _catalog = sorted(known) if isinstance(known, dict) and known else _FALLBACK_CATALOG
    log.info("model loaded in %.1f s, sample_rate %d, %d built-in voices, %d threads",
             time.perf_counter() - t0, _model.sample_rate, len(_catalog), args.threads)

    voice_path = Path(args.voice)
    if voice_path.suffix.lower() == ".wav" or voice_path.is_file():
        voice_path = voice_path.resolve()
        _voices_dir = Path(args.voices_dir).resolve() if args.voices_dir else voice_path.parent
        _default_voice = str(voice_path.relative_to(_voices_dir).with_suffix("")) \
            if voice_path.is_relative_to(_voices_dir) else voice_path.stem
        _states[_default_voice] = None  # placeholder so the name resolves; loaded right below
        del _states[_default_voice]
    else:
        _voices_dir = Path(args.voices_dir).resolve() if args.voices_dir else Path.cwd()
        _default_voice = args.voice
    try:
        _load_voice(_default_voice)
    except HTTPException as e:
        raise SystemExit(f"default voice {_default_voice!r}: {e.detail}") from e

    for s in ("Hello.", "Hello, I'm Winston. How can I help?"):   # warm up
        q: queue.Queue = queue.Queue(); t0 = time.perf_counter(); n = 0
        threading.Thread(target=_synth_stream, args=(s, _default_voice, q)).start()
        while (item := q.get()) is not None:
            if isinstance(item, Exception):
                raise item
            n += len(item) // 2
        log.info("warmup %r: %.2fs audio in %.0f ms", s, n / _model.sample_rate, (time.perf_counter() - t0) * 1000)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
