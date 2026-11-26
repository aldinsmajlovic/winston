"""Graphics memory, the computer's memory and context (src/winston/config/memory.py, llm.context): the recommendation per card,
filled last; an older config's context moved into llm.context with the engine's args unchanged; the Ollama
copy named by its context.

    uv run python tests/memory_cases.py
"""
import asyncio
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from harness import check, finish_checks

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import yaml

from winston.api.options import COPY, ollama_copy
from winston.config import memory
from winston.config.presets import apply_profile, preset
from winston.config.schema import WinstonConfig
from winston.engines.supervisor import _expand_model

# ---- The recommendation ----
rec = {mib: memory.recommend(mib, 300) for mib in (8192, 10240, 12288, 16376, 24576)}
check("each card's brain and voice, then the context filled up to 90%",
      {mib: (r["profile"], r["context"] // 1024) for mib, r in rec.items()},
      {8192: ("nvidia-8gb", 112), 10240: ("ollama", 40), 12288: ("nvidia-12gb", 20), 16376: ("nvidia-16gb", 40),
       24576: ("nvidia-24gb", 16)})
check("never under 16K, even when the card is already past 90%", memory.recommend(24576, 3000)["context"], 16384)
check("other programs on the card leave the context less",
      memory.recommend(16376, 2000)["context"] < rec[16376]["context"], True)
check("the 8 GB card's brain is its profile's own model", rec[8192]["model"], "qwen3.5:4b")
used = {mib: 300 + memory.at(memory.BRAINS[r["model"]], r["context"] / 1024) + memory.VOICES.get(r["tts"], 0)
        for mib, r in rec.items()}
check("every recommendation fits its card", all(used[mib] <= mib for mib in rec), True)

# ---- Without an NVIDIA card: an AMD card or the processor ----
vm8 = memory.recommend(8192, 1500, "ram")
check("an 8 GB computer without a card: the small brain on the processor, Pocket and Fluid beside it",
      (vm8["profile"], vm8["model"], vm8["tts"], vm8["context"] // 1024), ("ollama", "qwen3.5:4b", "pocket", 28))
vm16 = memory.recommend(16384, 1500, "ram")
check("a 16 GB one fits Ollama's own brain", (vm16["model"], vm16["context"] // 1024 > 16), ("granite4.2:8b", True))
amd16 = memory.recommend(16368, 300, "amd")
check("an AMD card gets Ollama and Pocket, the context filled on the card",
      (amd16["profile"], amd16["llm"], amd16["model"], amd16["context"] // 1024), ("ollama", "ollama", "granite4.2:8b", 100))

# ---- Reading this computer ----
two = "NVIDIA GeForce RTX 3060, 500, 12288\nNVIDIA GeForce RTX 3090, 900, 24576\n"
check("several NVIDIA cards: the largest", memory.nvidia(two), memory.Memory("nvidia", "NVIDIA GeForce RTX 3090", 24576, 900))
check("no NVIDIA card", memory.nvidia(""), None)
drm = Path(tempfile.mkdtemp())
for card, vendor, total, name in (("card0", "0x1002", 16368, "AMD Radeon RX 9070 XT"), ("card1", "0x1002", 512, ""),
                                  ("card2", "0x8086", 16000, "")):
    d = drm / card / "device"
    d.mkdir(parents=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "mem_info_vram_total").write_text(str(total * 2 ** 20))
    (d / "mem_info_vram_used").write_text(str(300 * 2 ** 20))
    if name:
        (d / "product_name").write_text(name)
(drm / "card0-DP-1").mkdir()
check("an AMD card from the kernel's files; an integrated one, another vendor and a connector skipped",
      memory.amd(drm), memory.Memory("amd", "AMD Radeon RX 9070 XT", 16368, 300))
check("the computer's memory: in use is all but what is available",
      memory.ram("MemTotal:       16384000 kB\nMemFree:  100 kB\nMemAvailable:   12288000 kB\n"),
      memory.Memory("ram", None, 16000, 4000))
check("an AMD card cannot say what his engines hold", memory.other(memory.Memory("amd", "x", 16368, 900)), 900)
# vLLM renames the process holding the card VLLM::EngineCore: still his brain, not another program's 16 GB.
fake = subprocess.Popen(["bash", "-c", "exec -a VLLM::EngineCore sleep 30"])
time.sleep(0.2)
run = memory._run
memory._run = lambda cmd: f"{fake.pid}, 16000\n99999999, 500\n"
check("an NVIDIA card: vLLM's renamed engine is his, the rest other programs",
      memory.other(memory.Memory("nvidia", "x", 24576, 22700)), 6700)
memory._run = run
fake.kill()

check("vLLM's share: its --gpu-memory-utilization in either form, else vLLM's own 0.9",
      [memory.vllm_share(["--gpu-memory-utilization", "0.85"]), memory.vllm_share(["--gpu-memory-utilization=0.6"]),
       memory.vllm_share(None), memory.vllm_share(["--gpu-memory-utilization", "much"])], [0.85, 0.6, 0.9, 0.9])
check("the bar's vLLM brain takes the share its engine is given",
      asyncio.run(memory.brain("any/model", "vllm", "", args=["--gpu-memory-utilization", "0.8"]))["share"], 0.8)

# ---- Estimates ----
check("an unmeasured brain: its weights, the overhead, and the measured cost per 1K",
      memory.estimate(5 * 2 ** 30), ((16, 5120 + memory.OVERHEAD), (64, 5120 + memory.OVERHEAD + 48 * memory.PER_K)))
check("past the last measured point the line goes on", round(memory.at(memory.BRAINS["qwen3.5:4b"], 96)) > 5588, True)

# ---- llm.context ----
example = yaml.safe_load((Path(__file__).resolve().parents[1] / "examples/config.yaml").read_text())
cfg = WinstonConfig.model_validate(example)
args = _expand_model(cfg.engines["llm"], cfg).args
check("llama.cpp gets llm.context for each of its two slots", args[args.index("-c") + 1], "32768")
old = {**example, "engines": {**example["engines"], "llm": {**example["engines"]["llm"],
       "args": [a if a != "{llm.context_both}" else "65536" for a in example["engines"]["llm"]["args"]]}}}
moved = WinstonConfig.model_validate(old)
check("an older config's -c becomes llm.context, the args naming it",
      (moved.llm.context, "{llm.context_both}" in moved.engines["llm"].args), (32768, True))
check("and the engine still gets the same -c", _expand_model(moved.engines["llm"], moved).args[args.index("-c") + 1],
      "65536")
vllm = WinstonConfig.model_validate(apply_profile(example, "nvidia-24gb") | {"llm": {**example["llm"], "context": 24576}})
check("a saved llm.context wins over nothing written in the args", vllm.llm.context, 24576)

# ---- llama.cpp on AMD ----
amd_cfg = WinstonConfig.model_validate(apply_profile(example, "ollama") | {"engines": {
    **example["engines"], "llm": preset("llm", "llamacpp-amd")["engine"]}})
e = _expand_model(amd_cfg.engines["llm"], amd_cfg)
check("the AMD preset: the Vulkan image, the card's devices, no NVIDIA runtime, the same arguments",
      ("vulkan" in e.image, e.devices, e.runtime, e.gpu, e.args[e.args.index("-c") + 1]),
      (True, ["/dev/dri"], None, False, "32768"))

# ---- The Ollama copy ----
check("the copy is named by its context", [ollama_copy("granite4.2:8b"), ollama_copy("granite4.2:8b", 40960)],
      ["granite4.2:8b-16k", "granite4.2:8b-40k"])
check("his copies are told from a pick at any context", [bool(COPY.search(n)) for n in
      ("granite4.2:8b-40k", "granite4.2:8b", "qwen3.5:4b-112k")], [True, False, True])

finish_checks("memory: the recommendation per card, estimates, llm.context and its migration, the Ollama copy")
