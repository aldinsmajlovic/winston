"""Lockdown: while on, actions asked in guarded conversations wait for the owner's phone.

The switch is the file data/lockdown.json ({"since", "by"}), read on every call so every process
(winstond, the voice subprocess, the CLI) sees the same state. It fails closed: a file that exists
but cannot be read counts as on, and turn_off still removes it. Each change is appended to
logs/lockdown.jsonl ({"ts", "on", "by"}), which windows() reads back.
"""
import json
import os
import time

from winston.config import resolve

GUARDED = ("voice", "satellite", "assist")
PASS = frozenset({"start_over", "mark_mistake", "lockdown"})  # tools that run whatever the state
WAITING = "needs your confirmation first, on your phone, within five minutes. Nothing has happened yet."
UNAVAILABLE = "Not available right now. Nothing has happened."


def state() -> dict | None:
    path = resolve("lockdown.json")
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except (OSError, ValueError):       # ValueError: bytes that are not text
        return {"since": None, "by": "unknown"}
    try:
        d = json.loads(text)
        return {"since": float(d["since"]), "by": str(d["by"])}
    except (ValueError, TypeError, KeyError):
        return {"since": None, "by": "unknown"}


def on() -> bool:
    return state() is not None


def _log(ts: float, is_on: bool, by: str):
    log = resolve("logs/lockdown.jsonl")
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(json.dumps({"ts": ts, "on": is_on, "by": by}) + "\n")


def turn_on(by: str) -> bool:
    if on():
        return False
    path = resolve("lockdown.json")
    now = time.time()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"since": now, "by": by}))
    os.replace(tmp, path)
    _log(now, True, by)
    return True


def turn_off(by: str) -> bool:
    if not on():
        return False
    resolve("lockdown.json").unlink(missing_ok=True)
    _log(time.time(), False, by)
    return True


def guarded(place: str, in_room: bool) -> bool:
    return place in GUARDED or in_room


def windows(start: float, end: float) -> list[tuple[float, float]]:
    """The on periods overlapping [start, end], from the log; one still open ends at end."""
    try:
        lines = resolve("logs/lockdown.jsonl").read_text().splitlines()
    except OSError:
        return []
    out, opened = [], None
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e["on"] and opened is None:
            opened = e["ts"]
        elif not e["on"] and opened is not None:
            out.append((opened, e["ts"]))
            opened = None
    if opened is not None:
        out.append((opened, max(end, opened)))
    return [(a, min(b, end)) for a, b in out if a <= end and b >= start]
