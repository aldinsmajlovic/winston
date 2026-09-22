"""M2 component check, no browser needed: STT on a known clip, TTS of a few sentences
(timed), then STT of the TTS output to confirm it is intelligible. Requires the TTS
server on :8880. Run from the repo root:  .venv/bin/python tests/m2_components.py
"""
import io
import sys
import time
import wave
from pathlib import Path

import httpx
import numpy as np
import onnx_asr

from winston.config import load  # noqa: E402
ROOT = Path(__file__).resolve().parents[1]
TTS = load().tts.base_url.removesuffix("/v1")
OUT = ROOT / "tests/latency/results"; OUT.mkdir(parents=True, exist_ok=True)


def load_wav(path_or_bytes):
    with wave.open(io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else str(path_or_bytes)) as w:
        sr = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    return pcm, sr


t0 = time.perf_counter()
stt = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization="int8")
print(f"STT model load {time.perf_counter() - t0:.1f}s")

ref = ROOT / "assets/voices/vctk/p226_023.wav"
pcm, sr = load_wav(ref)
t0 = time.perf_counter(); text = stt.recognize(pcm, sample_rate=sr); dt = time.perf_counter() - t0
print(f"STT numpy path {len(pcm) / sr:.1f}s@{sr} -> {dt * 1000:.0f} ms: {text!r}")

sentences = ["Good evening. The living room lights are at fifty percent and warm.",
             "Sweden has more than two hundred thousand islands, though most are uninhabited.",
             "Right. Anything else?"]
ok = True
for s in sentences:
    # streaming pcm: time to first byte and total
    t0 = time.perf_counter(); first = None; n = 0
    with httpx.stream("POST", f"{TTS}/v1/audio/speech", json={"input": s, "response_format": "pcm"}, timeout=60) as r:
        r.raise_for_status()
        for chunk in r.iter_bytes():
            if first is None and chunk: first = time.perf_counter() - t0
            n += len(chunk)
    total = time.perf_counter() - t0
    secs = n / 2 / 24000
    # wav for listening + STT check
    wav = httpx.post(f"{TTS}/v1/audio/speech", json={"input": s, "response_format": "wav"}, timeout=60).content
    (OUT / f"tts_{sentences.index(s)}.wav").write_bytes(wav)
    a, asr = load_wav(wav)
    back = stt.recognize(a, sample_rate=asr)
    print(f"TTS {len(s):3d} chars -> {secs:.2f}s audio | ttfb {first * 1000:.0f} ms, total {total * 1000:.0f} ms, "
          f"rtf {total / secs:.2f}\n     said: {s}\n     heard: {back}")
    ok &= len(back.split()) >= 0.6 * len(s.split())
print("PASS" if ok else "FAIL: TTS output not intelligible to STT"); sys.exit(0 if ok else 1)
