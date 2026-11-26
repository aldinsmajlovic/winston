"""Memory each pick takes, and the picks recommended for this computer: the graphics card's (NVIDIA or AMD), or
without one the computer's own, since the brain then runs on the processor.

Measured on 2026-11-25, each part working (the brain answering, the voice speaking, hearing transcribing): on
the RTX 3090 with nvidia-smi, without the desktop's share, and on the processor as each process's resident
memory: docs/hardware.md (Sizing by the card). A brain not measured is estimated from the size of its weights.
The memory bar and setup's recommendation both read these numbers, so they agree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from winston.config.schema import WinstonConfig

# A brain's MiB at each context (K tokens per conversation); between and past them, a straight line.
BRAINS: dict[str, tuple[tuple[int, int], ...]] = {
    "granite4.2:8b": ((16, 6796), (32, 8188), (64, 11094)),
    "qwen3.5:4b": ((16, 4236), (32, 4604), (64, 5588)),
    "huihui-ai/Huihui-Qwen3.8-27B-abliterated-GGUF:UD-DW-Q4_K_S": ((16, 17270), (32, 18598), (64, 21320)),
    "unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_S": ((16, 17014), (32, 18342), (64, 21064)),   # 0.25 GB under huihui's
}
# A voice's or hearing's MiB by preset id; one on the processor takes none. Gepard, Qwen3-TTS and VoxCPM2 are
# from the voice benchmarks (docs/voices.md), the rest measured as above.
VOICES = {"breeze2": 5382, "chatterbox": 3364, "gepard": 2253, "qwen3": 6656, "voxcpm2": 7066}
HEARING = {"qwen3-asr-0.6b": 2314, "qwen3-asr-1.7b": 4896}
# The same on the processor, in the computer's memory: Ollama with no layers on the card, Pocket speaking, the
# built-in hearing transcribing. A GPU-only voice or hearing does not run there at all.
BRAINS_CPU: dict[str, tuple[tuple[int, int], ...]] = {
    "granite4.2:8b": ((16, 6528), (32, 7889), (64, 10611)),
    "qwen3.5:4b": ((16, 3827), (32, 3999), (64, 4609)),
}
VOICES_CPU = {"pocket": 955}
HEARING_CPU = {"fluid-speech-pro": 923, "parakeet": 1140}

PER_K = 85            # an unmeasured brain's MiB per 1K of context: Granite 8B and the 27B take 83 to 91
OVERHEAD = 1536       # an unmeasured brain's MiB beyond its weights at 16K (Granite 8B 1.7 GB, Qwen 4B 1.1 GB)
GREEN, FILL = 0.80, 0.90   # the bar is green under GREEN; the recommendation grows the context up to FILL
MIN_K, MAX_K, STEP_K = 16, 128, 4


def at(points: tuple[tuple[int, int], ...], k: float) -> float:
    """MiB at k K tokens, on the line through the nearest two measured points."""
    i = next((n for n, (pk, _) in enumerate(points) if pk >= k), len(points) - 1)
    (k0, v0), (k1, v1) = points[max(i, 1) - 1], points[max(i, 1)]
    return v0 + (v1 - v0) * (k - k0) / (k1 - k0)


def estimate(weights: int) -> tuple[tuple[int, int], ...]:
    """A brain's points from the bytes of its weights."""
    base = weights / 2 ** 20 + OVERHEAD
    return (MIN_K, round(base)), (64, round(base + (64 - MIN_K) * PER_K))


def fill(total: int, fixed: float, points: tuple[tuple[int, int], ...]) -> int:
    """The context (K) to recommend: the largest step that keeps the card under FILL, never under MIN_K."""
    k = MIN_K
    while k + STEP_K <= MAX_K and fixed + at(points, k + STEP_K) <= FILL * total:
        k += STEP_K
    return k


def _model(p: dict) -> str:
    from winston.config.presets import preset
    return (p.get("settings") or {}).get("llm", {}).get("model") \
        or preset("llm", p["presets"]["llm"])["settings"]["llm"]["model"]


def recommend(total: int, other: int, kind: str = "nvidia") -> dict[str, Any]:
    """Setup's picks for total MiB with other MiB held by other programs, then the context filled last. On an
    NVIDIA card (kind), the profile setup.pick sizes to it. On an AMD card or the processor ("amd", "ram"), Ollama
    and Pocket, the only picks that run there, with Ollama's brain, or the 8 GB profile's smaller one when that
    does not fit; on the processor the voice and hearing share the memory too."""
    from winston.config.presets import profile
    from winston.setup import pick_for
    if kind == "nvidia":
        p = profile(pick_for(total))
        model, table = _model(p), BRAINS
        fixed = other + VOICES.get(p["presets"].get("tts", ""), 0)
    else:
        p, table = profile("ollama"), BRAINS if kind == "amd" else BRAINS_CPU
        fixed = other + (VOICES_CPU["pocket"] + HEARING_CPU["fluid-speech-pro"] if kind == "ram" else 0)
        big, small = _model(p), _model(profile("nvidia-8gb"))
        model = big if fixed + at(table[big], MIN_K) <= FILL * total else small
    points = table.get(model)
    context = fill(total, fixed, points) if points else MIN_K
    return {"profile": p["id"], "llm": p["presets"]["llm"], "model": model, "tts": p["presets"].get("tts"),
            "stt": "fluid-speech-pro", "context": context * 1024}


