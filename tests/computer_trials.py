"""Computer trials: does the brain reach a computer's tools ([computers](../docs/computer.md)) from plain
requests? Typed turns with thinking off, as on the spoken channels, against the real LLM and the running
winstond's tools. The computer is the first switched-on MCP server with a `security_updates` tool; its
`open` is stubbed, so nothing opens, and its read tools run. Each trial is a fresh conversation.

    uv run python tests/computer_trials.py [--n 3]

A comparison, run on purpose like `winston check brain`."""
import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx
from loguru import logger

os.environ["WINSTON_THINKING"] = "0"
from winston.channels.text import TextSession  # noqa: E402
from winston.config import load  # noqa: E402
from winston.extension_host.client import make_handler  # noqa: E402

logger.remove(); logger.add(sys.stderr, level="WARNING")

# What is said, the tool it should reach, and what its arguments should hold (lowercased, in any field).
CASES = [
    ("How is my computer doing?", "status", None),
    ("Does my computer need any updates?", "security_updates", None),
    ("Is there an update waiting for Firefox?", "security_updates", "firefox"),
    ("Any errors in the docker log today?", "logs", "docker"),
    ("Open Firefox on my computer.", "open", "firefox"),
    ("Open youtube.com on the computer.", "open", "youtube.com"),
    ("Open the budget spreadsheet in my documents.", "open", "budget"),
]


def computer(cfg) -> tuple[str | None, dict[str, dict]]:
    """The computer's id and its tools as the brain gets them."""
    tools = httpx.get(f"{cfg.control.url}/api/extensions/tools", timeout=10).json()
    pc = next((t["extension"] for t in tools if t["tool"] == "security_updates"), None)
    return pc, {t["name"]: t for t in tools if t["extension"] == pc}


async def trial(cfg, specs: dict[str, dict], said: str) -> tuple[list[tuple[str, dict]], str, float]:
    calls: list[tuple[str, dict]] = []
    s = TextSession(cfg, mentions=False, channel="computer-trial")
    await s.start()
    for name, spec in specs.items():
        real = make_handler(cfg, spec, "computer-trial", lambda: None)  # what the brain really calls

        async def spy(params, _spec=spec, _real=real):
            calls.append((_spec["tool"], dict(params.arguments)))
            if _spec["tool"] == "open":                 # stubbed: nothing opens
                await params.result_callback({"opened": next(iter(params.arguments.values()), None)})
            else:
                await _real(params)
        s.brain.llm.register_function(name, spy)
    t0 = time.perf_counter()
    try:
        reply = await s.ask(said)
    finally:
        await s.close()
    return calls, reply, time.perf_counter() - t0


async def main(n: int) -> int:
    cfg = load()
    cfg.logging.tool_calls = str(Path(tempfile.gettempdir()) / "winston-computer-trials.jsonl")   # not data/logs
    pc, specs = computer(cfg)
    if pc is None:
        print("No switched-on MCP server has a security_updates tool."); return 1
    print(f"computer: {pc}")
    misses = 0
    for said, want, holds in CASES:
        hits = 0
        for _ in range(n):
            calls, reply, secs = await trial(cfg, specs, said)
            ok = any(tool == want and (holds is None or holds in json.dumps(args).lower()) for tool, args in calls)
            hits += ok
            print(f"  {'ok ' if ok else 'MISS'} {secs:4.1f} s  {said!r}: {calls}  -> {reply[:110]!r}")
        misses += hits < n
        print(f"{hits}/{n}  {said}")
    return 1 if misses else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    sys.exit(asyncio.run(main(ap.parse_args().n)))
