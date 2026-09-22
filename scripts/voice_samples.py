"""Synthesize the same lines in every reference voice so a human can pick one.
Needs the TTS server on :8880.   .venv/bin/python scripts/voice_samples.py [out_dir]
"""
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
TTS = "http://127.0.0.1:8880"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "samples/voices"
LINES = ("Good evening. I'm Winston. The living room lights are at fifty percent and warm, "
         "and your PC is booting now. Anything else? [chuckle] I thought not.")

OUT.mkdir(parents=True, exist_ok=True)
for clip in sorted((ROOT / "assets/voices/vctk").glob("*.wav")):
    voice = f"vctk/{clip.stem}"
    t0 = time.perf_counter()
    wav = httpx.post(f"{TTS}/v1/audio/speech", json={"input": LINES, "voice": voice, "response_format": "wav"},
                     timeout=300).content
    out = OUT / f"{clip.stem}.wav"; out.write_bytes(wav)
    print(f"{voice}: {len(wav) / 48000:.1f}s audio in {time.perf_counter() - t0:.1f}s -> {out}")
