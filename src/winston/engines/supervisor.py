"""The supervisor owns every process Winston depends on: engines from config plus the channel
processes (voice, Discord). Start, stop, health, status, and adopting a changed config. The API and the CLI talk
to this, never to processes."""
from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
from dotenv import dotenv_values
from loguru import logger

from winston.config import data_dir
from winston.config.schema import EngineConfig, WinstonConfig
from winston.engines import install
from winston.engines.drivers import (DockerDriver, Driver, ExternalDriver, SubprocessDriver, discord_channel_cmd,
                                     satellite_channel_cmd, voice_channel_cmd)


@dataclass
class Managed:
    name: str
    cfg: EngineConfig
    driver: Driver
    kind: str = "engine"            # engine | channel
    state: str = "stopped"          # stopped | installing | starting | healthy | unhealthy | error
    detail: str = ""
    since: float = field(default_factory=time.time)
    started_in: float | None = None     # seconds the last successful start took
    owned: bool = False                 # this supervisor started it, so it may stop it on exit
    pending: Managed | None = None      # built from a newer config; swapped in on the next stop
    stale: bool = False                 # runs with settings or secrets saved since it started; a restart applies them
    stale_base: dict | None = None      # the saved config it runs with, while stale from a setting (None: a secret)
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
DOWNLOAD_GRACE_SECS = 120   # a start whose download grew in the last check waits at least this much longer


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
        self.on_healthy: Callable[[str], object] | None = None     # told the name of each one that comes up

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
            # port 0 is "no port": constructed, since a configured engine's port must be 1 or more
            dcfg = EngineConfig.model_construct(driver="subprocess", port=0, health="", startup_timeout=60)
            d = cfg.channels.discord            # the checks discord.py makes on start
            items["discord"] = Managed("discord", dcfg, SubprocessDriver("discord", dcfg, discord_channel_cmd()),
                                       kind="channel",
                                       needs_env=[d.token_env] + ([] if d.allowed_user else [d.allowed_user_env]))
        s = cfg.channels.satellite
        if s.enabled and s.devices:
            scfg = EngineConfig(driver="subprocess", port=s.http_port, health="/health", startup_timeout=60)
            items["satellite"] = Managed("satellite", scfg, SubprocessDriver("satellite", scfg, satellite_channel_cmd()),
                                         kind="channel", needs_env=[x.key_env for x in s.devices.values() if x.key_env])
        return items

    async def reload(self, cfg: WinstonConfig) -> list[str]:
        """Adopt a new config. A running engine whose config changed keeps its process and takes
        the new config on its next stop or restart; returns the names of those. One the config no
        longer has (removed, switched off, a channel with nothing to serve) is stopped and dropped."""
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
                old.detail = "config changed, restart to apply"
                waiting.append(name)
            else:
                old.pending = None
        gone = [name for name in self.items if name not in fresh]
        for name in gone:
            self.items[name].pending = None
        await asyncio.gather(*(self.stop(name) for name in gone if self.items[name].state != "stopped"))
        for name in gone:
            del self.items[name]
        return waiting

    def mark_stale(self, names, base: dict | None = None) -> list[str]:
        """Processes that run and now miss a saved change (a setting they read at start, a secret);
        returns their names. A stopped one reads the new config when it starts. `base` is the saved
        config (dumped) before a settings change, so undoing the change can clear the mark again
        (settle); a secret passes none, and only a restart clears it."""
        out = []
        for name in names:
            m = self.items.get(name)
            if m and m.state in ("starting", "healthy", "unhealthy"):
                m.stale_base = base if not m.stale else (m.stale_base if base is not None else None)
                m.stale = True
                if not m.pending:
                    m.detail = "config changed, restart to apply"
                out.append(name)
        return out

    def settle(self, restarts) -> None:
        """Clear the stale mark of processes whose settings are back to what they run with: `restarts`
        maps an older saved config to the processes that would restart to go from it to the new one."""
        for m in self.items.values():
            if m.stale and m.stale_base is not None and m.name not in restarts(m.stale_base):
                m.stale, m.stale_base = False, None
                if not m.pending:
                    m.detail = ""

    def _set(self, m: Managed, state: str, detail: str = ""):
        if state != m.state:
            m.since = time.time()
            if state == "healthy" and self.on_healthy:
                self.on_healthy(m.name)
        m.state, m.detail = state, detail

    async def healthy(self, m: Managed) -> bool:
        if not m.cfg.health:                    # no health path: up whenever the process is (hosted APIs)
            return await m.driver.running()
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                return (await c.get(m.health_url)).status_code == 200
        except Exception:  # noqa: BLE001
            return False

    async def refresh(self) -> list[Managed]:
        for m in self.items.values():
            if m.state in ("installing", "starting"):
                continue
            keep = m.detail if m.pending or m.stale else ""
            if await self.healthy(m):
                self._set(m, "healthy", keep)
            elif await m.driver.running():
                self._set(m, "unhealthy", keep or "process up, health check failing")
            elif m.state == "error":            # kept until it is started or stopped again
                continue
            elif m.expected and _exit_code(m.driver) is not None:
                m.expected, m.stale = False, False       # gone: the next start reads the new config
                self._set(m, "error", _exited(m)); logger.error(f"{m.name}: {m.detail}")
            else:
                m.stale = False
                self._set(m, "stopped", keep if m.pending else "")
        return list(self.items.values())

    async def start(self, name: str, wait: bool = True) -> Managed:
        m = self.items[name]
        stale = getattr(m.driver, "stale", None)
        if stale and await stale():             # healthy is not enough: it must be the configured engine
            logger.warning(f"{name}: running from a different config, replacing it")
        elif await self.healthy(m):
            self._set(m, "healthy"); return m
        m.stale = False                         # a fresh start reads the saved config and data/.env
        if missing := _missing_env(m.needs_env):
            d = self.cfg.channels.discord       # an empty user ID reads an older setup's variable, never drawn
            if name == "discord" and not d.allowed_user and _missing_env([d.allowed_user_env]):
                rest = _missing_env([n for n in m.needs_env if n != d.allowed_user_env])
                unset = "Your Discord user ID is not set: add it on Ways to talk, Discord"
                missing = f"{rest}; {unset}" if rest else unset
            m.expected = False
            self._set(m, "error", missing); logger.error(f"{name}: not started, {missing}"); return m
        if m.kind == "engine" and m.cfg.driver != "external":
            needs = await install.parts(name, self.cfg)
            if any(not p.installed for p in needs):
                self._set(m, "installing")
                why = await install.ensure(needs, lambda text: m.state == "installing" and self._set(m, "installing", text))
                if m.state != "installing":         # stopped meanwhile; the install itself goes on
                    return m
                if why:
                    self._set(m, "error", f"not installed: {why}"); logger.error(f"{name}: not installed, {why}")
                    return m
        self._set(m, "starting")
        m.owned = True
        m.log_start = _log_size(m.driver)
        try:
            await m.driver.start()
        except Exception as e:  # noqa: BLE001
            self._set(m, "error", str(e)); logger.error(f"{name}: {e}"); return m
        m.expected = True
        if not wait:
            return m
        t0 = time.time()
        if not m.cfg.health and m.driver.log_path():     # health-less process: up means it survived startup
            await asyncio.sleep(GRACE_SECS)
        # A first start downloads its model into the Hugging Face cache: while that grows it is not stuck.
        from winston.api.options import hf_cache      # lazy: the API's module, as the Docker driver's
        hub = None if m.cfg.driver == "external" else hf_cache(m.cfg.model_dump())
        first = seen = await asyncio.to_thread(install.hub_bytes, hub)
        deadline = t0 + m.cfg.startup_timeout
        while time.time() < deadline:
            if await self.healthy(m):
                took = time.time() - t0
                if seen == first:                   # a start that downloaded is not the usual restart time
                    m.started_in = took
                self._set(m, "healthy"); logger.info(f"{name}: healthy after {took:.0f} s"); return m
            if not await m.driver.running():
                m.expected = False
                detail = _exited(m) if _exit_code(m.driver) is not None else "exited during startup; see docker logs"
                self._set(m, "error", detail); logger.error(f"{name}: {detail}")
                return m
            await asyncio.sleep(3)
            if hub and (now := await asyncio.to_thread(install.hub_bytes, hub)) > seen:
                seen, deadline = now, max(deadline, time.time() + DOWNLOAD_GRACE_SECS)
                m.detail = f"downloading, {install.gb(now - first)} so far"
        hint = ("; nothing answered at its Address, check that it runs" if m.cfg.driver == "external"
                else f"; its download stopped at {install.gb(seen - first)}" if seen > first
                else "; raise Startup time limit under How it runs if it is slow to load" if m.kind == "engine" else "")
        self._set(m, "error", f"not ready after {m.cfg.startup_timeout} s{hint}")
        return m

    async def stop(self, name: str) -> Managed:
        m = self.items[name]
        m.expected, m.stale = False, False
        await m.driver.stop()
        self._set(m, "stopped")
        if m.pending:
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