assert round(at(BRAINS["granite4.2:8b"], 24)) == 7492 and round(at(BRAINS["granite4.2:8b"], 128)) == 16906
assert fill(16376, 300 + VOICES["breeze2"], BRAINS["granite4.2:8b"]) == 40


# ---- Read from this computer -------------------------------------------------------------------------------

OURS = ("llama-server", "vllm", "ollama", "/services/tts-", "/services/stt-")   # his engines' processes, any case
AMD_MIN = 4096   # ponytail: an AMD device with less is an integrated one's carve-out, not a card for the brain


@dataclass
class Memory:
    kind: str            # nvidia | amd | ram (no graphics card: the computer's own memory)
    name: str | None     # the card's name; None for ram
    total: int           # MiB
    used: int            # MiB in use now, by anything


def _run(cmd: list[str]) -> str:
    import subprocess
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def nvidia(csv: str) -> Memory | None:
    """The largest card in `nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader,nounits`."""
    cards = []
    for line in csv.splitlines():
        name, _, rest = line.partition(", ")
        used, _, total = rest.partition(", ")
        if used.strip().isdigit() and total.strip().isdigit():
            cards.append(Memory("nvidia", name.strip(), int(total), int(used)))
    return max(cards, key=lambda c: c.total, default=None)


def amd(drm: Path = Path("/sys/class/drm")) -> Memory | None:
    """The largest AMD card, from the kernel's amdgpu files (no tool needed)."""
    cards = []
    for card in drm.glob("card[0-9]*"):
        d = card / "device"
        try:
            if "-" in card.name or (d / "vendor").read_text().strip() != "0x1002":
                continue
            total = int((d / "mem_info_vram_total").read_text()) // 2 ** 20
            used = int((d / "mem_info_vram_used").read_text()) // 2 ** 20
        except (OSError, ValueError):
            continue
        name = (d / "product_name").read_text().strip() if (d / "product_name").is_file() else ""
        if total >= AMD_MIN:
            cards.append(Memory("amd", name or "AMD graphics card", total, used))
    return max(cards, key=lambda c: c.total, default=None)


