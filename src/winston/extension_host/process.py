"""One running extension: its own process, spoken to over MCP on stdio.

It starts with only what it needs in its environment (never winstond's keys), gets its settings and
the values of its own secret fields over the protocol, and is watched: a crash restarts it after 1, 5
and 30 s, five crashes in ten minutes stop it, past its memory limit it is stopped, and one that
stops answering is started again. Every reason lands on its card."""
from __future__ import annotations

import asyncio
import os
import signal
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx2
from loguru import logger
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from pydantic import BaseModel, ConfigDict
from winston_sdk.server import request

from winston.extension_host.sources import Source, base_env, command, state_dir

BACKOFF = (1, 5, 30)
CRASH_WINDOW, CRASH_LIMIT = 600, 5
WATCH_EVERY = 2.0
PING_EVERY, PING_MISSES = 10.0, 3


class _Bearer(httpx2.Auth):
    """An HTTP MCP server's token on every request, and a note of any 401, so the card can say the token
    was refused rather than MCP's "Server returned an error response"."""

    def __init__(self, token: str):
        self.token, self.refused = token, False

    def auth_flow(self, request):
        if self.token:
            request.headers["Authorization"] = f"Bearer {self.token}"
        response = yield request
        self.refused = self.refused or response.status_code == 401


class _Any(BaseModel):
    model_config = ConfigDict(extra="allow")


def proc_status(pid: int) -> tuple[bool, float]:
    """(alive, resident memory in MB) from /proc; a zombie counts as gone."""
    try:
        text = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return False, 0.0
    state, rss = "", 0.0
    for line in text.splitlines():
        if line.startswith("State:"):
            state = line.split()[1]
        elif line.startswith("VmRSS:"):
            rss = int(line.split()[1]) / 1024
    return state not in ("Z", "X"), rss


