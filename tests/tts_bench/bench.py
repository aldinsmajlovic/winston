"""Voice benchmark: speak Winston's own lines through OpenAI speech servers and measure them.

    uv run python tests/tts_bench/bench.py turbo=http://127.0.0.1:8880 --stream false
    uv run python tests/tts_bench/bench.py qwen3=http://127.0.0.1:8891 voxcpm2=http://127.0.0.1:8892
    uv run python tests/tts_bench/bench.py pocket=http://127.0.0.1:8895#george     # a built-in voice

Each NAME=URL is a server speaking the protocol of services/speech_server.py. For every
reference clip (copied read-only from data/voices into work/voices, with Parakeet's transcript
beside it for engines that clone from the words) and every line of sentences.txt it measures:

- ttfa_ms: request sent to the first audio byte back, total_ms: to the last, rtf: total / audio length
- sound_ms: when a listener hears the first sound (leading silence played out, late chunks waited for)
- wer: Parakeet (onnx-asr on the CPU, as Winston hears) on the audio against the sentence, tags removed
- first_use_ms: the first request in a clip, a short "Hello.", which includes cloning the clip
A server that cannot clone (Pocket without Kyutai's gated cloning weights) takes #VOICE after the
URL: its built-in voice then speaks every sentence once, and listen.py pairs it with the baseline in
the first clip, so that pair compares the voice as well as the engine.

- peak VRAM: nvidia-smi sampled every 0.2 s, for the server's own process (by /health pid or the
  port's listener) and the whole card

Results merge into work/results.json by name, so servers can run one at a time (only one GPU engine
fits beside the brain); audio goes to work/audio/NAME/. listen.py turns them into the blind listening page.
Servers are only spoken to; nothing is started, stopped or written outside tests/tts_bench/work.
"""
import argparse
import json
import re
import shutil
import statistics
import subprocess
import threading
import time
import unicodedata
import wave
from pathlib import Path
from urllib.parse import urlparse

import httpx
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = HERE / "work"
VOICES = WORK / "voices"
RESULTS = WORK / "results.json"
# the voice Winston speaks in now, the default clip, and the other clip he used most (tts.log)
CLIPS = ["librivox/clifton_fatherbrown", "vctk/p254_023", "vctk/p226_023"]


