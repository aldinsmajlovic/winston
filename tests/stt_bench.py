"""Hearing benchmark: Fluid Speech Pro through Winston's own adapter against any OpenAI transcription server.

150 FLEURS en_us test sentences, each three ways: clean, with four other FLEURS sentences 10 dB under the
speaker, and under a 50 to 200 Hz hum with brown noise 5 dB under. Every engine hears the same mixed files.
Work files go to ~/.cache/winston/stt-bench (or $WINSTON_STT_BENCH), never the repo.

    uv run python tests/stt_bench.py fetch        # FLEURS en_us test, about 290 MB
    uv run python tests/stt_bench.py mix
    uv run python tests/stt_bench.py fluid        # Fluid Speech Pro, int8, on the CPU
    uv run python tests/stt_bench.py server NAME  # the server on 127.0.0.1:8001 (services/stt-qwen3-asr)
    uv run python tests/stt_bench.py score

Results: docs/voice-benchmarks.md (Hearing engines).
"""
import csv
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soundfile as sf

B = Path(os.environ.get("WINSTON_STT_BENCH") or Path.home() / ".cache/winston/stt-bench")
F = B / "fleurs"
RATE = 16000
CONDS = ("clean", "talkers", "hum")
FLEURS = "https://huggingface.co/datasets/google/fleurs/resolve/main/data/en_us"


def fetch() -> None:
    F.mkdir(parents=True, exist_ok=True)
    for url, out in ((f"{FLEURS}/test.tsv", "test.tsv"), (f"{FLEURS}/audio/test.tar.gz", "test.tar.gz")):
        subprocess.run(["curl", "-4", "-fsSL", "-o", str(F / out), url], check=True)   # IPv4: a VPN can drop IPv6
    subprocess.run(["tar", "xzf", "test.tar.gz"], cwd=F, check=True)


def rows() -> tuple[list, list]:
    out, seen = [], set()
    with open(F / "test.tsv", newline="") as f:
        for r in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            if r[1] not in seen and (F / "test" / r[1]).exists():
                seen.add(r[1])
                out.append((r[1], r[2]))
    pick = np.random.default_rng(24).choice(len(out), 150, replace=False)
    return [out[i] for i in sorted(pick)], out


def load(name: str) -> np.ndarray:
    a, r = sf.read(F / "test" / name, dtype="float32", always_2d=True)
    assert r == RATE, r
    return a.mean(axis=1)


def rms(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(a ** 2)) + 1e-9)


def mix() -> None:
    pick, everyone = rows()
    rng = np.random.default_rng(7)
    for c in CONDS:
        (B / c).mkdir(parents=True, exist_ok=True)
    for name, _ in pick:
        a = load(name)
        n = len(a)
        talk = np.zeros(n, np.float32)
        for other, _ in [everyone[i] for i in rng.choice(len(everyone), 4, replace=False)]:
            o = load(other)
            o = np.resize(o, n) if len(o) >= n else np.pad(o, (0, n - len(o)))
            talk += o / rms(o)
        talk *= rms(a) / rms(talk) * 10 ** (-10 / 20)
        t = np.arange(n) / RATE
        hum = sum(np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28)) for f in (50, 100, 150, 200))
        brown = np.cumsum(rng.standard_normal(n))
        brown -= np.convolve(brown, np.ones(4000) / 4000, "same")
        noise = hum / rms(hum) + brown / rms(brown)
        noise *= rms(a) / rms(noise) * 10 ** (-5 / 20)
        for c, x in (("clean", a), ("talkers", a + talk), ("hum", a + noise)):
            x = x / max(1.0, float(np.abs(x).max()) / 0.99)
            sf.write(B / c / name, (x * 32767).astype(np.int16), RATE, subtype="PCM_16")
    (B / "refs.json").write_text(json.dumps(pick))
    print("mixed", len(pick))


def run(engine: str) -> None:
    pick = json.loads((B / "refs.json").read_text())
    if engine == "fluid":
        from winston.plugins.stt.parakeet import Parakeet
        m = Parakeet.load_model(SimpleNamespace(model="FluidVoice-Labs/fluid-speech-pro-onnx", quantization="int8"))

        def hear(p: Path) -> str:
            return Parakeet.transcribe(m, sf.read(p, dtype="int16")[0], RATE)
    else:
        import httpx
        client = httpx.Client(timeout=60)

        def hear(p: Path) -> str:
            return client.post("http://127.0.0.1:8001/v1/audio/transcriptions", data={"model": "x"},
                               files={"file": (p.name, p.read_bytes(), "audio/wav")}).json()["text"]
    out = {}
    for c in CONDS:
        times = []
        for name, _ in pick:
            t = time.perf_counter()
            out[f"{c}/{name}"] = hear(B / c / name)
            times.append(time.perf_counter() - t)
        print(engine, c, f"median {np.median(times):.2f} s")
    (B / f"{engine}.json").write_text(json.dumps(out))


def words(s: str) -> list[str]:
    return re.sub(r"[^\w' ]+", " ", s.lower().replace("-", " ")).split()


def edits(ref: list[str], hyp: list[str]) -> int:
    d = list(range(len(hyp) + 1))
    for i, rw in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[-1]


def score() -> None:
    pick = json.loads((B / "refs.json").read_text())
    total = sum(len(words(ref)) for _, ref in pick)
    for p in sorted(B.glob("*.json")):
        if p.name == "refs.json":
            continue
        hyp = json.loads(p.read_text())
        cells = [f"{c} {100 * sum(edits(words(r), words(hyp[f'{c}/{n}'])) for n, r in pick) / total:.1f}%" for c in CONDS]
        print(p.stem, ", ".join(cells))


assert edits(words("Hello, there big-world"), words("hello their big world")) == 1   # the scorer itself

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "score"
    if cmd == "server":
        run(sys.argv[2] if len(sys.argv) > 2 else "server")
    else:
        {"fetch": fetch, "mix": mix, "fluid": lambda: run("fluid"), "score": score}[cmd]()
