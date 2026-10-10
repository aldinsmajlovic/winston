"""The settings of one computer, in a file on that computer: which folders files open from, which logs
may be read, where it listens over HTTP and who may connect. And its token, of which only the hash is
kept. `WINSTON_COMPUTER_CONFIG` names another folder (tests use it)."""
from __future__ import annotations

import hashlib
import ipaddress
import os
import secrets
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

TEMPLATE = """\
# winston-computer: what Winston may reach on this computer. Read when the server starts.

# Files open from these folders and below, nowhere else. Empty: no files.
folders = []

# Where it listens for HTTP, with its token. Leave it out to serve stdio only.
listen = "127.0.0.1:8711"

# Who may connect over HTTP: addresses or networks ("192.168.1.20", "192.168.1.0/24").
# Empty: anyone with the token.
allow_from = []

# Logs it may read, each with a name you choose; Winston sees only the names.
#   backup = { journal = "restic-backup.service" }    a system unit (your user must be allowed to read the journal)
#   sync = { user_journal = "syncthing.service" }      one of your own user units
#   web = { file = "/var/log/nginx/error.log" }         a file
[logs]
"""
LOG_KINDS = ("journal", "user_journal", "file")


def config_dir() -> Path:
    if env := os.environ.get("WINSTON_COMPUTER_CONFIG"):
        return Path(env)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "winston-computer"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "winston-computer"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "winston-computer"


def token_path() -> Path:
    return config_dir() / "token.sha256"


class ConfigError(ValueError):
    """The settings file says something it cannot do; the message names the setting."""


@dataclass
class Config:
    folders: list[Path] = field(default_factory=list)
    listen: tuple[str, int] | None = None
    allow_from: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = field(default_factory=list)
    logs: dict[str, tuple[str, str]] = field(default_factory=dict)     # name -> (kind, unit or path)

    def allowed(self, address: str) -> bool:
        if not self.allow_from:
            return True
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(ip in net for net in self.allow_from)


def parse(text: str) -> Config:
    raw = tomllib.loads(text)
    cfg = Config(folders=[Path(f).expanduser() for f in raw.get("folders", [])])
    if listen := raw.get("listen"):
        host, _, port = str(listen).rpartition(":")
        if not host or not port.isdigit():
            raise ConfigError(f'listen: "{listen}" is not host:port')
        cfg.listen = (host.strip("[]"), int(port))
    for item in raw.get("allow_from", []):
        try:
            cfg.allow_from.append(ipaddress.ip_network(item, strict=False))
        except ValueError as e:
            raise ConfigError(f"allow_from: {e}") from None
    for name, spec in (raw.get("logs") or {}).items():
        kinds = [k for k in LOG_KINDS if k in (spec if isinstance(spec, dict) else {})]
        if len(kinds) != 1:
            raise ConfigError(f"logs.{name}: give one of {', '.join(LOG_KINDS)}")
        cfg.logs[name] = (kinds[0], str(spec[kinds[0]]))
    return cfg


def load() -> Config:
    path = config_dir() / "config.toml"
    if not path.is_file():
        raise ConfigError(f"no settings at {path}; run `winston-computer init`")
    try:
        return parse(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None


def init() -> Path:
    """Writes the settings file with nothing allowed, unless one is there."""
    path = config_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(TEMPLATE, encoding="utf-8")
    return path


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    """A new token, replacing the old; only its hash is written, readable by you alone."""
    token = secrets.token_urlsafe(32)
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(_hash(token) + "\n", encoding="utf-8")
    return token


def token_hash() -> str | None:
    path = token_path()
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def token_ok(presented: str, stored: str | None) -> bool:
    return bool(stored) and secrets.compare_digest(_hash(presented), stored)
