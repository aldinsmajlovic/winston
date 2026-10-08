"""The OpenAI speech protocol shared by the speech servers in this directory.

Each engine (tts-chatterbox, tts-pocket, tts-qwen3, tts-voxcpm2, tts-gepard) is a small `Engine` in its
own venv; this module turns it into the server Winston's `openai_speech` provider and
tests/tts_bench/bench.py talk to:

- POST /v1/audio/speech with input, voice, response_format pcm (raw s16le mono, streamed) or wav.
  The rate is the model's own and is sent as X-Sample-Rate: 24 kHz for Chatterbox, Pocket and
  Qwen3-TTS, 48 kHz for VoxCPM2, 22.05 kHz for Gepard.
- voice is a built-in voice of the engine or a clip name under --voices-dir ("vctk/p254_023" means
  vctk/p254_023.wav), prepared on first use and cached (the default plus the eight most recently
  used), prepared again when the file changes. OpenAI's names ("alloy" and so on) mean the default.
  A name that leaves --voices-dir is refused with a 400.
- A request may carry stream true or false for itself, and the engine may take more of its own
  (Chatterbox's first_secs, chunk_secs, lookahead_secs); the answer says which way it rendered in
  X-Streaming.
- GET /health: status, sample_rate, default_voice, voices_loaded, builtin_voices, streaming, model,
  pid (so the benchmark can find the process on nvidia-smi) and the torch GPU memory.

Cloning with the clip's words (Qwen3-TTS, VoxCPM2) needs a transcript: a .txt beside the clip if there
is one, otherwise Parakeet (onnx-asr on the CPU, as Winston hears) transcribes it once (`transcript`).
Nothing is written into the voices directory. Models whose generate() returns only at the end stream
through `frames_from` and `stream_frames`.

Engines import it with services/ on sys.path; it needs only what every engine's venv has, and imports
the rest (onnx_asr, soundfile) only when an engine asks for a transcript.
"""
import argparse
import asyncio
import io
import logging
import os
import queue
import re
import threading
import time
import wave
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Protocol

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

log = logging.getLogger("winston.tts")

MAX_VOICES = 8                    # prepared clips kept besides the default, least recently used dropped
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_OPENAI_VOICES = {"alloy", "ash", "ballad", "cedar", "coral", "echo", "fable", "marin", "nova", "onyx",
                  "sage", "shimmer", "verse", "default", "winston"}


class SpeechRequest(BaseModel):
    input: str
    model: str | None = None
    voice: str | None = None
    response_format: str = "pcm"      # pcm (s16le mono at X-Sample-Rate) or wav
    speed: float | None = None        # accepted, ignored
    instructions: str | None = None   # accepted, ignored
    stream: bool | None = None        # this request only; unset keeps --stream
    first_secs: float | None = Field(None, gt=0, le=20)       # Chatterbox's chunking; other engines ignore it
    chunk_secs: float | None = Field(None, gt=0, le=20)
    lookahead_secs: float | None = Field(None, gt=0, le=20)


class Engine(Protocol):
    """What an engine provides. prepare and synth run in a worker thread, one at a time."""
    model: str                    # what /health reports, e.g. "ResembleAI/chatterbox-turbo"
    sample_rate: int
    builtin_voices: tuple[str, ...]

    def prepare(self, source: Path | str) -> object:
        """A reference clip (a Path) or a built-in voice's name to whatever synth needs, once per voice."""

    def stream_for(self, req: SpeechRequest, on: bool) -> object:
        """What synth gets as `stream` for this request: `on` itself, or the engine's own chunking with
        the request's values on top. Raises HTTPException to refuse them, before any audio is sent."""

    def synth(self, text: str, voice: object, stream: object) -> Iterable[np.ndarray]:
        """One sentence as float32 mono chunks in [-1, 1], as they are made; a single chunk when not on."""


_transcripts: dict[tuple[str, float], str] = {}
_asr = None
_asr_lock = threading.Lock()


def read_clip(path: Path) -> tuple[np.ndarray, int]:
    """A wav as float32 mono and its rate."""
    import soundfile as sf
    a, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return a.mean(axis=1), sr


