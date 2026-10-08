"""A throwaway winstond with the docs' example extensions, the built-in ones (News reads the public feeds
of its presets), an MCP server and the fake Home Assistant (its made-up home), for looking at the
Extensions page and taking its screenshots. Never touches data/ or the real Home Assistant. Prints the
URL; Ctrl-C stops it.

    uv run python tests/extensions_demo.py [--port 8790]
"""
import argparse
import asyncio
import re
import shutil
import sys

from harness import REPO, free_port, scratch_data, winstond_data

DATA = scratch_data("winston-extensions-demo-")

import uvicorn  # noqa: E402
from fake_ha import FakeHA  # noqa: E402


def setup(api_port: int, ha_port: int):
    cfg = {"llm": {"model": "none"},
           "channels": {"voice": {"enabled": False}, "satellite": {"enabled": False}, "discord": {"enabled": False}},
           "announce": {"enabled": False},
           "providers": {"home_assistant": {"url": f"http://127.0.0.1:{ha_port}", "token_env": "HA_TOKEN",
                                            "expose": {"light.main_room": {}}}},
           "control": {"port": api_port},
           "speak_up": {"home": "person.someone"},
           "extensions": {
               "plants": {"enabled": True, "settings": {"sensor": "sensor.ficus_moisture"}},
               "remind": {"enabled": True},
               "coin": {"enabled": True, "tools": {"flip": {"offer": True}}},
               "news": {"enabled": True, "settings": {"topics": ["energy prices", "AI"]}},
               "reminders": {"enabled": True},
               "echo": {"enabled": True, "mcp": {"command": [sys.executable, str(REPO / "tests/fixtures/mcp_echo.py")]},
                        "tools": {"echo": {}}}}}
    winstond_data(DATA, cfg)
    (DATA / "extensions").mkdir()
    doc = (REPO / "docs" / "extensions.md").read_text()
    builtin = {p.stem for p in (REPO / "src" / "winston" / "extensions").glob("*.py")}
    for block in re.findall(r"```python\n(# /// script.*?)```", doc, re.S):
        ident = re.search(r'^# id = "(\w+)"', block, re.M).group(1)
        if ident not in builtin:                        # the docs' trimmed Hevy is the built-in's example
            (DATA / "extensions" / f"{ident}.py").write_text(block)


async def main(port: int):
    ha_port = free_port()
    setup(port, ha_port)
    from winston.api.app import create_app
    from winston.config import load
    async with FakeHA(ha_port, seed="home"):
        app = create_app(load(), autostart=True)
        print(f"winstond with made-up extensions: http://127.0.0.1:{port}/extensions  (data {DATA})", flush=True)
        await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")).serve()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    try:
        asyncio.run(main(ap.parse_args().port))
    finally:
        shutil.rmtree(DATA, ignore_errors=True)
