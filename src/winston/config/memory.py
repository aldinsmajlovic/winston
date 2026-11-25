"""Graphics memory each pick takes, and the picks recommended for a card.

Measured with nvidia-smi on the RTX 3090 on 2026-11-25, each part working (the brain answering, the voice
speaking, hearing transcribing), without the desktop's share: docs/hardware.md (Sizing by the card). A brain
not measured is estimated from the size of its weights. The Brain page's memory bar and setup's recommendation
both read these numbers, so they agree.
"""
from __future__ import annotations

from typing import Any

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

PER_K = 85            # an unmeasured brain's MiB per 1K of context: Granite 8B and the 27B take 83 to 91
OVERHEAD = 1536       # an unmeasured brain's MiB beyond its weights at 16K (Granite 8B 1.7 GB, Qwen 4B 1.1 GB)
VLLM_SHARE = 0.70     # vLLM takes its --gpu-memory-utilization share of the card, whatever the model
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


def recommend(total: int, other: int) -> dict[str, Any]:
    """Setup's picks for a card of total MiB with other MiB held by other programs: the profile setup.pick
    chooses, and the context filled last."""
    from winston.config.presets import preset, profile
    from winston.setup import pick_for
    p = profile(pick_for(total))
    model = (p.get("settings") or {}).get("llm", {}).get("model") \
        or preset("llm", p["presets"]["llm"])["settings"]["llm"]["model"]
    points = BRAINS.get(model)
    voice = VOICES.get(p["presets"].get("tts", ""), 0)
    context = fill(total, other + voice, points) if points else MIN_K
    return {"profile": p["id"], "llm": p["presets"]["llm"], "model": model, "tts": p["presets"].get("tts"),
            "stt": "fluid-speech-pro", "context": context * 1024}


assert round(at(BRAINS["granite4.2:8b"], 24)) == 7492 and round(at(BRAINS["granite4.2:8b"], 128)) == 16906
assert fill(16376, 300 + VOICES["breeze2"], BRAINS["granite4.2:8b"]) == 40


# ---- Read from this computer -------------------------------------------------------------------------------

OURS = ("llama-server", "vllm", "ollama", "/services/tts-", "/services/stt-")   # his engines' processes


def other(used: int) -> int:
    """MiB of used that is not his engines': the desktop, a browser, a game. Compute processes are told apart by
    their command line; what nvidia-smi lists for none (the display) counts as other."""
    import subprocess
    from pathlib import Path
    try:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=3, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return used
    ours = 0
    for line in out.splitlines():
        pid, _, mib = line.partition(",")
        try:
            cmd = Path(f"/proc/{pid.strip()}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if any(o in cmd for o in OURS) and mib.strip().isdigit():
            ours += int(mib)
    return max(0, used - ours)


async def brain(model: str, kind: str | None, base: str) -> dict[str, Any]:
    """A brain's memory: measured points, points estimated from its weights, vLLM's share, or nothing known."""
    if kind == "vllm":
        return {"points": None, "measured": True, "share": VLLM_SHARE}
    if points := BRAINS.get(model):
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