def transcript(clip: Path) -> str:
    """The words of a reference clip: <clip>.txt if it exists, else Parakeet's, cached per file version."""
    side = clip.with_suffix(".txt")
    if side.is_file():
        return side.read_text(encoding="utf-8").strip()
    key = (str(clip), clip.stat().st_mtime)
    if key not in _transcripts:
        global _asr
        with _asr_lock:
            if _asr is None:
                import onnx_asr
                _asr = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization="int8")
            a, sr = read_clip(clip)
            t0 = time.perf_counter()
            _transcripts[key] = str(_asr.recognize(a, sample_rate=sr)).strip()
        log.info("transcribed %s in %.0f ms: %r", clip.name, (time.perf_counter() - t0) * 1000, _transcripts[key])
    return _transcripts[key]


def frames_from(run: Callable[[Callable[[object], None]], None]) -> Iterator[object]:
    """Run run(emit) in a thread and yield what it emits as it happens: how a model whose generate()
    returns only at the end is made to hand over each frame (the engines hook emit into its loop)."""
    q: queue.Queue = queue.Queue()
    done = object()

    def work():
        try:
            run(q.put)
        except BaseException as e:    # noqa: BLE001, re-raised in the consumer
            q.put(e)
        finally:
            q.put(done)

    threading.Thread(target=work, daemon=True).start()
    while (item := q.get()) is not done:
        if isinstance(item, BaseException):
            raise item
        yield item


def stream_frames(frames: Iterable, decode: Callable[[list], np.ndarray], *, context: list | None = None,
                  first: int = 6, step: int = 24, left: int = 25, hold: int = 2, fade: int = 480) -> Iterator[np.ndarray]:
    """Codec frames to audio as they arrive, for models that make discrete frames and decode at the end.

    After `first` frames the frames so far are decoded (with up to `left` earlier frames as left
    context, `context` being the reference clip's own frames for models that continue from it) and
    sent, except the last `hold` frames whose right context is still missing; later chunks double
    up to `step` frames. Each seam is cross-faded over `fade` samples into the provisional render
    of what followed, as the Chatterbox server does.
    """
    got = list(context or [])
    base = emitted = len(got)
    due, inc, tail = base + first, first, None
    it = iter(frames)
    while True:
        done = False
        for f in it:
            got.append(f)
            if len(got) >= due:
                break
        else:
            done = True
        n = len(got)
        if n > emitted:
            start = max(0, emitted - left)
            wav = decode(got[start:n])
            hop = len(wav) / (n - start)
            end = n if done else max(emitted, n - hold)
            a = round((emitted - start) * hop)
            b = len(wav) if done else round((end - start) * hop)
            seg = wav[a:b].astype(np.float32, copy=True)
            if tail is not None and len(seg) >= fade and len(tail) >= fade:
                r = np.linspace(0, 1, fade, dtype=np.float32)
                seg[:fade] = tail[:fade] * (1 - r) + seg[:fade] * r
            tail, emitted = wav[b:b + fade], end
            if len(seg):
                yield seg
        if done:
            return
        due, inc = n + inc, min(step, inc * 2)