# ---------------------------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------------------------

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
         "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return _ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + number_words(n % 100))
    for size, word in ((10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")):
        if n >= size:
            return number_words(n // size) + " " + word + ("" if n % size == 0 else " " + number_words(n % size))
    return str(n)


def words(text: str) -> list[str]:
    """Text to comparable words: tags out, accents off, digits spelled, hyphens split, punctuation gone."""
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("%", " percent ").replace("-", " ").replace("'", "")
    text = re.sub(r"(\d),(\d{3})", r"\1\2", text)
    text = re.sub(r"\d+", lambda m: " " + number_words(int(m.group())) + " ", text)
    return re.sub(r"[^a-z ]", " ", text).split()


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
    return d[len(h)] / max(len(r), 1)


def read_sentences(path: Path) -> list[str]:
    return [s.strip() for s in path.read_text(encoding="utf-8").splitlines() if s.strip() and not s.startswith("#")]


# ---------------------------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------------------------

def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        a = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    return a.reshape(-1, ch).mean(axis=1), sr


def write_wav(path: Path, pcm: bytes, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm)


class Ears:
    """Parakeet, loaded once."""

    def __init__(self):
        import onnx_asr
        t0 = time.perf_counter()
        self.model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization="int8")
        print(f"Parakeet loaded in {time.perf_counter() - t0:.1f} s")

    def hear(self, a: np.ndarray, sr: int) -> str:
        return str(self.model.recognize(a.astype(np.float32), sample_rate=sr)).strip() if len(a) else ""


def prepare_clips(clips: list[str], source: Path, ears: Ears) -> None:
    """Copy each clip out of data/voices (read only) and write its transcript beside the copy."""
    for name in clips:
        src, dst = (source / name).with_suffix(".wav"), (VOICES / name).with_suffix(".wav")
        if not src.is_file():
            raise SystemExit(f"no clip {src}")
        if not dst.is_file() or dst.stat().st_size != src.stat().st_size:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        txt = dst.with_suffix(".txt")
        if not txt.is_file():
            txt.write_text(ears.hear(*read_wav(dst)) + "\n", encoding="utf-8")
        a, sr = read_wav(dst)
        print(f"clip {name}: {len(a) / sr:.1f} s, words: {txt.read_text(encoding='utf-8').strip()[:90]}")


# ---------------------------------------------------------------------------------------------
# VRAM
# ---------------------------------------------------------------------------------------------

def _smi(query: str, kind: str) -> list[list[str]]:
    try:
        out = subprocess.run(["nvidia-smi", f"--query-{kind}={query}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [[c.strip() for c in line.split(",")] for line in out.splitlines() if line.strip()]


class GpuSampler(threading.Thread):
    """Peak memory of one process and of the whole card while the benchmark runs."""

    def __init__(self, pid: int | None, every: float = 0.2):
        super().__init__(daemon=True)
        self.pid, self.every, self.stop_ = pid, every, threading.Event()
        self.proc_mb = self.card_mb = 0
        self.proc_start_mb, self.card_start_mb = self.sample()

    def sample(self) -> tuple[int, int]:
        proc = sum(int(r[1]) for r in _smi("pid,used_memory", "compute-apps") if len(r) == 2 and r[0] == str(self.pid)
                   and r[1].isdigit())
        card = sum(int(r[0]) for r in _smi("memory.used", "gpu") if r and r[0].isdigit())
        return proc, card

    def run(self):
        while not self.stop_.is_set():
            p, c = self.sample()
            self.proc_mb, self.card_mb = max(self.proc_mb, p), max(self.card_mb, c)
            self.stop_.wait(self.every)

    def stop(self) -> dict:
        self.stop_.set(); self.join()
        return {"server_start_mb": self.proc_start_mb, "server_peak_mb": self.proc_mb,
                "card_start_mb": self.card_start_mb, "card_peak_mb": self.card_mb,
                "card_total_mb": sum(int(r[0]) for r in _smi("memory.total", "gpu") if r and r[0].isdigit())}


def server_pid(url: str, health: dict) -> int | None:
    if isinstance(health.get("pid"), int):
        return health["pid"]
    port = urlparse(url).port
    try:
        out = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"], capture_output=True, text=True, timeout=5,
                             check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"pid=(\d+)", out)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------------------------
# Speaking
# ---------------------------------------------------------------------------------------------

def speak(client: httpx.Client, url: str, text: str, voice: str, stream: bool | None, rate: int) -> dict:
    body = {"input": text, "voice": voice, "response_format": "pcm"}
    if stream is not None:
        body["stream"] = stream
    t0 = time.perf_counter()
    first, chunks, arrivals, got = None, [], [], 0
    with client.stream("POST", f"{url}/v1/audio/speech", json=body) as r:
        if r.status_code != 200:
            r.read()
            raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
        sr = int(r.headers.get("x-sample-rate") or rate)
        streaming = r.headers.get("x-streaming", "")
        for c in r.iter_bytes():
            if c and first is None:
                first = time.perf_counter() - t0
            chunks.append(c)
            got += len(c)
            arrivals.append((got // 2, time.perf_counter() - t0))
    total = time.perf_counter() - t0
    pcm = b"".join(chunks)
    pcm = pcm[:len(pcm) // 2 * 2]
    secs = len(pcm) / 2 / sr
    return {"pcm": pcm, "sr": sr, "streaming": streaming, "ttfa_ms": round((first or total) * 1000),
            "sound_ms": first_sound_ms(pcm, sr, arrivals, first or total), "total_ms": round(total * 1000),
            "audio_s": round(secs, 3), "rtf": round(total / secs, 3) if secs else None}


def first_sound_ms(pcm: bytes, sr: int, arrivals: list[tuple[int, float]], first: float) -> int | None:
    """When a listener would hear the first sound, not just get the first byte: playback starts with the
    first byte, leading silence plays out, and a sound that arrives later than its turn waits for it."""
    a = np.abs(np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0)
    win = max(1, sr // 100)                                        # 10 ms windows over 0.02 (about -34 dBFS)
    loud = np.flatnonzero(np.convolve(a, np.ones(win) / win, mode="same") > 0.02)
    if not len(loud):
        return None
    i = int(loud[0])
    came = next(t for end, t in arrivals if end > i)
    return round(max(came, first + i / sr) * 1000)


def clip_file(name: str) -> str:
    return name.replace("/", "__")


def run_server(name: str, url: str, sentences: list[str], clips: list[str], stream: bool | None, ears: Ears,
               timeout: float) -> dict:
    url, _, builtin = url.partition("#")
    url = url.rstrip("/").removesuffix("/v1")
    if builtin:
        clips = [builtin]
    with httpx.Client(timeout=timeout) as client:
        health = client.get(f"{url}/health").json()
        rate = int(health.get("sample_rate") or 24000)
        pid = server_pid(url, health)
        print(f"\n{name} at {url}: {health.get('model', '')} {rate} Hz, pid {pid}, default {health.get('default_voice')}")
        gpu = GpuSampler(pid)
        gpu.start()
        rows, first_use = [], {}
        try:
            for clip in clips:
                r = speak(client, url, "Hello.", clip, stream, rate)
                first_use[clip] = r["total_ms"]
                print(f"  {clip}: first use {r['total_ms']} ms")
                for i, text in enumerate(sentences, 1):
                    sid = f"s{i:02d}"
                    r = speak(client, url, text, clip, stream, rate)
                    path = WORK / "audio" / name / f"{clip_file(clip)}__{sid}.wav"
                    write_wav(path, r.pop("pcm"), r["sr"])
                    heard = ears.hear(*read_wav(path))
                    row = {"clip": clip, "sid": sid, "text": text, "heard": heard, "wer": round(wer(text, heard), 3),
                           "file": str(path.relative_to(WORK)), **r}
                    rows.append(row)
                    print(f"  {sid} ttfa {r['ttfa_ms']:5d} ms  sound {r['sound_ms'] or 0:5d} ms  total {r['total_ms']:6d} ms  {r['audio_s']:5.2f} s  "
                          f"rtf {r['rtf'] or 0:.2f}  wer {row['wer']:.2f}  heard: {heard[:70]}")
        finally:
            vram = gpu.stop()
        after = client.get(f"{url}/health").json()
    return {"url": url, "model": health.get("model"), "sample_rate": rate, "pid": pid, "stream_sent": stream,
            "builtin_voice": builtin or None,
            "health": after, "vram": vram, "first_use_ms": first_use, "rows": rows,
            "summary": summarize(rows, vram), "at": time.strftime("%Y-%m-%d %H:%M:%S")}


def summarize(rows: list[dict], vram: dict) -> dict:
    if not rows:
        return {}
    ttfa = sorted(r["ttfa_ms"] for r in rows)
    sound = [r["sound_ms"] for r in rows if r.get("sound_ms") is not None]
    return {"n": len(rows), "ttfa_median_ms": round(statistics.median(ttfa)),
            "sound_median_ms": round(statistics.median(sound)) if sound else None,
            "ttfa_p90_ms": ttfa[min(len(ttfa) - 1, int(0.9 * len(ttfa)))],
            "rtf_median": round(statistics.median(r["rtf"] for r in rows if r["rtf"]), 3),
            "wer_mean": round(statistics.mean(r["wer"] for r in rows), 3),
            "wer_over_20pct": sum(r["wer"] > 0.2 for r in rows),
            "server_peak_mb": vram["server_peak_mb"], "card_peak_mb": vram["card_peak_mb"]}


def print_table(results: dict) -> None:
    print(f"\n{'server':16} {'n':>3} {'ttfa med':>9} {'ttfa p90':>9} {'sound med':>10} {'rtf':>6} {'wer':>6} {'>20%':>5} "
          f"{'vram MB':>8} {'card MB':>8}")
    for name, r in results.items():
        s = r.get("summary") or {}
        if s:
            print(f"{name:16} {s['n']:3d} {s['ttfa_median_ms']:9d} {s['ttfa_p90_ms']:9d} {s.get('sound_median_ms') or 0:10d} "
                  f"{s['rtf_median']:6.2f} "
                  f"{s['wer_mean']:6.3f} {s['wer_over_20pct']:5d} {s['server_peak_mb']:8d} {s['card_peak_mb']:8d}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("servers", nargs="*", metavar="NAME=URL", help="e.g. turbo=http://127.0.0.1:8880")
    ap.add_argument("--stream", choices=["true", "false", "server"], default="true",
                    help="ask each server to stream (true), to render whole sentences (false), or send nothing")
    ap.add_argument("--sentences", type=int, default=0, help="only the first N sentences (a dry run)")
    ap.add_argument("--clips", default=",".join(CLIPS), help="comma-separated clip names under data/voices")
    ap.add_argument("--source", default=str(ROOT / "data/voices"), help="where the clips are copied from (read only)")
    ap.add_argument("--timeout", type=float, default=300, help="seconds per request (a CPU run is slow)")
    ap.add_argument("--table", action="store_true", help="print the table of what work/results.json holds and stop")
    ap.add_argument("--prepare", action="store_true", help="only copy the clips and write their transcripts")
    args = ap.parse_args()

    results = json.loads(RESULTS.read_text()) if RESULTS.is_file() else {}
    if args.table:
        print_table(results)
        return
    if not args.servers and not args.prepare:
        ap.error("name at least one NAME=URL")
    sentences = read_sentences(HERE / "sentences.txt")
    if args.sentences:
        sentences = sentences[:args.sentences]
    clips = [c.strip() for c in args.clips.split(",") if c.strip()]
    stream = {"true": True, "false": False, "server": None}[args.stream]

    ears = Ears()
    prepare_clips(clips, Path(args.source), ears)
    if args.prepare:
        return
    for spec in args.servers:
        name, _, url = spec.partition("=")
        if not url:
            ap.error(f"{spec!r} is not NAME=URL")
        try:
            results[name] = run_server(name, url, sentences, clips, stream, ears, args.timeout)
        except (httpx.HTTPError, RuntimeError) as e:
            print(f"{name}: failed: {e}")
            continue
        WORK.mkdir(parents=True, exist_ok=True)
        RESULTS.write_text(json.dumps(results, indent=1))
    print_table(results)
    print(f"\nresults in {RESULTS}")


if __name__ == "__main__":
    main()