def ram(meminfo: str) -> Memory:
    """The computer's memory from /proc/meminfo: in use is all but what is available."""
    kb = {k: int(v.split()[0]) for k, _, v in (line.partition(":") for line in meminfo.splitlines()) if v.strip()}
    return Memory("ram", None, kb["MemTotal"] // 1024, (kb["MemTotal"] - kb["MemAvailable"]) // 1024)


def here() -> Memory:
    """What the brain runs in on this computer: the NVIDIA card, else the AMD card, else the computer's memory."""
    return nvidia(_run(["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader,nounits"])) \
        or amd() or ram(Path("/proc/meminfo").read_text())


def _ours(pid: str) -> bool:
    """Whether a process is one of his engines'. Any case: vLLM renames the one holding the card VLLM::EngineCore."""
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").lower()
    except OSError:
        return False
    return any(o in cmd for o in OURS)


def other(m: Memory) -> int | None:
    """MiB of what is in use that is not his engines': the desktop, a browser, a game. On an NVIDIA card compute
    processes are told apart by their command line, and what nvidia-smi lists for none (the display) counts as
    other; in the computer's memory each process's own (anonymous) memory. An AMD card does not say which program
    holds what, so there it is all of it: the caller knows whether his engines hold any."""
    if m.kind == "amd":
        return m.used
    ours = 0
    if m.kind == "nvidia":
        for line in _run(["nvidia-smi", "--query-compute-apps=pid,used_memory",
                          "--format=csv,noheader,nounits"]).splitlines():
            pid, _, mib = line.partition(",")
            if mib.strip().isdigit() and _ours(pid.strip()):
                ours += int(mib)
    else:
        for proc in Path("/proc").glob("[0-9]*"):
            if _ours(proc.name):
                try:
                    ours += next(int(line.split()[1]) for line in (proc / "status").read_text().splitlines()
                                 if line.startswith("RssAnon:")) // 1024
                except (OSError, StopIteration, ValueError):
                    pass
    return max(0, m.used - ours)


def vllm_share(args: list | None) -> float:
    """The share of the card vLLM takes whatever the model: its --gpu-memory-utilization, else vLLM's default 0.9."""
    args, share = [str(a) for a in args or []], 0.9
    for i, a in enumerate(args):
        v = args[i + 1] if a == "--gpu-memory-utilization" and i + 1 < len(args) else \
            a.partition("=")[2] if a.startswith("--gpu-memory-utilization=") else None
        try:
            share = float(v) if v is not None else share
        except ValueError:
            pass
    return share


async def brain(model: str, kind: str | None, base: str, cpu: bool = False, args: list | None = None) -> dict[str, Any]:
    """A brain's memory, on the card or (cpu) the processor: measured points, points estimated from its weights,
    vLLM's share (from its engine's args), or nothing known."""
    if kind == "vllm":
        return {"points": None, "measured": not cpu, "share": None if cpu else vllm_share(args)}
    if points := (BRAINS_CPU if cpu else BRAINS).get(model):
        return {"points": points, "measured": True, "share": None}
    weights = None
    try:
        if kind == "llamacpp" and ":" in model:
            from winston.engines.install import _gguf, _repo_files
            repo, _, quant = model.partition(":")
            files = await _repo_files(repo)
            weights = sum(files[f] for f in _gguf(list(files), quant)) or None
        elif kind == "ollama":
            weights = await _ollama_bytes(model, base)
    except Exception:  # noqa: BLE001  (offline, or no such model: nothing known)
        weights = None
    return {"points": estimate(weights) if weights else None, "measured": False, "share": None}


async def _ollama_bytes(model: str, base: str) -> int | None:
    import httpx

    from winston.engines.install import _manifest_size
    name = model if ":" in model.rsplit("/", 1)[-1] else f"{model}:latest"
    async with httpx.AsyncClient(timeout=5) as c:
        tags = (await c.get(base + "/api/tags")).json().get("models") or []
    if size := next((t.get("size") for t in tags if name in (t.get("name"), t.get("model"))), None):
        return size
    path, tag = name.rsplit(":", 1)
    first, _, rest = path.partition("/")
    host, repo = (first, rest) if rest and "." in first else ("registry.ollama.ai", path if rest else f"library/{path}")
    return await _manifest_size(host, repo, tag, "ollama")


# ---- A brain stopped for want of the computer's memory ---------------------------------------------------------
# A brain on the processor that does not fit is stopped by the kernel as it loads (Granite 8B at 44K on a 16 GB
# computer, 2026-11-26): the request only loses its connection, and a system Ollama comes back healthy seconds
# later. So a failed request is checked against the kernel's count, and what it finds is noted in data/state for
# winstond's status, since the Talk page, Discord and `winston talk` ask the brain from their own processes.

LOCAL = ("127.0.0.1", "localhost", "::1")


class OutOfMemory(Exception):
    """The brain on this computer was stopped for want of memory during a request."""

    def __init__(self, context: int):
        super().__init__(f"the computer ran out of memory with {context // 1024}K of context: lower How much he "
                         "keeps in mind on Brain, or close other programs")


def oom_kills(vmstat: Path = Path("/proc/vmstat")) -> int | None:
    """Processes the kernel has stopped for want of memory since the computer started; None where not kept."""
    try:
        m = re.search(r"^oom_kill (\d+)$", vmstat.read_text(), re.MULTILINE)
    except OSError:
        return None
    return int(m[1]) if m else None


def _note() -> Path:
    from winston.config.loader import data_dir
    return data_dir() / "state" / "brain_out_of_memory.txt"


def out_of_memory(cfg: WinstonConfig, before: int | None, err: BaseException) -> OutOfMemory | None:
    """For a brain request that failed with err: OutOfMemory, noted for the status, when the brain runs on this
    computer, its server went away (unreachable) and the kernel stopped a process for want of memory since
    `before` (oom_kills() as it began); else None. ponytail: any process stopped meanwhile counts, as the kernel
    names its victim only in dmesg; an Ollama whose runner alone is stopped answers 500 and is not caught."""
    from winston.core.brain import unreachable
    now = oom_kills()
    if (before is None or now is None or now <= before or urlsplit(cfg.llm.base_url).hostname not in LOCAL
            or not unreachable(err)):
        return None
    e = OutOfMemory(cfg.llm.context)
    _note().parent.mkdir(parents=True, exist_ok=True)
    _note().write_text(str(e))
    return e


def stopped_for_memory() -> str:
    """Why the brain was last stopped for want of memory, until it answers again or is restarted; "" if not."""
    try:
        return _note().read_text()
    except OSError:
        return ""


def answered() -> None:
    """The brain answered (or was restarted): any earlier out of memory no longer holds."""
    _note().unlink(missing_ok=True)
