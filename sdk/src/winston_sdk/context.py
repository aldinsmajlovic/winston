"""`ctx`: what every handler gets. Settings, the store and the clock are local; everything else
reaches winstond, Home Assistant or the network, and is awaited."""
from __future__ import annotations

import asyncio
import os
import sys
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel

from winston_sdk.errors import NotPermitted
from winston_sdk.nudge import Nudge, NudgeState
from winston_sdk.when import parse_when

if TYPE_CHECKING:
    from winston_sdk.runtime import Runtime


def local_zone() -> ZoneInfo:
    """The machine's zone by name, so arithmetic across daylight saving holds (a fixed offset from
    datetime.now().astimezone() would be an hour out half the year)."""
    name = os.environ.get("TZ", "").lstrip(":")
    if not name:
        try:
            link = os.readlink("/etc/localtime")
            name = link.split("zoneinfo/", 1)[1] if "zoneinfo/" in link else ""
        except OSError:
            name = ""
    if not name and Path("/etc/timezone").is_file():
        name = Path("/etc/timezone").read_text().strip()
    try:
        return ZoneInfo(name or "UTC")
    except Exception:                          # an odd TZ value should not stop an extension
        return ZoneInfo("UTC")


class Clock:
    def __init__(self, zone: ZoneInfo | None = None):
        self.zone = zone or local_zone()

    def now(self) -> datetime:
        return datetime.now(self.zone)


def _dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else value


@dataclass
class State:
    """A Home Assistant state."""
    entity_id: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    last_changed: datetime | None = None

    @classmethod
    def of(cls, data: dict | None) -> State | None:
        if not data:
            return None
        return cls(data["entity_id"], data["state"], data.get("attributes") or {}, _dt(data.get("last_changed")))


@dataclass
class Device:
    """A device in Home Assistant's registry, as far as the `devices` permission shows it. `service` is
    true for what Home Assistant lists as a service rather than hardware (an add-on, a cloud account)."""
    id: str
    name: str
    manufacturer: str | None = None
    model: str | None = None
    model_id: str | None = None
    sw_version: str | None = None
    hw_version: str | None = None
    area: str | None = None
    service: bool = False


@dataclass
class Fact:
    text: str
    topic: str
    noted: date | None = None
    until: date | None = None


@dataclass
class ToolInfo:
    name: str
    tier: str
    description: str = ""


@dataclass
class DiaryEntry:
    day: date
    text: str


@dataclass
class Timer:
    id: str
    job: str
    at: datetime
    data: Any = None
    _ctx: Ctx | None = field(default=None, repr=False)

    async def cancel(self) -> None:
        if self._ctx is not None:
            await self._ctx._call("timer.cancel", id=self.id)


class Log:
    def __init__(self, write: Callable[[str, str], None]):
        self._write = write

    def info(self, msg: str) -> None:
        self._write("info", str(msg))

    def warning(self, msg: str) -> None:
        self._write("warning", str(msg))

    def error(self, msg: str) -> None:
        self._write("error", str(msg))


class _HA:
    def __init__(self, ctx: Ctx):
        self._ctx = ctx

    async def state(self, entity_id: str) -> State | None:
        return State.of(await self._ctx._call("ha.state", entity_id=entity_id))

    async def states(self, domain: str) -> list[State]:
        return [State.of(s) for s in await self._ctx._call("ha.states", domain=domain)]

    async def history(self, entity_id: str, since: datetime | timedelta) -> list[State]:
        start = self._ctx.now() - since if isinstance(since, timedelta) else since
        return [State.of(s) for s in await self._ctx._call("ha.history", entity_id=entity_id,
                                                          since=start.isoformat())]

    async def query(self, domain: str, service: str, data: dict | None = None, target: dict | None = None) -> Any:
        return await self._ctx._call("ha.query", domain=domain, service=service, data=data or {},
                                     target=target or {})

    async def call(self, domain: str, service: str, data: dict | None = None, target: dict | None = None) -> Any:
        return await self._ctx._call("ha.call", domain=domain, service=service, data=data or {},
                                     target=target or {})