class SpeechServer:
    def __init__(self, engine: Engine, voice: str, voices_dir: str | None, stream: bool):
        self.engine, self.stream = engine, stream
        path = Path(voice)
        if path.suffix.lower() == ".wav" or path.is_file():
            path = path.resolve()
            self.voices_dir = Path(voices_dir or path.parent).resolve()
            self.default = str(path.relative_to(self.voices_dir).with_suffix("")) \
                if path.is_relative_to(self.voices_dir) else path.stem
            self.default_source: Path | str = path
        else:                                      # a built-in voice, or a clip name under the voices dir
            self.voices_dir = Path(voices_dir or ".").resolve()
            self.default = voice
            self.default_source = voice if voice in engine.builtin_voices else self._clip_path(voice)
            if isinstance(self.default_source, Path) and not self.default_source.is_file():
                raise HTTPException(400, f"not a built-in voice and no file {self.default_source}")
        self.lock = threading.Lock()               # one prepare or synth at a time on the device
        self.cache_lock = threading.Lock()         # self.voices, read and written from request threads
        self.voices: OrderedDict[str, tuple[object, float | None]] = OrderedDict()   # name: (prepared, mtime)
        self.app = self._routes()

    def _clip_path(self, name: str) -> Path:
        """The clip a voice name means, refused (400) unless it stays under the voices directory."""
        parts = Path(name).parts
        if "\\" in name or "\0" in name or Path(name).is_absolute() or ".." in parts or not parts:
            raise HTTPException(400, f"voice {name!r} is not a clip name under the voices directory")
        return (self.voices_dir / name).with_suffix(".wav")

    def prepare_default(self) -> None:
        t0 = time.perf_counter()
        with self.lock:
            self.voices[self.default] = (self.engine.prepare(self.default_source), None)
        log.info("default voice %s prepared in %.0f ms", self.default, (time.perf_counter() - t0) * 1000)

    def _cached(self, name: str, mtime: float | None) -> object | None:
        """The voice's prepared state if it is cached and its clip is unchanged, else None."""
        with self.cache_lock:
            hit = self.voices.get(name)
            if hit is not None and (name == self.default or hit[1] == mtime):
                self.voices.move_to_end(name)
                return hit[0]
        return None

    def resolve(self, name: str | None) -> tuple[str, object]:
        """The voice's name and prepared state, preparing it first if needed. Preparing blocks on the device
        lock, so the async handler runs this in a thread; a cached voice does not wait. The state itself
        goes to the synth, so a voice dropped from the cache meanwhile still speaks."""
        # OpenAI clients validate voice names against OpenAI's list, so any of those means the default
        if not name or name in _OPENAI_VOICES:
            name = self.default
        source: Path | str = name
        mtime = None
        if name != self.default and name not in self.engine.builtin_voices:
            source = self._clip_path(name)
            try:
                mtime = source.stat().st_mtime
            except OSError:
                with self.cache_lock:
                    self.voices.pop(name, None)
                raise HTTPException(400, f"unknown voice {name!r}; no file {source}") from None
        if (prepared := self._cached(name, mtime)) is not None:
            return name, prepared
        with self.lock:
            # two requests for a new voice both wait here; the second finds what the first prepared
            if (prepared := self._cached(name, mtime)) is not None:
                return name, prepared
            t0 = time.perf_counter()
            prepared = self.engine.prepare(source)
            with self.cache_lock:
                self.voices.pop(name, None)
                self.voices[name] = (prepared, mtime)
                for old in [v for v in self.voices if v != self.default][:-MAX_VOICES]:
                    del self.voices[old]
        log.info("prepared voice %s in %.0f ms", name, (time.perf_counter() - t0) * 1000)
        return name, prepared

    def synth_all(self, sentences: list[str], voice: object, stream: object, emit) -> None:
        """Runs in a thread: every sentence through the engine, each chunk to emit as int16 bytes. The lock
        is taken per sentence, so another request (a second box) gets its turn between sentences."""
        for s in sentences:
            with self.lock:
                for a in self.engine.synth(s, voice, stream):
                    emit((np.clip(np.asarray(a, dtype=np.float32).reshape(-1), -1.0, 1.0) * 32767)
                         .astype(np.int16).tobytes())

    def _routes(self) -> FastAPI:
        app = FastAPI(title=f"Winston TTS ({self.engine.model})")

        @app.get("/health")
        async def health():
            gpu = None
            try:
                import torch
                if torch.cuda.is_available() and torch.cuda.is_initialized():
                    gpu = {"allocated_gb": round(torch.cuda.memory_allocated() / 1e9, 2),
                           "peak_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
                           "reserved_gb": round(torch.cuda.memory_reserved() / 1e9, 2)}
            except Exception as e:             # noqa: BLE001, health must answer
                log.debug("no gpu memory for health: %s", e)
            return {"status": "ok", "model": self.engine.model, "sample_rate": self.engine.sample_rate,
                    "default_voice": self.default, "voices_loaded": sorted(self.voices),
                    "builtin_voices": list(self.engine.builtin_voices),
                    "streaming": self.stream, "pid": os.getpid(), "gpu": gpu}

        @app.post("/v1/audio/speech")
        async def speech(req: SpeechRequest):
            if not req.input.strip():
                raise HTTPException(400, "empty input")
            if req.response_format not in ("pcm", "wav"):
                raise HTTPException(400, "response_format must be pcm or wav")
            on = self.stream if req.stream is None else req.stream
            stream = self.engine.stream_for(req, on)
            # preparing a new voice holds the lock for a while; off the event loop, which may be sending audio
            voice, prompt = await asyncio.to_thread(self.resolve, req.voice)
            sentences = [s.strip() for s in _SENTENCE_RE.split(req.input.strip()) if s.strip()] or [req.input.strip()]
            sr = self.engine.sample_rate

            async def pcm_chunks():
                loop, q = asyncio.get_running_loop(), asyncio.Queue()
                t_req, first, total = time.perf_counter(), None, 0

                def work():
                    try:
                        self.synth_all(sentences, prompt, stream, lambda b: loop.call_soon_threadsafe(q.put_nowait, b))
                    except BaseException as e:    # noqa: BLE001, reported to the request
                        loop.call_soon_threadsafe(q.put_nowait, e)
                    finally:
                        loop.call_soon_threadsafe(q.put_nowait, None)

                task = asyncio.create_task(asyncio.to_thread(work))
                while (item := await q.get()) is not None:
                    if isinstance(item, BaseException):
                        raise item
                    if first is None:
                        first = time.perf_counter() - t_req
                    total += len(item) // 2
                    yield item
                await task
                dt = time.perf_counter() - t_req
                log.info("tts %s %s %d chars -> %.2fs audio in %.0f ms (rtf %.2f), ttfb %.0f ms", voice,
                         "streamed" if on else "whole", len(req.input), total / sr, dt * 1000,
                         dt / max(total / sr, 1e-3), (first or 0) * 1000)

            headers = {"X-Sample-Rate": str(sr), "X-Streaming": "on" if on else "off"}
            if req.response_format == "wav":
                buf = io.BytesIO()
                with wave.open(buf, "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
                    async for chunk in pcm_chunks():
                        w.writeframes(chunk)
                return Response(buf.getvalue(), media_type="audio/wav", headers=headers)
            return StreamingResponse(pcm_chunks(), media_type="audio/pcm", headers=headers)

        return app


def arg_parser(description: str, voice: str | None = None) -> argparse.ArgumentParser:
    """The command line every engine shares; each adds its own options. voice is the default --voice."""
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--voice", required=voice is None, default=voice,
                    help="reference clip (wav) for the default voice, or a built-in voice's name")
    ap.add_argument("--voices-dir", default=None, help="dir that request 'voice' names resolve under")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8880)
    ap.add_argument("--stream", type=lambda v: v.lower() in ("1", "true", "yes", "on"), default=True,
                    help="true: send audio while a sentence is still being generated; false: whole sentences")
    return ap


