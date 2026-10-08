"""What the checks in tests/ share: the two ways of reporting cases, a free port, waiting for a condition,
a throwaway data directory and winstond, and voice turns through tests/m2_headless_roundtrip.py.

Importing it imports nothing of Winston's, so a check can set WINSTON_DATA (scratch_data) before it does."""
import asyncio
import os
import re
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent

# expect(): every case printed as it runs, failures counted; finish() prints the count and exits with it.
failed = 0
detail_on_pass = True       # False prints a case's detail only when it fails


def expect(name: str, ok: bool, detail=""):
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name} {detail if detail_on_pass or not ok else ''}")


def finish():
    print(f"\n{'all passed' if not failed else f'{failed} failed'}")
    sys.exit(1 if failed else 0)


# check(): only mismatches are kept; finish_checks() prints them and exits 1, or prints the ok line.
mismatches: list[str] = []


def check(name, got, want):
    if got != want:
        mismatches.append(f"{name}: got {got!r}, want {want!r}")


def finish_checks(ok_line: str):
    if mismatches:
        print("\n".join(mismatches)); sys.exit(1)
    print(ok_line)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def until(pred, timeout: float, every: float = 0.1) -> bool:
    """Awaits pred() every `every` seconds until it is true (True) or timeout seconds pass (False)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if await pred():
            return True
        await asyncio.sleep(every)
    return False


def scratch_data(prefix: str) -> Path:
    """A throwaway data directory, set as WINSTON_DATA. Call it before importing winston."""
    data = Path(tempfile.mkdtemp(prefix=prefix))
    os.environ["WINSTON_DATA"] = str(data)
    return data


def winstond_data(data: Path, cfg: dict, persona: Path = REPO / "examples" / "persona.md",
                  fixtures: tuple[str, ...] = (), env: str = ""):
    """A throwaway winstond's data directory: cfg as config.yaml, the persona, examples/memory, these
    extensions from tests/fixtures/extensions, and a .env (owner-only) with the fake Home Assistant's
    token and env's lines."""
    (data / "config.yaml").write_text(yaml.safe_dump(cfg))
    shutil.copy(persona, data / "persona.md")
    shutil.copytree(REPO / "examples" / "memory", data / "memory", dirs_exist_ok=True)
    if fixtures:
        (data / "extensions").mkdir(parents=True, exist_ok=True)
    for name in fixtures:
        shutil.copy(REPO / "tests" / "fixtures" / "extensions" / name, data / "extensions" / name)
    (data / ".env").write_text('HA_TOKEN="fake-ha-token"\n' + env)
    os.chmod(data / ".env", 0o600)


def real_engines_data(data: Path, real: Path, api_port: int, ha_port: int, extensions: dict,
                      fixtures: tuple[str, ...], env: str = ""):
    """winstond_data for a throwaway winstond on the real engines: real's config.yaml with every channel
    and announcements off, Home Assistant pointed at the fake and only these extensions, and real's
    persona (his real instructions, so prompts are real-sized). real is only read."""
    cfg = yaml.safe_load((real / "config.yaml").read_text())
    for c in ("voice", "satellite", "discord"):
        cfg.setdefault("channels", {}).setdefault(c, {})["enabled"] = False
    cfg["announce"] = {"enabled": False}
    ha = cfg.setdefault("providers", {}).setdefault("home_assistant", {})
    ha.update({"url": f"http://127.0.0.1:{ha_port}", "token_env": "HA_TOKEN", "expose": {"light.taklampa": {}}})
    cfg["control"] = {**cfg.get("control", {}), "port": api_port, "host": "127.0.0.1"}
    cfg["extensions"] = extensions
    cfg.setdefault("logging", {})
    winstond_data(data, cfg, real / "persona.md", fixtures, env)


async def serve(app, port: int):
    """Serves app on 127.0.0.1:port in this process. Returns once it is up (or 20 s on), with the
    coroutine function that stops it."""
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    await until(lambda: asyncio.sleep(0, result=server.started), 20)

    async def stop():
        server.should_exit = True
        await serving
    return stop


async def voice_turns(label: str, questions: list[str]) -> list[float]:
    """The questions spoken through tests/m2_headless_roundtrip.py, a turn each; prints every turn and
    returns the times from end of speech to first audio, in ms."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(REPO / "tests" / "m2_headless_roundtrip.py"), *questions,
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
