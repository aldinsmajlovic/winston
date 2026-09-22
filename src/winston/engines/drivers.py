"""Engine drivers: how a heavy process is started, stopped and observed. Docker for containers
(the LLM server), subprocess for local venv services (the TTS server, the voice channel),
external for something you run yourself and Winston only health-checks."""
import asyncio
import os
import sys
from pathlib import Path
from typing import Protocol

from loguru import logger

from winston.config import data_dir, repo_root
from winston.config.schema import EngineConfig


def _expand(s: str) -> str:
    return s.replace("{data}", str(data_dir())).replace("{repo}", str(repo_root()))


class Driver(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def running(self) -> bool: ...
    def log_path(self) -> Path | None: ...


class DockerDriver:
    def __init__(self, name: str, cfg: EngineConfig):
        import docker  # lazy: only needed when a docker engine exists
        self.name, self.cfg = f"winston-{name}", cfg
        self._client = docker.from_env()
        self._docker = docker

    def _get(self):
        try:
            return self._client.containers.get(self.name)
        except self._docker.errors.NotFound:
            return None

    async def running(self) -> bool:
        c = await asyncio.to_thread(self._get)
        return bool(c and c.status == "running")

    async def start(self) -> None:
        def _start():
            c = self._get()
            if c and c.status == "running":
                return
            if c:
                c.remove(force=True)
            kwargs = dict(
                name=self.name, command=[_expand(a) for a in self.cfg.args], detach=True,
                environment=self.cfg.env,
                ports={f"{self.cfg.port}/tcp": ("127.0.0.1", self.cfg.port)},
                volumes={str(Path(_expand(h)).expanduser()): {"bind": c_, "mode": "rw"}
                         for h, c_ in self.cfg.volumes.items()},
            )
            if self.cfg.ipc_host:
                kwargs["ipc_mode"] = "host"
            if self.cfg.runtime:
                kwargs["runtime"] = self.cfg.runtime
            elif self.cfg.gpu:
                kwargs["device_requests"] = [self._docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])]
            self._client.containers.run(self.cfg.image, **kwargs)
        await asyncio.to_thread(_start)

    async def stop(self) -> None:
        def _stop():
            c = self._get()
            if c:
                c.stop(timeout=30)
        await asyncio.to_thread(_stop)

    def log_path(self) -> Path | None:
        return None   # `docker logs winston-<name>`

    def logs(self, tail: int = 50) -> str:
        c = self._get()
        return c.logs(tail=tail).decode(errors="replace") if c else ""


class SubprocessDriver:
    def __init__(self, name: str, cfg: EngineConfig, cmd: list[str] | None = None):
        self.name, self.cfg = name, cfg
        self.cmd = [_expand(a) for a in (cmd or cfg.cmd)]
        self.cwd = Path(_expand(cfg.cwd)) if cfg.cwd else repo_root()
        self._proc: asyncio.subprocess.Process | None = None
        self._log = data_dir() / "logs" / f"{name}.log"

    async def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        if await self.running():
            return
        self._log.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, **self.cfg.env, "WINSTON_DATA": str(data_dir()), "PYTHONUNBUFFERED": "1"}
        logf = open(self._log, "ab")
        self._proc = await asyncio.create_subprocess_exec(*self.cmd, cwd=self.cwd, env=env, stdout=logf, stderr=logf)
        logger.info(f"{self.name}: started pid {self._proc.pid} ({' '.join(self.cmd)}) -> {self._log}")

    async def stop(self) -> None:
        if not await self.running():
            return
        self._proc.terminate()
        try:
            await asyncio.wait_for(self._proc.wait(), 15)
        except asyncio.TimeoutError:
            self._proc.kill()

    def log_path(self) -> Path | None:
        return self._log


class ExternalDriver:
    def __init__(self, name: str, cfg: EngineConfig):
        self.name, self.cfg = name, cfg

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def running(self) -> bool: return True
    def log_path(self) -> Path | None: return None


def voice_channel_cmd(host: str, port: int) -> list[str]:
    return [sys.executable, "-m", "winston.channels.voice", "--host", host, "--port", str(port)]
