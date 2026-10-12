"""Every profile and preset renders a config the schema accepts, from the example config.

    uv run python tests/profiles_load.py
"""
import os
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
# A version 1 file's tools section loads as the built-in extensions, with the same settings.
v1 = {"version": 1, "llm": {"model": "x"}, "extensions": {"hevy": {"enabled": True}}, "tools": {
    "sensors": {"Phone": {"level": "sensor.phone_battery_level"}},
    "web_search": {"enabled": True, "provider": "gemini", "api_key_env": "GEMINI_API_KEY", "max_per_day": 50, "timeout": 15},
    "ask_claude": {"enabled": False, "model": "claude-opus-5-5", "max_usd_per_day": 2.0}}}
os.environ.pop("ANTHROPIC_API_KEY", None)                           # else it stays Ask Claude's key
ext = WinstonConfig.model_validate(v1).model_dump(mode="json")["extensions"]
want = {"hevy": (True, {}), "sensors": (True, {"sensors": {"Phone": {"level": "sensor.phone_battery_level"}}}),
        "web": (True, {"api_key": "GEMINI_API_KEY", "max_per_day": 50}),
        "claude": (False, {"model": "claude-opus-5-5", "max_usd_per_day": 2.0})}
got = {k: (v["enabled"], v["settings"]) for k, v in ext.items()}
if got != want:
    failed += 1; print(f"FAIL version 1 tools: {got}")
else:
    print("ok   a version 1 tools section loads as the built-in extensions")
print("\nall profiles and presets load" if not failed else f"\n{failed} failed")
sys.exit(1 if failed else 0)
