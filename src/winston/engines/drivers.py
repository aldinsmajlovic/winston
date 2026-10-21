"""Engine drivers: how a heavy process is started, stopped and observed. Docker for containers
(the LLM server), subprocess for local venv services (the TTS server, the voice channel),
external for something you run yourself and Winston only health-checks."""
import asyncio
import hashlib
import json
import os
import sys
from collections import deque
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
    def tail(self, n: int) -> list[str]: ...


FINGERPRINT = "winston.config"      # container label: which engine config the container was started from


class DockerDriver:
    def __init__(self, name: str, cfg: EngineConfig):
        import docker  # lazy: only needed when a docker engine exists
        self.name, self.cfg = f"winston-{name}", cfg
        self._client = docker.from_env()
        self._docker = docker
        run = cfg.model_dump(include={"image", "args", "env", "volumes", "port", "runtime", "ipc_host", "gpu"})
        self.fingerprint = hashlib.sha256(json.dumps(run, sort_keys=True).encode()).hexdigest()[:16]

    def _get(self):
        try:
            return self._client.containers.get(self.name)
        except self._docker.errors.NotFound:
            return None

    async def running(self) -> bool:
        c = await asyncio.to_thread(self._get)
        return bool(c and c.status == "running")

    async def stale(self) -> bool:
        """A container runs under our name but from another config (an earlier winstond, a hand edit)."""
        c = await asyncio.to_thread(self._get)
        return bool(c and c.status == "running" and c.labels.get(FINGERPRINT) != self.fingerprint)

    async def start(self) -> None:
        def _start():
            c = self._get()
            if c and c.status == "running" and c.labels.get(FINGERPRINT) == self.fingerprint:
                return
            if c:
                c.remove(force=True)
            volumes = {str(Path(_expand(h)).expanduser()): c_ for h, c_ in self.cfg.volumes.items()}
            # Made as us first: a container runs as root and would make a missing host folder root's, and
            # a Hugging Face cache's hub/ with it, which engines on the host (a voice) then cannot write.
            from winston.api.options import hf_cache
            for host in volumes:
                if not Path(host).exists():
                    Path(host).mkdir(parents=True)
            hub = hf_cache({"volumes": volumes})
            if str(hub.parent) in volumes:              # from one of its volumes, not the HF_HOME fallback
                hub.mkdir(exist_ok=True)
            kwargs = {
                "name": self.name, "command": [_expand(a) for a in self.cfg.args], "detach": True,
                "labels": {FINGERPRINT: self.fingerprint},
                "environment": self.cfg.env,
                "ports": {f"{self.cfg.port}/tcp": ("127.0.0.1", self.cfg.port)},
                "volumes": {h: {"bind": c_, "mode": "rw"} for h, c_ in volumes.items()},
            }
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

    def tail(self, n: int) -> list[str]:
        """The last n lines of the container's log (blocking: call it in a thread)."""
        c = self._get()
        return c.logs(tail=n).decode(errors="replace").splitlines() if c else []


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
        with open(self._log, "ab") as logf:     # noqa: ASYNC230  (a local file; the process keeps its own copy)
            self._proc = await asyncio.create_subprocess_exec(*self.cmd, cwd=self.cwd, env=env, stdout=logf,
                                                              stderr=logf)
        logger.info(f"{self.name}: started pid {self._proc.pid} ({' '.join(self.cmd)}) -> {self._log}")

    async def stop(self) -> None:
        if not await self.running():
            return
        self._proc.terminate()
        try:
            await asyncio.wait_for(self._proc.wait(), 15)
        except TimeoutError:
            self._proc.kill()
            await self._proc.wait()   # reap it, or running() stays True and the next start() is a no-op

    def log_path(self) -> Path | None:
        return self._log

    def tail(self, n: int) -> list[str]:
        try:
            with open(self._log, encoding="utf-8", errors="replace") as f:
                return [ln.rstrip("\n") for ln in deque(f, maxlen=n)]
        except FileNotFoundError:
            return []

    def exit_code(self) -> int | None:
        """The return code once the process has exited, None while it runs or before it started."""
        return self._proc.returncode if self._proc is not None else None


class ExternalDriver:
    def __init__(self, name: str, cfg: EngineConfig):
        self.name, self.cfg = name, cfg

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def running(self) -> bool: return True
    def log_path(self) -> Path | None: return None
    def tail(self, n: int) -> list[str]: return []


def voice_channel_cmd(host: str, port: int) -> list[str]:
    return [sys.executable, "-m", "winston.channels.voice", "--host", host, "--port", str(port)]


def satellite_channel_cmd() -> list[str]:
    return [sys.executable, "-m", "winston.channels.satellite"]


def discord_channel_cmd() -> list[str]:
    return [sys.executable, "-m", "winston.channels.discord"]