class ExtensionProcess:
    def __init__(self, src: Source, *, api: str, token: str, log_path: Path, memory_mb: Callable[[], int],
                 on_ready: Callable[[ExtensionProcess], Awaitable[None]], extra_env: dict[str, str] | None = None,
                 mcp_command: list[str] | None = None, mcp_url: str | None = None,
                 mcp_token: Callable[[], str] = lambda: ""):
        self.src = src
        self.id = src.id
        self.api, self.token = api, token
        self.log_path = log_path
        self.memory_mb = memory_mb
        self.on_ready = on_ready
        self.extra_env = extra_env or {}
        self.mcp_command, self.mcp_url, self.mcp_token = mcp_command, mcp_url, mcp_token
        self.state = "stopped"          # stopped | starting | running | restarting | failed
        self.detail = ""
        self.client: Client | None = None
        self.pid: int | None = None
        self.rss_mb = 0.0
        self.peak_mb = 0.0
        self.started_at: float | None = None
        self.crashes: deque[float] = deque()
        self.exits: deque[dict[str, Any]] = deque(maxlen=10)     # why it stopped, for the card
        self.ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._kill_reason: str | None = None
        self._task: asyncio.Task | None = None

    @property
    def is_mcp_server(self) -> bool:
        return self.src.origin == "mcp"

    # lifecycle

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self.crashes.clear()
        self._task = asyncio.create_task(self._supervise(), name=f"extension {self.id}")

    async def stop(self) -> None:
        self._stop.set()
        self._kill(signal.SIGTERM)
        if self._task:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), 10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._kill(signal.SIGKILL)
                self._task.cancel()
        self.state, self.detail = "stopped", ""
        self.client, self.pid = None, None
        self.ready.clear()

    async def restart(self, why: str = "") -> None:
        await self.stop()
        self.detail = why
        self.start()

    def _kill(self, sig: int) -> None:
        if self.pid:
            try:
                os.kill(self.pid, sig)
            except ProcessLookupError:
                pass

    async def _supervise(self) -> None:
        while not self._stop.is_set():
            self.state = "starting" if not self.crashes else "restarting"
            error = None
            try:
                await self._run_once()
            except Exception as e:                                      # noqa: BLE001  (its failure, not ours)
                error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            finally:
                self.client, self.pid = None, None
                self.ready.clear()
            if self._stop.is_set():
                break
            why = self._kill_reason or error or "its process ended"
            self._kill_reason = None
            now = time.monotonic()
            self.crashes.append(now)
            while self.crashes and now - self.crashes[0] > CRASH_WINDOW:
                self.crashes.popleft()
            logger.warning(f"extension {self.id}: {why}")
            self.exits.append({"at": time.time(), "why": why})
            if len(self.crashes) >= CRASH_LIMIT:
                self.state = "failed"
                self.detail = f"Stopped after {CRASH_LIMIT} crashes in ten minutes. The last: {why}"
                return
            wait = BACKOFF[min(len(self.crashes), len(BACKOFF)) - 1]
            self.state, self.detail = "restarting", f"{why}; starting again in {wait} s"
            try:
                await asyncio.wait_for(self._stop.wait(), wait)
            except asyncio.TimeoutError:
                pass

    def _params(self) -> StdioServerParameters:
        folder = state_dir(self.id)
        folder.mkdir(parents=True, exist_ok=True)
        env = {**base_env(), **self.extra_env}
        if self.is_mcp_server:
            cmd = self.mcp_command or []
            return StdioServerParameters(command=cmd[0], args=cmd[1:], env=env, cwd=str(folder))
        env |= {"WINSTON_API": self.api, "WINSTON_TOKEN": self.token, "WINSTON_STATE": str(folder)}
        cmd = command(self.src)
        return StdioServerParameters(command=cmd[0], args=[*cmd[1:], "run", self.src.target], env=env,
                                     cwd=str(folder))

    async def _run_once(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", buffering=1) as log:
            log.write(f"--- started {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            auth = _Bearer(self.mcp_token()) if self.mcp_url and self.is_mcp_server else None
            try:
                await self._session(log, auth)
            except Exception:
                if auth and auth.refused:
                    raise ConnectionError("it refused its token (401)" if auth.token else
                                          "it wants a token (401); paste one on its card") from None
                raise

    async def _session(self, log, auth: _Bearer | None) -> None:
        """Connected over stdio, or over HTTP with `auth`; returns when it stops or is stopped."""
        async with AsyncExitStack() as stack:
            if auth is not None:
                http = await stack.enter_async_context(create_mcp_http_client(auth=auth))
                transport = streamable_http_client(self.mcp_url, http_client=http)
            else:
                transport = stdio_client(self._params(), errlog=log)
            self.client = await stack.enter_async_context(Client(transport, read_timeout_seconds=None))
            if not self.is_mcp_server:
                info = await self.call("info", timeout=15)
                self.pid = int(info["pid"])
            self.started_at = time.time()
            await self.on_ready(self)
            self.state, self.detail = "running", ""
            self.ready.set()
            await self._watch()

    async def _watch(self) -> None:
        last_ping, misses = time.monotonic(), 0
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), WATCH_EVERY)
                return
            except asyncio.TimeoutError:
                pass
            if self.pid:
                alive, rss = proc_status(self.pid)
                self.rss_mb, self.peak_mb = rss, max(self.peak_mb, rss)
                if not alive:
                    return
                limit = self.memory_mb()
                if rss > limit:
                    self._kill_reason = f"used {rss:.0f} MB of memory, over its limit of {limit} MB"
                    self._kill(signal.SIGKILL)
                    return
            if time.monotonic() - last_ping >= PING_EVERY:
                last_ping = time.monotonic()
                try:
                    await asyncio.wait_for(self._alive(), 5)
                    misses = 0
                except Exception:                                       # noqa: BLE001
                    misses += 1
                    if misses >= PING_MISSES:
                        self._kill_reason = f"stopped answering for {PING_EVERY * PING_MISSES:.0f} s"
                        self._kill(signal.SIGKILL)
                        return

    async def _alive(self) -> None:
        """MCP 2026-07-28 has no ping: an SDK extension answers winston/info, an MCP server lists its tools."""
        if self.is_mcp_server:
            await self.client.list_tools()
        else:
            await self.call("info", timeout=5)

    # calls

    async def call(self, method: str, timeout: float = 30, **params: Any) -> dict[str, Any]:
        """A `winston/<method>` call; raises when it does not run or does not answer in time."""
        if self.client is None:
            raise ConnectionError(f"{self.id} is not running")
        result = await asyncio.wait_for(self.client.session.send_request(request(method, **params), _Any), timeout)
        return {k: v for k, v in result.model_dump().items() if k not in ("resultType", "_meta")}

    async def call_tool(self, name: str, args: dict[str, Any], timeout: float) -> Any:
        if self.client is None:
            raise ConnectionError(f"{self.id} is not running")
        return await asyncio.wait_for(self.client.call_tool(name, args), timeout)

    async def list_tools(self) -> list[Any]:
        if self.client is None:
            return []
        return list((await self.client.list_tools()).tools)