class _Memory:
    def __init__(self, ctx: Ctx):
        self._ctx = ctx

    async def facts(self, topic: str | None = None) -> list[Fact]:
        rows = await self._ctx._call("memory.facts", topic=topic)
        return [Fact(r["text"], r["topic"], date.fromisoformat(r["noted"]) if r.get("noted") else None,
                     date.fromisoformat(r["until"]) if r.get("until") else None) for r in rows]

    async def diary(self, days: int = 7) -> list[DiaryEntry]:
        """His diary entries of the last `days` days, newest first."""
        rows = await self._ctx._call("memory.diary", days=days)
        return [DiaryEntry(date.fromisoformat(r["day"]), r["text"]) for r in rows]

    async def remember(self, text: str, topic: str, until: date | str | None = None) -> str:
        return await self._ctx._call("memory.remember", text=text, topic=topic,
                                     until=until.isoformat() if isinstance(until, date) else until)


class _Tools:
    def __init__(self, ctx: Ctx):
        self._ctx = ctx

    async def list(self) -> list[ToolInfo]:
        """The tools the brain has from Home Assistant and the other extensions, by the names it sees."""
        return [ToolInfo(t["name"], t["tier"], t.get("description", "")) for t in await self._ctx._call("tools.list")]

    async def call(self, name: str, args: dict[str, Any] | None = None, /) -> Any:
        """Runs one of them as the brain would, with its arguments as one dict (Home Assistant's tools take
        a `name` of their own), and returns its result; a failure raises with its error."""
        return await self._ctx._call("tools.call", name=name, args=args or {})


class _Presence:
    def __init__(self, ctx: Ctx):
        self._ctx = ctx

    async def home(self) -> bool:
        return bool((await self._ctx._call("presence"))["home"])

    async def since(self) -> datetime | None:
        return _dt((await self._ctx._call("presence"))["since"])


class _Speakers:
    """Your speakers (the satellite box in the room you are in): an audio stream the extension serves
    on this machine (127.0.0.1) or from a host it declares, in any common format. The core turns it
    into what the box plays, 48 kHz stereo, lossless on the way. One stream at a time per extension;
    the box turns it down while he listens and talks."""

    def __init__(self, ctx: Ctx):
        self._ctx = ctx

    async def play(self, url: str, title: str | None = None) -> int:
        """Starts playing `url`; returns how many speakers took it (0: none is connected)."""
        return int((await self._ctx._call("speakers.play", url=url, title=title))["speakers"])

    async def stop(self) -> None:
        await self._ctx._call("speakers.stop")


class _LLM:
    def __init__(self, ctx: Ctx):
        self._ctx = ctx

    async def ask(self, prompt: str, schema: type[BaseModel] | None = None, max_tokens: int = 500) -> Any:
        answer = await self._ctx._call("llm.ask", prompt=prompt, max_tokens=max_tokens,
                                       schema=schema.model_json_schema() if schema else None)
        return schema.model_validate(answer) if schema else answer


class _Other:
    def __init__(self, ctx: Ctx, ext_id: str):
        self._ctx, self._id = ctx, ext_id

    async def call(self, tool: str, **args: Any) -> Any:
        return await self._ctx._call("ext.call", extension=self._id, tool=tool, args=args)


