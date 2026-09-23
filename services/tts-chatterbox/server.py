"""Winston TTS adapter: OpenAI-compatible /v1/audio/speech on top of Chatterbox-Turbo.

Runs in its own venv (voice/tts/chatterbox_server/.venv) so its torch pins never
touch the pipeline venv. Pipecat's OpenAITTSService talks to it with
response_format=pcm and expects 24 kHz mono int16, which is Chatterbox's native rate.

    .venv/bin/python server.py --voice ../reference/vctk/p232_023.wav --port 8880

Voice conditionals are prepared once per reference clip and cached (the default plus the eight
most recently used), so a request may name any clip under --voices-dir (e.g. "vctk/p226_023") to
switch voices. A clip written again (a friend's voice cloned anew) is prepared again.

Streaming: Chatterbox renders a sentence whole, and its speech-token stage takes about 25 ms per
character, so a long sentence kept the first audio waiting 1.5 to 2 s. With --stream true (default)
the speech tokens are generated one by one and the prefix so far is vocoded after --first-secs of
audio, then in chunks that double up to --chunk-secs, so the next chunk is always ready before
playback catches up. Only the new samples go out, minus the last --lookahead-secs whose right
context is still missing, cross-faded into the next chunk. Winston passes these from tts.streaming.
The vocoder is non-causal, so each prefix is a fresh render of the whole; the words come out the
same (Parakeet transcribes both identically).
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
_mtimes: dict[str, float] = {}    # voice name -> its clip's mtime when prepared; a re-recorded clone is prepared again
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_stream = {"on": True, "first": 24, "step": 40, "hold": 8}
SAMPLES_PER_TOKEN = 960           # speech tokens run at 25 Hz, audio at 24 kHz
FADE = 480                        # 20 ms cross-fade at each seam
MAX_VOICES = 8                    # prepared clips kept besides the default, least recently used dropped


class SpeechRequest(BaseModel):
    input: str
    model: str | None = None
    voice: str | None = None
    response_format: str = "pcm"      # pcm (24 kHz s16le mono) or wav
    speed: float | None = None        # accepted, ignored
    instructions: str | None = None   # accepted, ignored


_OPENAI_VOICES = {"alloy", "ash", "ballad", "cedar", "coral", "echo", "fable", "marin", "nova", "onyx",
                  "sage", "shimmer", "verse", "default", "winston"}


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _resolve_voice(name: str | None) -> str:
    # OpenAI clients validate voice names against OpenAI's list, so any of those means "the default clip"
    if not name or name in _OPENAI_VOICES:
        return _default_voice
    path = (_voices_dir / name).with_suffix(".wav")
    if name in _conds and (name == _default_voice or _mtimes.get(name) == _mtime(path)):
        _conds[name] = _conds.pop(name)      # most recently used goes last
        return name
    if not path.is_file():
        _conds.pop(name, None)
        raise HTTPException(400, f"unknown voice {name!r}; no file {path}")
    t0 = time.perf_counter()
    # under the GPU lock, so a sentence being rendered keeps its voice, and without autograd, whose
    # saved tensors otherwise stay on the GPU with every cached voice (11 clips ran a 3090 out of memory)
    with _lock, torch.inference_mode():
        before = _model.conds
        _model.prepare_conditionals(str(path))
        _conds.pop(name, None)
        _conds[name], _model.conds = _model.conds, before
        _mtimes[name] = _mtime(path)
        for old in [v for v in _conds if v != _default_voice][:-MAX_VOICES]:
            del _conds[old]
        torch.cuda.empty_cache()
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


def _pcm(wav) -> np.ndarray:
    wav = _model.watermarker.apply_watermark(np.asarray(wav, dtype=np.float32), sample_rate=_model.sr)
    return (np.clip(np.asarray(wav, dtype=np.float32), -1.0, 1.0) * 32767).astype(np.int16)


def _speech_tokens(text: str):
    """Chatterbox-Turbo's T3 loop (t3.inference_turbo) as a generator, same sampling as generate()."""
    from chatterbox.tts_turbo import punc_norm
    from transformers.generation.logits_process import (
        LogitsProcessorList,
        RepetitionPenaltyLogitsProcessor,
        TemperatureLogitsWarper,
        TopKLogitsWarper,
        TopPLogitsWarper,
    )
    t3 = _model.t3
    procs = LogitsProcessorList([TemperatureLogitsWarper(0.8), TopKLogitsWarper(1000), TopPLogitsWarper(0.95),
                                 RepetitionPenaltyLogitsProcessor(1.2)])
    tok = _model.tokenizer(punc_norm(text), return_tensors="pt", padding=True, truncation=True).input_ids.to(_model.device)
    start = t3.hp.start_speech_token * torch.ones_like(tok[:, :1])
    embeds, _ = t3.prepare_input_embeds(t3_cond=_model.conds.t3, text_tokens=tok, speech_tokens=start, cfg_weight=0.0)
    out = t3.tfmr(inputs_embeds=embeds, use_cache=True)
    past, logits, seen = out.past_key_values, t3.speech_head(out[0][:, -1:]), start
    for _ in range(1000):
        nxt = torch.multinomial(torch.softmax(procs(seen, logits[:, -1, :]), dim=-1), num_samples=1)
        if int(nxt) == t3.hp.stop_speech_token:
            return
        seen = nxt if seen is start else torch.cat([seen, nxt], dim=1)
        yield int(nxt)
        out = t3.tfmr(inputs_embeds=t3.speech_emb(nxt), past_key_values=past, use_cache=True)
        past, logits = out.past_key_values, t3.speech_head(out[0])


