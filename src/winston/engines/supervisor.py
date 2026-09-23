"""The supervisor owns every process Winston depends on: engines from config plus the channel
processes (voice, Discord). Start, stop, health, status, and adopting a changed config. The API and the CLI talk
to this, never to processes."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx
from loguru import logger

from winston.config.schema import EngineConfig, WinstonConfig
from winston.engines.drivers import (DockerDriver, Driver, ExternalDriver, SubprocessDriver, discord_channel_cmd,
                                     voice_channel_cmd)


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

    @property
    def health_url(self) -> str:
        base = self.cfg.url.rstrip("/") if self.cfg.url else f"http://127.0.0.1:{self.cfg.port}"
        return base + self.cfg.health

    def same_as(self, other: Managed) -> bool:
        return self.cfg == other.cfg and getattr(self.driver, "cmd", None) == getattr(other.driver, "cmd", None)


def _expand_model(e: EngineConfig, cfg: WinstonConfig) -> EngineConfig:
    """"{model}" in an engine's args or cmd stands for llm.model, so the model name lives in one place."""
    sub = lambda xs: [x.replace("{model}", cfg.llm.model) for x in xs]   # noqa: E731
    if any("{model}" in x for x in e.args + e.cmd):
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
            items["discord"] = Managed("discord", dcfg, SubprocessDriver("discord", dcfg, discord_channel_cmd()),
                                       kind="channel")
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
            elif old.state == "stopped":
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
        self._set(m, "starting")
        m.owned = True
        try:
            await m.driver.start()
        except Exception as e:
            self._set(m, "error", str(e)); logger.error(f"{name}: {e}"); return m
        if not wait:
            return m
        t0 = time.time()
        while time.time() - t0 < m.cfg.startup_timeout:
            if await self.healthy(m):
                m.started_in = time.time() - t0
                self._set(m, "healthy"); logger.info(f"{name}: healthy after {m.started_in:.0f} s"); return m
            if not await m.driver.running():
                self._set(m, "error", f"exited during startup; see logs ({m.driver.log_path() or 'docker logs'})")
                return m
            await asyncio.sleep(3)
        self._set(m, "error", f"not healthy after {m.cfg.startup_timeout} s")
        return m

    async def stop(self, name: str) -> Managed:
        m = self.items[name]
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
