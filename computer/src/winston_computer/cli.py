"""winston-computer: init, token, serve, call, service install | remove."""
import argparse
import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path

from winston_computer import config

UNIT = """\
[Unit]
Description=winston-computer: Winston's tools on this computer
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart={python} -m winston_computer serve
Restart=on-failure
RestartSec=5
# What it opens keeps running when it stops or restarts.
KillMode=process

[Install]
WantedBy=graphical-session.target
"""


def _unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "winston-computer.service"


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, check=False)


def service(action: str) -> None:
    if not sys.platform.startswith("linux") or not shutil.which("systemctl"):
        raise SystemExit("service install is built for Linux with systemd only, so far; run "
                         "`winston-computer serve` from your session's autostart instead.")
    unit = _unit_path()
    if action == "remove":
        _systemctl("disable", "--now", unit.name)
        unit.unlink(missing_ok=True)
        _systemctl("daemon-reload")
        print("Removed: it no longer starts with your session.")
        return
    config.load()                                       # a bad settings file fails here, not in the unit
    if not config.token_hash():
        raise SystemExit("No token yet: run `winston-computer token` first.")
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(UNIT.format(python=sys.executable), encoding="utf-8")
    _systemctl("daemon-reload")
    r = _systemctl("enable", "--now", unit.name)
    if r.returncode:
        raise SystemExit(r.stderr.strip())
    print(f"Installed {unit} and started it; it starts with your desktop session.")
    if _systemctl("is-active", "graphical-session.target").stdout.strip() != "active":
        print("Your session does not start graphical-session.target, so it will not start at login by itself: "
              "add `winston-computer serve` to your desktop's autostart instead.")
    env = _systemctl("show-environment").stdout
    if "WAYLAND_DISPLAY=" not in env and "DISPLAY=" not in env:
        print("systemd does not know your session's display, so apps cannot open from the service. Most desktops "
              "pass it; on a bare compositor add this to its autostart:\n"
              "  dbus-update-activation-environment --systemd WAYLAND_DISPLAY DISPLAY XDG_CURRENT_DESKTOP")


async def call(tool: str, args: dict) -> None:
    from mcp.client.client import Client

    from winston_computer.server import build
    async with Client(build(config.load())) as c:
        r = await c.call_tool(tool, args)
    text = "\n".join(getattr(x, "text", "") for x in r.content)
    print(text)
    if r.is_error:
        raise SystemExit(1)


def main() -> None:
    ap = argparse.ArgumentParser(prog="winston-computer", description="Winston's tools on this computer, over MCP.")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="write the settings file, with nothing allowed")
    sub.add_parser("token", help="make a new token (replacing the old) and print it once")
    s = sub.add_parser("serve", help="serve over HTTP as the settings say, or over stdio")
    s.add_argument("--stdio", action="store_true", help="stdio even when the settings name an address")
    c = sub.add_parser("call", help="run one tool here, as Winston would, and print its answer")
    c.add_argument("tool")
    c.add_argument("args", nargs="?", default="{}", help='its arguments as JSON: \'{"app": "Firefox"}\'')
    v = sub.add_parser("service", help="start it with your desktop session (systemd), or stop that")
    v.add_argument("action", choices=["install", "remove"])
    a = ap.parse_args()
    try:
        if a.command == "init":
            print(f"Settings: {config.init()}  (nothing is allowed until you add it there)")
        elif a.command == "token":
            print(config.new_token())
            print("Give this to Winston (the MCP server's Token field); it is not kept here and is shown once.",
                  file=sys.stderr)
        elif a.command == "serve":
            from winston_computer.server import serve
            serve(config.load(), stdio=a.stdio)
        elif a.command == "call":
            asyncio.run(call(a.tool, json.loads(a.args)))
        elif a.command == "service":
            service(a.action)
    except config.ConfigError as e:
        raise SystemExit(str(e)) from None
