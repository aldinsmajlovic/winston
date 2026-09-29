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
import re
import shutil
import socket
import statistics
import sys
import tempfile
import time
from pathlib import Path

DATA = Path(tempfile.mkdtemp(prefix="winston-extensions-voice-"))
REAL = Path(os.environ.get("WINSTON_DATA") or Path(__file__).resolve().parent.parent / "data")
os.environ["WINSTON_DATA"] = str(DATA)
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
import yaml  # noqa: E402
from fake_ha import FakeHA  # noqa: E402


QUESTIONS = ["What's the capital of Sweden?", "How far away is the moon?", "Tell me a short joke.",
             "What year did the Berlin wall fall?", "Say something nice about Mondays.",
             "How many legs does a spider have?"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def setup(api_port: int, ha_port: int):
    cfg = yaml.safe_load((REAL / "config.yaml").read_text())
    for c in ("voice", "satellite", "discord"):
        cfg.setdefault("channels", {}).setdefault(c, {})["enabled"] = False
    cfg["announce"] = {"enabled": False}
    ha = cfg.setdefault("providers", {}).setdefault("home_assistant", {})
    ha.update({"url": f"http://127.0.0.1:{ha_port}", "token_env": "HA_TOKEN", "expose": {"light.taklampa": {}}})
    cfg["control"] = {**cfg.get("control", {}), "port": api_port, "host": "127.0.0.1"}
    cfg["extensions"] = {"probe": {"enabled": True, "memory_mb": 150}}
    cfg.setdefault("logging", {})
    (DATA / "config.yaml").write_text(yaml.safe_dump(cfg))
    shutil.copy(REAL / "persona.md", DATA / "persona.md")          # his real instructions, so prompts are real-sized
    shutil.copytree(REPO / "examples" / "memory", DATA / "memory")
    (DATA / "extensions").mkdir()
    shutil.copy(REPO / "tests" / "fixtures" / "extensions" / "probe.py", DATA / "extensions" / "probe.py")
    (DATA / ".env").write_text('HA_TOKEN="fake-ha-token"\nPROBE_KEY="x"\n')
    os.chmod(DATA / ".env", 0o600)


async def voice_turns(label: str) -> list[float]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(REPO / "tests" / "m2_headless_roundtrip.py"), *QUESTIONS,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env={**os.environ})
    out, _ = await proc.communicate()
    text = out.decode(errors="replace")
    firsts = [float(m) for m in re.findall(r"FIRST AUDIO (\d+(?:\.\d+)?) ms", text)]
    for block in text.split("\n> ")[1:]:
        q = block.splitlines()[0]
        ms = re.search(r"FIRST AUDIO (\d+)", block)
        reply = re.search(r"reply: (.*)", block)
        print(f"  {ms.group(1) if ms else '?':>5} ms  {q}  ->  {reply.group(1)[:70] if reply else ''}")
    if not firsts:
        print(text[-3000:])
    print(f"{label}: first audio {', '.join(f'{f:.0f}' for f in firsts)} ms")
    return firsts


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
    setup(api_port, ha_port)
    import winston.extension_host.process as P
    P.BACKOFF = (1, 1, 1)                               # keep it coming back, so it keeps misbehaving
    from winston.api.app import create_app
    from winston.config import load
    async with FakeHA(ha_port):
        app = create_app(load(), autostart=False)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning"))
        serving = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.1)
        host = app.state.extensions
        await host.start()
        api = httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}")
        for _ in range(100):
            if host.procs.get("probe") and host.procs["probe"].state == "running":
                break
            await asyncio.sleep(0.2)
        quiet = await voice_turns("probe quiet")
        stop, counts = asyncio.Event(), {}
        chaos = asyncio.create_task(misbehave(api, stop, counts))
        await asyncio.sleep(2)
        busy = await voice_turns("probe misbehaving")
        stop.set()
        await chaos
        exits = [e["why"] for e in host.procs["probe"].exits]
        print(f"meanwhile the probe was told to block {counts.get('block', 0)}, hog {counts.get('hog', 0)} and die "
              f"{counts.get('die', 0)} times; it stopped {len(exits)} times: {sorted(set(exits))}")
        await host.stop_all()
        server.should_exit = True
        await serving
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
