"""Every profile and preset renders a config the schema accepts, from the example config.

    uv run python tests/profiles_load.py
"""
import sys
from pathlib import Path

import yaml

from winston.config.presets import LLM, PROFILES, TTS, apply, apply_profile, stt_presets
from winston.config.schema import WinstonConfig

base = yaml.safe_load((Path(__file__).resolve().parents[1] / "examples/config.yaml").read_text())
failed = 0
for p in PROFILES:
    try:
        cfg = WinstonConfig.model_validate(apply_profile(base, p["id"]))
        llm, tts = cfg.engines["llm"], cfg.engines["tts"]
        print(f"ok   profile {p['id']:<12} llm {llm.driver:<10} {cfg.llm.model:<28} thinking {cfg.llm.thinking.control:<16} "
              f"tts {tts.driver:<10} voice {cfg.tts.voice}")
    except Exception as e:                                          # noqa: BLE001
        failed += 1; print(f"FAIL profile {p['id']}: {e}")
for kind, group in (("llm", LLM), ("tts", TTS), ("stt", stt_presets())):
    for p in group:
        try:
            WinstonConfig.model_validate(apply(base, kind, p["id"]))
            print(f"ok   preset {kind}/{p['id']}")
        except Exception as e:                                      # noqa: BLE001
            failed += 1; print(f"FAIL preset {kind}/{p['id']}: {e}")
print("\nall profiles and presets load" if not failed else f"\n{failed} failed")
sys.exit(1 if failed else 0)
