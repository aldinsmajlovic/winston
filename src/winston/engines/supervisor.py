"""The supervisor owns every process Winston depends on: engines from config plus the voice
channel. Start, stop, health, status. The API and the CLI talk to this, never to processes."""
import asyncio
import time
from dataclasses import dataclass, field

import httpx
from loguru import logger

from winston.config.schema import EngineConfig, WinstonConfig
from winston.engines.drivers import DockerDriver, Driver, ExternalDriver, SubprocessDriver, voice_channel_cmd


@dataclass
class Managed:
    name: str
    cfg: EngineConfig
    driver: Driver
    kind: str = "engine"            # engine | channel
    state: str = "stopped"          # stopped | starting | healthy | unhealthy | error
    detail: str = ""
    since: float = field(default_factory=time.time)

    @property
    def health_url(self) -> str:
        base = self.cfg.url.rstrip("/") if self.cfg.url else f"http://127.0.0.1:{self.cfg.port}"
        return base + self.cfg.health


class Supervisor:
    def __init__(self, cfg: WinstonConfig):
        self.cfg = cfg
        self.items: dict[str, Managed] = {}
        for name, e in cfg.engines.items():
            if not e.enabled:
                continue
            driver = {"docker": DockerDriver, "subprocess": SubprocessDriver, "external": ExternalDriver}[e.driver](name, e)
            self.items[name] = Managed(name, e, driver)
        v = cfg.channels.voice
        if v.enabled:
            vcfg = EngineConfig(driver="subprocess", port=v.port, health="/client/", startup_timeout=120)
            self.items["voice"] = Managed("voice", vcfg, SubprocessDriver("voice", vcfg, voice_channel_cmd(v.host, v.port)),
                                          kind="channel")

    def _set(self, m: Managed, state: str, detail: str = ""):
        if state != m.state:
            m.since = time.time()
        m.state, m.detail = state, detail

    async def healthy(self, m: Managed) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                return (await c.get(m.health_url)).status_code == 200
        except Exception:
            return False

    async def refresh(self) -> list[Managed]:
        for m in self.items.values():
            if m.state == "starting":
                continue
            if await self.healthy(m):
                self._set(m, "healthy")
            elif await m.driver.running():
                self._set(m, "unhealthy", "process up, health check failing")
            else:
                self._set(m, "stopped")
        return list(self.items.values())

    async def start(self, name: str, wait: bool = True) -> Managed:
        m = self.items[name]
        if await self.healthy(m):
            self._set(m, "healthy"); return m
        self._set(m, "starting")
        try:
            await m.driver.start()
        except Exception as e:
            self._set(m, "error", str(e)); logger.error(f"{name}: {e}"); return m
        if not wait:
            return m
        t0 = time.time()
        while time.time() - t0 < m.cfg.startup_timeout:
            if await self.healthy(m):
                self._set(m, "healthy"); logger.info(f"{name}: healthy after {time.time() - t0:.0f} s"); return m
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
        return m

    async def start_all(self) -> list[Managed]:
        engines = [n for n, m in self.items.items() if m.kind == "engine"]
        await asyncio.gather(*(self.start(n) for n in engines))          # engines in parallel
        if "voice" in self.items and all(self.items[n].state == "healthy" for n in engines):
            await self.start("voice")
        return list(self.items.values())

    async def stop_all(self) -> None:
        for name in reversed(list(self.items)):                            # channel first, engines after
            await self.stop(name)
