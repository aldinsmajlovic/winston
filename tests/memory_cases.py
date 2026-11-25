"""Graphics memory and context (src/winston/config/memory.py, llm.context): the recommendation per card,
filled last; an older config's context moved into llm.context with the engine's args unchanged; the Ollama
copy named by its context.

    uv run python tests/memory_cases.py
"""
import sys
from pathlib import Path

from harness import check, finish_checks

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import yaml

from winston.api.options import COPY, ollama_copy
from winston.config import memory
from winston.config.presets import apply_profile
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

# ---- The Ollama copy ----
check("the copy is named by its context", [ollama_copy("granite4.2:8b"), ollama_copy("granite4.2:8b", 40960)],
      ["granite4.2:8b-16k", "granite4.2:8b-40k"])
check("his copies are told from a pick at any context", [bool(COPY.search(n)) for n in
      ("granite4.2:8b-40k", "granite4.2:8b", "qwen3.5:4b-112k")], [True, False, True])

finish_checks("memory: the recommendation per card, estimates, llm.context and its migration, the Ollama copy")
