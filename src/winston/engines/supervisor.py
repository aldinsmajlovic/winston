"""The supervisor owns every process Winston depends on: engines from config plus the channel
processes (voice, Discord). Start, stop, health, status, and adopting a changed config. The API and the CLI talk
to this, never to processes."""
from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass, field

import httpx
from dotenv import dotenv_values
from loguru import logger

from winston.config import data_dir
from winston.config.schema import EngineConfig, WinstonConfig
from winston.engines.drivers import (DockerDriver, Driver, ExternalDriver, SubprocessDriver, discord_channel_cmd,
                                     satellite_channel_cmd, voice_channel_cmd)


@dataclass
class Managed:
    name: str
    cfg: EngineConfig
    driver: Driver
    kind: str = "engine"            # engine | channel
    state: str = "stopped"          # stopped | starting | healthy | unhealthy | error
    detail: str = ""
    since: float = field(default_factory=time.time)
    started_in: float | None = None     # seconds the last successful start took
    owned: bool = False                 # this supervisor started it, so it may stop it on exit
    pending: Managed | None = None      # built from a newer config; swapped in on the next stop
    retired: bool = False               # gone from config; dropped on the next stop
    needs_env: list[str] = field(default_factory=list)  # variables it cannot start without (data/.env)
    expected: bool = False              # started and not stopped on purpose: an exit is a crash
    log_start: int = 0                  # size of its log when last started: older lines are another run's

    @property
    def health_url(self) -> str:
        base = self.cfg.url.rstrip("/") if self.cfg.url else f"http://127.0.0.1:{self.cfg.port}"
        return base + self.cfg.health

    def same_as(self, other: Managed) -> bool:
        return (self.cfg == other.cfg and self.needs_env == other.needs_env
                and getattr(self.driver, "cmd", None) == getattr(other.driver, "cmd", None))


_SETTING = re.compile(r"\{([a-z_]+(?:\.[a-z_]+)+)\}")      # {tts.streaming.chunk_secs}


def _setting(cfg: WinstonConfig, path: str) -> str:
    v = cfg
    for part in path.split("."):
        if not hasattr(v, part):
            logger.warning(f"engine command names {{{path}}}, which is not a config value; left as written")
            return "{" + path + "}"
        v = getattr(v, part)
    return str(v).lower() if isinstance(v, bool) else str(v)


GRACE_SECS = 2.0        # a process with no health check must live this long before it counts as up


def _missing_env(names: list[str]) -> str:
    """Which of names the channel would start without, as the detail to show, or "". Looked up the way the
    process itself will: winstond's environment first, then data/.env, which the process loads on start."""
    env = data_dir() / ".env"
    file = dotenv_values(env) if env.is_file() else {}
    value = {n: os.environ[n] if n in os.environ else file.get(n) for n in names}     # the file never overrides
    missing = [n for n in names if n not in os.environ and n not in file]
    empty = [n for n in names if n not in missing and not (value[n] or "").strip()]
    parts = ([f"{', '.join(missing)} missing from data/.env"] if missing else []) + \
            ([f"{', '.join(empty)} empty in data/.env"] if empty else [])
    return "; ".join(parts)


def _exit_code(driver: Driver) -> int | None:
    fn = getattr(driver, "exit_code", None)       # subprocesses only; docker and external have none
    return fn() if fn else None


def _log_size(driver: Driver) -> int:
    try:
        return os.path.getsize(driver.log_path())
    except (OSError, TypeError):
        return 0


def _last_line(driver: Driver, start: int = 0) -> str:
    """The last non-empty line this run wrote to its log (from byte start on): usually why it exited."""
    path = driver.log_path()
    try:
        with open(path, "rb") as f:
            f.seek(max(start, f.seek(0, 2) - 4096))
            lines = [ln.strip() for ln in f.read().decode(errors="replace").splitlines() if ln.strip()]
    except (OSError, TypeError):
        return ""
    return lines[-1][:300] if lines else ""


def _exited(m: Managed) -> str:
    code, line = _exit_code(m.driver), _last_line(m.driver, m.log_start)
    return f"exited with code {code}" + (f": {line}" if line else "")


def _expand_model(e: EngineConfig, cfg: WinstonConfig) -> EngineConfig:
    """"{model}" in an engine's args or cmd stands for llm.model, so the model name lives in one place;
    "{section.field}" stands for any other config value, so a server's options are settings too."""
    def sub(xs: list[str]) -> list[str]:
        return [_SETTING.sub(lambda m: _setting(cfg, m.group(1)), x.replace("{model}", cfg.llm.model)) for x in xs]
    if any("{" in x for x in e.args + e.cmd):
        return e.model_copy(update={"args": sub(e.args), "cmd": sub(e.cmd)})
    return e


