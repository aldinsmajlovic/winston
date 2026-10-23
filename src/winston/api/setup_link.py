"""Setup mode (docs/setup.md, The first run). From `winston setup` making a new data directory until the
wizard's Done, winstond listens on the network and serves another device only with the setup link's key: in
its `?key=`, then in the cookie that sets. The key is kept only as its hash, in data/state/setup.json, with
the wizard's steps done or skipped, which stay after setup ends for Set up next."""
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime
from pathlib import Path

from winston.config import data_dir

STEPS = ("brain", "home_assistant")   # the wizard's steps before Done, in order; the Setup track adds the rest
COOKIE = "winston_setup"
ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"     # read aloud or typed from a screen: no 0, 1, i, l or o

_cache: tuple[int, dict] | None = None


def path() -> Path:
    return data_dir() / "state/setup.json"


def state() -> dict:
    """The file as it is now (read again only when it changed: `winston setup link` writes it beside winstond)."""
    global _cache
    try:
        mtime = path().stat().st_mtime_ns
    except OSError:
        return {}
    if _cache is None or _cache[0] != mtime:
        try:
            _cache = (mtime, json.loads(path().read_text()))
        except (OSError, ValueError):
            _cache = (mtime, {})
    return _cache[1]


def _write(s: dict) -> dict:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    return s


def active() -> bool:
    s = state()
    return bool(s.get("key_sha256")) and not s.get("finished")


def holding() -> bool:
    """Whether winstond leaves his engines stopped when it starts: during setup, until Brain and voice is done,
    since starting one downloads what it lacks, and the wizard shows the picks and their sizes first."""
    return active() and (state().get("steps") or {}).get("brain") != "done"


def _hash(key: str) -> str:
    return hashlib.sha256(key.strip().lower().encode()).hexdigest()


def new_key() -> str:
    """A new setup key, which replaces the one before; setup mode starts if it had not."""
    key = "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3))
    _write({**state(), "key_sha256": _hash(key)})
    return key


def key_of(request) -> str | None:
    """The setup key a request carries, from the link's ?key= or the cookie it set, if it is the current one
    and setup is not done."""
    s = state()
    if not s.get("key_sha256") or s.get("finished"):
        return None
    for k in (request.query_params.get("key"), request.cookies.get(COOKIE)):
        if k and hmac.compare_digest(_hash(k), s["key_sha256"]):
            return k
    return None


def mark(step: str, how: str) -> dict:
    if step not in STEPS:
        raise KeyError(step)
    s = state()
    return _write({**s, "steps": {**(s.get("steps") or {}), step: how}})


def finish() -> dict:
    return _write({**state(), "finished": datetime.now().isoformat(timespec="seconds")})

