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
        print(f"ok   profile {p["id"]:<16} llm {llm.driver:<10} {cfg.llm.model:<28} thinking {cfg.llm.thinking.control:<16} "
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
# Engine commands take config values: the Chatterbox preset's streaming arguments expand, and changing
# one restarts the speech engine.
from winston.config.restarts import restarts_for
from winston.engines.supervisor import _expand_model

cfg = WinstonConfig.model_validate(apply(base, "tts", "chatterbox"))
cmd = _expand_model(cfg.engines["tts"], cfg).cmd
want = ["--stream", "true", "--chunk-secs", "1.6", "--first-secs", "0.96", "--lookahead-secs", "0.32"]
if cmd[-8:] != want:
    failed += 1; print(f"FAIL streaming args expand: {cmd[-8:]}")
else:
    print("ok   streaming args expand from tts.streaming")
old = cfg.model_dump(mode="json"); new = {**old, "tts": {**old["tts"], "streaming": {**old["tts"]["streaming"], "chunk_secs": 3.0}}}
got = restarts_for(old, new, WinstonConfig.model_json_schema())
if got != {"tts"}:
    failed += 1; print(f"FAIL chunk length restarts {got}, want tts")
else:
    print("ok   changing the chunk length restarts tts")
print("\nall profiles and presets load" if not failed else f"\n{failed} failed")
sys.exit(1 if failed else 0)
