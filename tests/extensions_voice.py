"""Voice turns while an extension misbehaves. End-of-speech to first audio through the real pipeline
(tests/m2_headless_roundtrip.py, the same Brain as the voice channel, extension tools included) against
the running LLM and TTS engines: once with the probe extension quiet, once while it blocks its own
loop, grows past its memory limit and crashes, over and over. The extension runs in a throwaway
winstond with the fake Home Assistant and a copy of the engine settings; data/ is only read.

Needs the LLM and TTS engines up (winston up, or their engines started from winstond).

    uv run python tests/extensions_voice.py
"""
import asyncio
import os
import shutil
import statistics
import sys
from pathlib import Path

from harness import free_port, real_engines_data, scratch_data, serve, voice_turns

REAL = Path(os.environ.get("WINSTON_DATA") or Path(__file__).resolve().parent.parent / "data")
DATA = scratch_data("winston-extensions-voice-")

import httpx  # noqa: E402
from fake_ha import FakeHA  # noqa: E402


QUESTIONS = ["What's the capital of Sweden?", "How far away is the moon?", "Tell me a short joke.",
             "What year did the Berlin wall fall?", "Say something nice about Mondays.",
             "How many legs does a spider have?"]


async def misbehave(api: httpx.AsyncClient, stop: asyncio.Event, counts: dict[str, int]):
    """Over and over: block the loop for 20 s, grow past the memory limit, crash."""
    while not stop.is_set():
        for tool, args in (("block", {"seconds": 20}), ("hog", {"mb": 300}), ("die", {})):
            if stop.is_set():
                break
            try:
                await api.post(f"/api/extensions/probe/tools/{tool}", json=args, timeout=30)
                counts[tool] = counts.get(tool, 0) + 1
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)


async def main():
    api_port, ha_port = free_port(), free_port()
    real_engines_data(DATA, REAL, api_port, ha_port, {"probe": {"enabled": True, "memory_mb": 150}},
                      ("probe.py",), env='PROBE_KEY="x"\n')
    import winston.extension_host.process as P
    P.BACKOFF = (1, 1, 1)                               # keep it coming back, so it keeps misbehaving
    from winston.api.app import create_app
    from winston.config import load
    async with FakeHA(ha_port):
        app = create_app(load(), autostart=False)
        stop_server = await serve(app, api_port)
        host = app.state.extensions
        await host.start()
        api = httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}")
        for _ in range(100):
            if host.procs.get("probe") and host.procs["probe"].state == "running":
                break
            await asyncio.sleep(0.2)
        quiet = await voice_turns("probe quiet", QUESTIONS)
        stop, counts = asyncio.Event(), {}
        chaos = asyncio.create_task(misbehave(api, stop, counts))
        await asyncio.sleep(2)
        busy = await voice_turns("probe misbehaving", QUESTIONS)
        stop.set()
        await chaos
        exits = [e["why"] for e in host.procs["probe"].exits]
        print(f"meanwhile the probe was told to block {counts.get('block', 0)}, hog {counts.get('hog', 0)} and die "
              f"{counts.get('die', 0)} times; it stopped {len(exits)} times: {sorted(set(exits))}")
        await host.stop_all()
        await stop_server()
        await api.aclose()
    if quiet and busy:
        a, b = statistics.median(quiet), statistics.median(busy)
        ok = b <= a * 1.1 + 150
        print(f"\nmedian first audio: {a:.0f} ms quiet, {b:.0f} ms while it misbehaved "
              f"({'within' if ok else 'OVER'} 10% + 150 ms)")
        return ok
    return False


if __name__ == "__main__":
    try:
        ok = asyncio.run(main())
    finally:
        shutil.rmtree(DATA, ignore_errors=True)
    sys.exit(0 if ok else 1)