class Ctx:
    """The context every handler gets first."""

    def __init__(self, runtime: Runtime):
        self._rt = runtime
        self.log = Log(runtime.write_log)
        self.ha = _HA(self)
        self.memory = _Memory(self)
        self.presence = _Presence(self)
        self.tools = _Tools(self)
        self.llm = _LLM(self)
        self.speakers = _Speakers(self)
        self._http: httpx.AsyncClient | None = None

    async def _call(self, op: str, **params: Any) -> Any:
        return await self._rt.services.call(op, **params)

    @property
    def settings(self) -> Any:
        return self._rt.settings

    @property
    def store(self) -> dict[str, Any]:
        return self._rt.store

    @property
    def folder(self) -> Path:
        return self._rt.state_dir

    def now(self) -> datetime:
        return self._rt.clock.now()

    def today(self) -> date:
        return self.now().date()

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = self._rt.make_http(self)
        return self._http

    async def devices(self) -> list[Device]:
        """Every device Home Assistant knows, exposed or not (needs `devices`)."""
        return [Device(**d) for d in await self._call("devices")]

    def ext(self, extension_id: str) -> _Other:
        return _Other(self, extension_id)

    async def later(self, when: datetime | timedelta, job: str | Callable, data: Any = None) -> Timer:
        at = self.now() + when if isinstance(when, timedelta) else when
        if at.tzinfo is None:
            raise ValueError("ctx.later needs an aware datetime (ctx.now() gives one)")
        name = job if isinstance(job, str) else job.__name__
        if name not in self._rt.ext.jobs:
            raise ValueError(f"no job named {name!r}")
        row = await self._call("timer.set", job=name, at=at.isoformat(), data=data)
        return Timer(row["id"], name, at, data, self)

    async def timers(self, job: str | Callable | None = None) -> list[Timer]:
        name = job if isinstance(job, str) or job is None else job.__name__
        rows = await self._call("timer.list", job=name)
        return [Timer(r["id"], r["job"], datetime.fromisoformat(r["at"]), r.get("data"), self) for r in rows]

    async def nudge(self, kind: str, nudge: Nudge) -> None:
        spec = self._rt.ext.kinds.get(kind)
        if spec is None:
            raise ValueError(f"no kind named {kind!r}")
        if spec.check is not None:
            raise ValueError(f"{kind!r} is a checked kind; push only to a kind made with ext.kind")
        self._rt.check_actions(kind, nudge)
        await self._call("nudge.push", kind=kind, nudge=nudge.model_dump(mode="json"))

    async def withdraw(self, kind: str, about: str) -> None:
        await self._call("nudge.withdraw", kind=kind, about=about)

    async def nudges(self, kind: str | None = None) -> list[NudgeState]:
        return [NudgeState.model_validate(r) for r in await self._call("nudge.list", kind=kind)]

    async def quota(self, name: str, per_day: int) -> bool:
        return bool(await self._call("quota", name=name, per_day=per_day))

    async def poke(self, kind: str) -> None:
        if kind not in self._rt.ext.kinds:
            raise ValueError(f"no kind named {kind!r}")
        await self._call("poke", kind=kind)

    async def run_sync(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)

    def parse_when(self, text: str) -> datetime | None:
        return parse_when(text, self.now(), self._rt.day_parts)


def make_http(ctx: Ctx, allowed: list[str], transport: httpx.AsyncBaseTransport | None) -> httpx.AsyncClient:
    """An HTTP client limited to the declared hosts. Each request is logged by method, host, path,
    status and time; never the query string, headers or body, where keys often travel."""

    async def check(request: httpx.Request) -> None:
        if "*" not in allowed and request.url.host not in allowed:
            raise NotPermitted(f"{request.url.host} is not in this extension's network permission")
        request.extensions["winston_t0"] = _time.perf_counter()

    async def log(response: httpx.Response) -> None:
        req = response.request
        ms = (_time.perf_counter() - req.extensions.get("winston_t0", _time.perf_counter())) * 1000
        ctx.log.info(f"http {req.method} {req.url.host}{req.url.path} {response.status_code} {ms:.0f} ms")

    return httpx.AsyncClient(timeout=10, transport=transport, follow_redirects=True,
                             event_hooks={"request": [check], "response": [log]})


def stderr_log(extension_id: str) -> Callable[[str, str], None]:
    def write(level: str, msg: str) -> None:
        for line in msg.splitlines() or [""]:
            print(f"{level} {line}", file=sys.stderr, flush=True)
    return write
