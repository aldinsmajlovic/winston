"""A throwaway winstond with a made-up turn record (a kitchen box and a browser, today and yesterday, two
turns marked), for looking at the Turns page and the marks, and taking their screenshots. Never touches
data/. Prints the URL; Ctrl-C stops it.

    uv run python tests/turns_demo.py [--port 8791]
"""
import argparse
import asyncio
import shutil
from datetime import datetime, timedelta

import numpy as np
from harness import REPO, scratch_data

DATA = scratch_data("winston-turns-demo-")

import uvicorn  # noqa: E402
import yaml  # noqa: E402

ENGINE = "parakeet/nemo-parakeet-tdt-0.6b-v3"


def tone(secs: float) -> np.ndarray:
    """Stands in for a recording: a soft rising tone as long as the request."""
    t = np.arange(int(secs * 16000)) / 16000
    return (np.sin(2 * np.pi * (180 + 40 * t) * t) * 3000 * np.minimum(1, 4 * t) * np.minimum(1, 4 * (secs - t))).astype(np.int16)


# (minutes before the newest, room, start, heard, raw, tools, said, outcome, first audio ms, mark)
TODAY = [
    (14, "kitchen", "wake", "Turn on the kitchen lights.", None,
     [("intent__HassTurnOn", {"name": "kitchen lights"})], "The kitchen lights are on, sir.", None, 1240, None),
    (12, "kitchen", "follow_up", "Make them a bit warm or.", "Make them a bit warm or.",
     [("light__HassLightSet", {"name": "kitchen lights", "color_temp_kelvin": 2700})],
     "Done, sir. They are warmer now.", None, 1810, {"kind": "misheard", "said": "Make them a bit warmer."}),
    (11, "kitchen", "follow_up", "Did you see the game last night?", None, [],
     "I'm afraid I don't follow sports, sir.", None, 1650, {"kind": "not_addressed"}),
    (5, None, "speech", "What's the weather tomorrow?", None,
     [("web_search", {"query": "weather tomorrow"})], "Tomorrow looks dry, eleven degrees and cloudy, sir.", None, 2930,
     None),
    (1, "kitchen", "wake", "", None, [], "", "nothing_heard", None, None),
]
YESTERDAY = [
    (30, "kitchen", "wake", "Set a timer for ten minutes.", None,
     [("intent__HassStartTimer", {"minutes": 10})], "A ten minute timer is running, sir.", None, 1380, None),
    (2, "kitchen", "button", "Turn off all the lights.", None,
     [("intent__HassTurnOff", {"domain": ["light"]})], "All the lights are off. Good night, sir.", None, 1490, None),
]


def seed(cfg):
    from winston.core.hints import Heard
    from winston.core.turns import TurnLog, TurnStore
    logs = {c: TurnLog(cfg, c) for c in ("satellite", "voice")}
    marks = []
    for day, rows in ((datetime.now(), TODAY), (datetime.now() - timedelta(days=1), YESTERDAY)):
        newest = day.replace(hour=21, minute=20, second=0, microsecond=0)
        for mins, room, start, heard, raw, tools, said, outcome, first, mark in rows:
            channel = "voice" if start == "speech" else "satellite"
            t = logs[channel].begin(room=room, start=start, ts=(newest - timedelta(minutes=mins)).timestamp())
            secs = max(0.8, len(heard) / 14)
            t.add(heard, Heard(heard, raw or heard, [], secs, 210, ENGINE, tone(secs), 16000))
            t.tools = [{"name": n, "args": a, "ok": True, "result": "{}"} for n, a in tools]
            t.spoken = [said] if said else []
            t.outcome, t.stt_ms, t.first_audio_ms = outcome, 1020 if heard else None, first
            t.first_token_ms = first - 500 if first else None
            logs[channel].write_now(t)
            if mark:
                marks.append((t.id, mark))
    store = TurnStore(cfg)
    for turn_id, mark in marks:
        store.mark(turn_id, mark.pop("kind"), by="page", **mark)


def setup(api_port: int):
    shutil.copytree(REPO / "examples" / "memory", DATA / "memory")
    shutil.copy(REPO / "examples" / "persona.md", DATA / "persona.md")
    cfg = {"llm": {"model": "none"},
           "channels": {"voice": {"enabled": False}, "satellite": {"enabled": False}, "discord": {"enabled": False}},
           "announce": {"enabled": False},
           "logging": {"turns": {"enabled": True}},
           "control": {"port": api_port}}
    (DATA / "config.yaml").write_text(yaml.safe_dump(cfg))


async def main(port: int):
    setup(port)
    from winston.api.app import create_app
    from winston.config import load
    cfg = load()
    seed(cfg)
    app = create_app(cfg, autostart=False)
    print(f"winstond with a made-up turn record: http://127.0.0.1:{port}/turns  (data {DATA})", flush=True)
    await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")).serve()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8791)
    try:
        asyncio.run(main(ap.parse_args().port))
    except KeyboardInterrupt:
        pass
    finally:
        shutil.rmtree(DATA, ignore_errors=True)