def _vocode(tokens: list[int], noise: torch.Tensor, final: bool) -> np.ndarray:
    """The whole prefix to float audio. One noise tensor per utterance keeps the renders close."""
    from chatterbox.models.s3gen.s3gen import S3Token2Mel
    from chatterbox.tts_turbo import S3GEN_SIL
    g = _model.s3gen
    st = torch.tensor([t for t in tokens if t < 6561] + ([S3GEN_SIL] * 3 if final else []), device=_model.device)
    torch.manual_seed(7)                     # the prompt part of the flow noise, and the vocoder's source
    mels = S3Token2Mel.forward(g, st[None], ref_wav=None, ref_sr=None, ref_dict=_model.conds.gen, n_cfm_timesteps=2,
                               finalize=True, noised_mels=noise[:, :, :len(st) * 2])   # finalize=False is broken upstream
    wav, _ = g.hift_inference(mels.to(g.dtype), None)
    wav[:, :len(g.trim_fade)] *= g.trim_fade
    return wav.squeeze(0).float().cpu().numpy()


def _synth_stream(text: str, voice: str, emit):
    """Stream one sentence: emit(int16 chunk) as audio becomes safe to send."""
    with _lock, torch.inference_mode():
        _model.conds = _conds[voice]
        g = _model.s3gen
        noise = torch.randn(1, 80, 2 * 1010, dtype=g.dtype, device=_model.device)
        tokens, emitted, tail, due = [], 0, None, _stream["first"]
        inc = min(_stream["step"], _stream["first"])   # chunks double up to --chunk-secs, so the next one
                                                        # is always ready before the audio sent so far runs out
        gen = _speech_tokens(text)
        while True:
            done = False
            for t in gen:
                tokens.append(t)
                if len(tokens) >= due:
                    break
            else:
                done = True
            if not tokens:
                return
            wav = _vocode(tokens, noise, done)
            end = len(wav) if done else max(emitted, len(wav) - _stream["hold"] * SAMPLES_PER_TOKEN)
            seg = wav[emitted:end].copy()
            if tail is not None and len(seg) >= FADE:
                r = np.linspace(0, 1, FADE, dtype=np.float32)
                seg[:FADE] = tail[:FADE] * (1 - r) + seg[:FADE] * r
            tail, emitted, due = wav[end:end + FADE], end, due + inc
            inc = min(_stream["step"], inc * 2)
            if len(seg):
                emit(_pcm(seg))
            if done:
                return


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
        if _stream["on"]:
            loop, q = asyncio.get_running_loop(), asyncio.Queue()

            def work():
                try:
                    for s in sentences:
                        _synth_stream(s, voice, lambda a: loop.call_soon_threadsafe(q.put_nowait, a))
                finally:
                    loop.call_soon_threadsafe(q.put_nowait, None)
            task = asyncio.create_task(asyncio.to_thread(work))
            first, total = None, 0
            while (audio := await q.get()) is not None:
                if first is None:
                    first = time.perf_counter() - t_req
                total += len(audio)
                yield audio.tobytes()
            await task
            secs = total / _model.sr
            log.info("tts %s streamed %d chars -> %.2fs audio in %.0f ms, ttfb %.0f ms", voice, len(req.input), secs,
                     (time.perf_counter() - t_req) * 1000, (first or 0) * 1000)
            return
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
    ap.add_argument("--stream", type=lambda v: v.lower() in ("1", "true", "yes", "on"), default=True,
                    help="true: send audio while a sentence is still being generated; false: whole sentences")
    ap.add_argument("--first-secs", type=float, default=0.96, help="audio rendered before the first chunk")
    ap.add_argument("--chunk-secs", type=float, default=1.6, help="audio per later chunk; longer is smoother")
    ap.add_argument("--lookahead-secs", type=float, default=0.32,
                    help="audio held back at a chunk's end until what follows is known")
    args = ap.parse_args()
    tokens = lambda secs: max(1, round(secs * 25))   # speech tokens run at 25 Hz
    _stream.update(on=args.stream, first=tokens(args.first_secs), step=tokens(args.chunk_secs),
                   hold=tokens(args.lookahead_secs))
    if _stream["hold"] >= _stream["first"]:
        ap.error("--lookahead-secs must be shorter than --first-secs")
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
    if _stream["on"]:
        t0 = time.perf_counter(); _synth_stream("Hello, I'm Winston. How can I help?", _default_voice, lambda a: None)
        log.info("warmup streamed in %.0f ms", (time.perf_counter() - t0) * 1000)
    if args.device == "cuda":
        log.info("gpu memory: %.2f GB allocated, %.2f GB reserved",
                 torch.cuda.memory_allocated() / 1e9, torch.cuda.memory_reserved() / 1e9)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