class Supervisor:
    def __init__(self, cfg: WinstonConfig):
        self.cfg = cfg
        self.items: dict[str, Managed] = self._build(cfg)

    @staticmethod
    def _build(cfg: WinstonConfig) -> dict[str, Managed]:
        items: dict[str, Managed] = {}
        for name, e in cfg.engines.items():
            if not e.enabled:
                continue
            e = _expand_model(e, cfg)
            driver = {"docker": DockerDriver, "subprocess": SubprocessDriver, "external": ExternalDriver}[e.driver](name, e)
            items[name] = Managed(name, e, driver)
        v = cfg.channels.voice
        if v.enabled:
            vcfg = EngineConfig(driver="subprocess", port=v.port, health="/client/", startup_timeout=120)
            items["voice"] = Managed("voice", vcfg, SubprocessDriver("voice", vcfg, voice_channel_cmd(v.host, v.port)),
                                     kind="channel")
        if cfg.channels.discord.enabled:            # no port and no health path: up while the process runs
            dcfg = EngineConfig(driver="subprocess", port=0, health="", startup_timeout=60)
            d = cfg.channels.discord            # the checks discord.py makes on start
            items["discord"] = Managed("discord", dcfg, SubprocessDriver("discord", dcfg, discord_channel_cmd()),
                                       kind="channel", needs_env=[d.token_env, d.allowed_user_env])
        s = cfg.channels.satellite
        if s.enabled and s.devices:
            scfg = EngineConfig(driver="subprocess", port=s.http_port, health="/health", startup_timeout=60)
            items["satellite"] = Managed("satellite", scfg, SubprocessDriver("satellite", scfg, satellite_channel_cmd()),
                                         kind="channel", needs_env=[x.key_env for x in s.devices.values() if x.key_env])
        return items

    async def reload(self, cfg: WinstonConfig) -> list[str]:
        """Adopt a new config. A running engine whose config changed keeps its process and takes
        the new config on its next stop or restart; returns the names of those."""
        self.cfg = cfg
        await self.refresh()                # decide on live state, not the last poll
        fresh = self._build(cfg)
        waiting: list[str] = []
        for name, m in fresh.items():
            old = self.items.get(name)
            if old is None:
                self.items[name] = m
            elif old.state == "stopped" or (old.state == "error" and not await old.driver.running()):
                self.items[name] = m if not old.same_as(m) else old
            elif not old.same_as(m):
                old.pending = m
                old.retired = False
                old.detail = "config changed, restart to apply"
                waiting.append(name)
            else:
                old.pending = None
        for name, m in list(self.items.items()):
            if name not in fresh:
                if m.state == "stopped":
                    del self.items[name]
                else:
                    m.pending, m.retired, m.detail = None, True, "removed from config, stop to drop"
                    waiting.append(name)
        return waiting

    def _set(self, m: Managed, state: str, detail: str = ""):
        if state != m.state:
            m.since = time.time()
        m.state, m.detail = state, detail

    async def healthy(self, m: Managed) -> bool:
        if not m.cfg.health:                    # no health path: up whenever the process is (hosted APIs)
            return await m.driver.running()
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                return (await c.get(m.health_url)).status_code == 200
        except Exception:
            return False

    async def refresh(self) -> list[Managed]:
        for m in self.items.values():
            if m.state == "starting":
                continue
            keep = m.detail if m.pending or m.retired else ""
            if await self.healthy(m):
                self._set(m, "healthy", keep)
            elif await m.driver.running():
                self._set(m, "unhealthy", keep or "process up, health check failing")
            elif m.state == "error":            # kept until it is started or stopped again
                continue
            elif m.expected and _exit_code(m.driver) is not None:
                m.expected = False
                self._set(m, "error", _exited(m)); logger.error(f"{m.name}: {m.detail}")
            else:
                self._set(m, "stopped", keep)
        return list(self.items.values())

    async def start(self, name: str, wait: bool = True) -> Managed:
        m = self.items[name]
        stale = getattr(m.driver, "stale", None)
        if stale and await stale():             # healthy is not enough: it must be the configured engine
            logger.warning(f"{name}: running from a different config, replacing it")
        elif await self.healthy(m):
            self._set(m, "healthy"); return m
        if missing := _missing_env(m.needs_env):
            m.expected = False
            self._set(m, "error", missing); logger.error(f"{name}: not started, {missing}"); return m
        self._set(m, "starting")
        m.owned = True
        m.log_start = _log_size(m.driver)
        try:
            await m.driver.start()
        except Exception as e:
            self._set(m, "error", str(e)); logger.error(f"{name}: {e}"); return m
        m.expected = True
        if not wait:
            return m
        t0 = time.time()
        if not m.cfg.health and m.driver.log_path():     # health-less process: up means it survived startup
            await asyncio.sleep(GRACE_SECS)
        while time.time() - t0 < m.cfg.startup_timeout:
            if await self.healthy(m):
                m.started_in = time.time() - t0
                self._set(m, "healthy"); logger.info(f"{name}: healthy after {m.started_in:.0f} s"); return m
            if not await m.driver.running():
                m.expected = False
                detail = _exited(m) if _exit_code(m.driver) is not None else "exited during startup; see docker logs"
                self._set(m, "error", detail); logger.error(f"{name}: {detail}")
                return m
            await asyncio.sleep(3)
        self._set(m, "error", f"not healthy after {m.cfg.startup_timeout} s")
        return m

    async def stop(self, name: str) -> Managed:
        m = self.items[name]
        m.expected = False
        await m.driver.stop()
        self._set(m, "stopped")
        if m.retired:
            del self.items[name]
        elif m.pending:
            m.pending.started_in, m.pending.owned = m.started_in, m.owned
            self.items[name] = m = m.pending
        return m

    async def start_all(self) -> list[Managed]:
        engines = [n for n, m in self.items.items() if m.kind == "engine"]
        await asyncio.gather(*(self.start(n) for n in engines))          # engines in parallel
        if all(self.items[n].state == "healthy" for n in engines):
            channels = [n for n, m in self.items.items() if m.kind == "channel"]
            await asyncio.gather(*(self.start(n) for n in channels))
        return list(self.items.values())

    async def stop_all(self, owned_only: bool = False) -> None:
        """Everything, channel first. On shutdown only what this supervisor started: a second
        winstond (say `winston up --no-start`) must not take down engines it merely observed."""
        for name in reversed(list(self.items)):
            if not owned_only or self.items[name].owned:
                await self.stop(name)