def serve(engine: Engine, args: argparse.Namespace) -> None:
    """Prepare the default voice, warm up, and serve until stopped."""
    try:
        server = SpeechServer(engine, args.voice, args.voices_dir, args.stream)
        server.prepare_default()
    except HTTPException as e:
        raise SystemExit(f"default voice {args.voice!r}: {e.detail}") from e
    voice = server.voices[server.default][0]
    # the streaming path too, even with --stream false: a request may ask for it (the Voice page's sample)
    for text, on in (("Hello.", False), ("Hello, I'm Winston. How can I help?", False),
                     ("Hello, I'm Winston. How can I help?", True)):
        t0, got = time.perf_counter(), []
        server.synth_all([text], voice, engine.stream_for(SpeechRequest(input=text), on), got.append)
        log.info("warmup %s %r: %.2fs audio in %.0f ms", "streamed" if on else "whole", text,
                 sum(map(len, got)) / 2 / engine.sample_rate, (time.perf_counter() - t0) * 1000)
    try:
        import torch
        if torch.cuda.is_available() and torch.cuda.is_initialized():
            log.info("gpu memory: %.2f GB allocated, %.2f GB peak, %.2f GB reserved",
                     torch.cuda.memory_allocated() / 1e9, torch.cuda.max_memory_allocated() / 1e9,
                     torch.cuda.memory_reserved() / 1e9)
    except Exception as e:                     # noqa: BLE001
        log.debug("no gpu memory to log: %s", e)
    uvicorn.run(server.app, host=args.host, port=args.port, log_level="warning")


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
