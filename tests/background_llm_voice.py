"""Voice turns while extensions use the background model (Extensions track step 3: "background model
calls are measured against the voice channel's prompt cache"). End-of-speech to first audio through
the real pipeline (tests/m2_headless_roundtrip.py, the voice channel's Brain) against the running LLM
and TTS engines: once with nothing else asking the model, once while an extension asks it one story
after another (a news extension judging stories does this). Background calls wait while a conversation
runs, so each turn here starts after 33 s of quiet: in the second run the extension is mid-question
when he is spoken to, and its call has to stop for the turn. The extension runs in a throwaway
winstond with the fake Home Assistant and a copy of the engine settings; data/ is only read.

Needs the LLM and TTS engines up.

    uv run python tests/background_llm_voice.py
"""
import asyncio
import os
import shutil
import statistics
import sys
from pathlib import Path

from harness import free_port, real_engines_data, scratch_data, serve, voice_turns

REAL = Path(os.environ.get("WINSTON_DATA") or Path(__file__).resolve().parent.parent / "data")
DATA = scratch_data("winston-background-llm-")

import httpx  # noqa: E402
from fake_ha import FakeHA  # noqa: E402


QUESTIONS = ["What's the capital of Sweden?", "How far away is the moon?", "Tell me a short joke.",
             "Say something nice about Mondays.", "How many legs does a spider have?"]
QUIET_SECS = 33                 # past the 30 s after which a conversation is over


def log_lines(text: str) -> list[str]:
    return [ln for ln in LOG if text in ln]


LOG: list[str] = []


async def main():
    from loguru import logger
    logger.add(lambda m: LOG.append(str(m)), level="INFO")
    api_port, ha_port = free_port(), free_port()
    real_engines_data(DATA, REAL, api_port, ha_port, {"asker": {"enabled": True}}, ("asker.py",))
    from winston.api.app import create_app
    from winston.config import load
    async with FakeHA(ha_port):
        app = create_app(load(), autostart=False)
        stop_server = await serve(app, api_port)
        host = app.state.extensions
        await host.start()
        api = httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}")
        for _ in range(100):
            if host.procs.get("asker") and host.procs["asker"].state == "running":
                break
            await asyncio.sleep(0.2)
        async def spaced(label: str) -> list[float]:
            out = []
            for q in QUESTIONS:
                await asyncio.sleep(QUIET_SECS)
                out += await voice_turns(label, [q])
            return out
        quiet = await spaced("model otherwise idle")
        stopped = len(log_lines("stopped for a conversation"))
        await api.post("/api/extensions/asker/tools/asking", json={"on": True}, timeout=30)
        busy = await spaced("an extension asking the model between turns")
        r = (await api.post("/api/extensions/asker/tools/asking", json={"on": False}, timeout=30)).json()
        stopped = len(log_lines("stopped for a conversation")) - stopped
        print(f"meanwhile the extension asked the background model {r['result']['asked']} times; "
              f"{stopped} of its calls stopped for a turn and were asked again after it")
        log = host.log_path("asker")
        if r["result"]["asked"] == 0 and log.is_file():
            print("its log:\n" + "\n".join(log.read_text().splitlines()[-15:]))
        await host.stop_all()
        await stop_server()
        await api.aclose()
    if quiet and busy:
        a, b = statistics.median(quiet), statistics.median(busy)
        ok = b <= a * 1.1 + 150
        print(f"\nmedian first audio: {a:.0f} ms with the model idle, {b:.0f} ms while an extension asked it "
              f"({'within' if ok else 'OVER'} 10% + 150 ms)")
        return ok
    return False


if __name__ == "__main__":
    try:
        ok = asyncio.run(main())
    finally:
        shutil.rmtree(DATA, ignore_errors=True)
    sys.exit(0 if ok else 1)
