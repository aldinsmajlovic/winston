"""Testing an extension without Winston: a clock you move, a fake Home Assistant, recorded HTTP
answers, and a record of what the extension pushed, set and asked for.

    h = Harness("src/winston/extensions/hevy.py", settings={"rest_days": 3},
                http=recorded("tests/fixtures/hevy/one_workout.json"))
    await h.run("poll")
    h.clock.advance(days=3)
    [nudge] = await h.check("rest")

`run_tests(path)` runs every `async def test_*` in a file, which is what `winston ext test` does, so
no test framework is needed; pytest with anyio works too."""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import itertools
import json
import shutil
import sys
import tempfile
import time as _time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

import httpx

from winston_sdk.context import Clock
from winston_sdk.errors import NotPermitted
from winston_sdk.loader import load
from winston_sdk.nudge import Nudge, NudgeInfo
from winston_sdk.runtime import Runtime

__all__ = ["FakeClock", "FakeHA", "Harness", "HandlerFailed", "recorded", "run_tests"]


class HandlerFailed(AssertionError):
    """A handler raised or returned an error; the message is what the card would show."""


class FakeClock(Clock):
    def __init__(self, start: datetime | str = "2026-01-05T09:00:00", zone: str = "UTC"):
        super().__init__(ZoneInfo(zone))
        at = datetime.fromisoformat(start) if isinstance(start, str) else start
        self._now = at if at.tzinfo else at.replace(tzinfo=self.zone)

    def now(self) -> datetime:
        return self._now.astimezone(self.zone)

    def advance(self, **kwargs: float) -> datetime:
        """`h.clock.advance(days=3, hours=2)`."""
        self._now += timedelta(**kwargs)
        return self.now()

    def set(self, at: datetime | str) -> datetime:
        at = datetime.fromisoformat(at) if isinstance(at, str) else at
        self._now = at if at.tzinfo else at.replace(tzinfo=self.zone)
        return self.now()


class FakeHA:
    """States and service answers for `ctx.ha`. `calls` records every service call."""

    def __init__(self, states: dict[str, Any] | None = None, services: dict[str, Any] | None = None):
        self.states: dict[str, dict[str, Any]] = {}
        self.history: dict[str, list[dict[str, Any]]] = {}
        for entity_id, value in (states or {}).items():
            if isinstance(value, dict):
                self.set(entity_id, value.get("state", ""), value.get("attributes"))
            else:
                self.set(entity_id, value)
        self.services = services or {}
        self.calls: list[dict[str, Any]] = []

    def set(self, entity_id: str, state: Any, attributes: dict | None = None, at: datetime | None = None) -> None:
        row = {"entity_id": entity_id, "state": str(state), "attributes": attributes or {},
               "last_changed": (at or datetime.now().astimezone()).isoformat()}
        self.states[entity_id] = row
        self.history.setdefault(entity_id, []).append(row)

    def answer(self, domain: str, service: str, data: dict, target: dict) -> Any:
        self.calls.append({"domain": domain, "service": service, "data": data, "target": target})
        found = self.services.get(f"{domain}.{service}")
        return found(data, target) if callable(found) else found


def recorded(source: str | Path | list[dict[str, Any]]) -> httpx.MockTransport:
    """HTTP answers from a list, or a JSON file of one: `[{"method": "GET", "url": "https://...",
    "status": 200, "json": {...}}]`. A url without `?` matches whatever query the request has."""
    rows = source if isinstance(source, list) else json.loads(Path(source).read_text(encoding="utf-8"))

    def matches(want: str, url: httpx.URL) -> bool:
        w = httpx.URL(want)
        if (w.scheme, w.host, w.path) != (url.scheme, url.host, url.path):
            return False
        return "?" not in want or sorted(parse_qsl(w.query.decode())) == sorted(parse_qsl(url.query.decode()))

    def handler(request: httpx.Request) -> httpx.Response:
        for row in rows:
            if row.get("method", "GET").upper() == request.method and matches(row["url"], request.url):
                if "json" in row:
                    return httpx.Response(row.get("status", 200), json=row["json"], headers=row.get("headers"))
                return httpx.Response(row.get("status", 200), text=row.get("text", ""), headers=row.get("headers"))
        raise AssertionError(f"no recorded answer for {request.method} {request.url}")

    return httpx.MockTransport(handler)


class FakeServices:
    """winstond's side, in memory: what a Harness's extension reaches through ctx."""

    def __init__(self, h: Harness):
        self.h = h
        self._ids = itertools.count(1)

    async def call(self, op: str, **p: Any) -> Any:
        h, perms = self.h, self.h.ext.manifest.permissions
        if op.startswith("ha.") and perms.home_assistant is None:
            raise NotPermitted("this extension has no home_assistant permission")
        if op == "ha.call" and perms.home_assistant != "act":
            raise NotPermitted("calling services needs home_assistant = \"act\"")
        if op.startswith("memory.") and perms.memory is None:
            raise NotPermitted("this extension has no memory permission")
        if op == "memory.remember" and perms.memory != "write":
            raise NotPermitted("remembering needs memory = \"write\"")
        if op == "presence" and not perms.presence:
            raise NotPermitted("this extension has no presence permission")
        if op == "llm.ask" and not perms.llm:
            raise NotPermitted("this extension has no llm permission")
        if op == "ext.call" and p["extension"] not in perms.extensions:
            raise NotPermitted(f"this extension may not call {p['extension']}")
        match op:
            case "ha.state":
                return h.ha.states.get(p["entity_id"])
            case "ha.states":
                return [s for eid, s in h.ha.states.items() if eid.split(".")[0] == p["domain"]]
            case "ha.history":
                since = datetime.fromisoformat(p["since"])
                return [s for s in h.ha.history.get(p["entity_id"], [])
                        if datetime.fromisoformat(s["last_changed"]) >= since]
            case "ha.query" | "ha.call":
                return h.ha.answer(p["domain"], p["service"], p["data"], p["target"])
            case "memory.facts":
                return [f for f in h.facts if p.get("topic") in (None, f["topic"])]
            case "memory.remember":
                h.facts.append({"text": p["text"], "topic": p["topic"], "noted": str(h.clock.now().date()),
                                "until": p.get("until")})
                return f"Remembered: {p['text']}"
            case "presence":
                return {"home": h.home, "since": None}
            case "llm.ask":
                if h.llm is None:
                    raise AssertionError("the extension asked the model; give the Harness llm=")
                return h.llm(p["prompt"], p.get("schema"))
            case "ext.call":
                fake = h.others.get(p["extension"], {}).get(p["tool"])
                if fake is None:
                    raise AssertionError(f"no fake for {p['extension']}.{p['tool']}; give the Harness others=")
                return fake(**p["args"]) if callable(fake) else fake
            case "timer.set":
                tid = f"t{next(self._ids)}"
                h.timers[tid] = {"id": tid, "job": p["job"], "at": p["at"], "data": p.get("data")}
                return h.timers[tid]
            case "timer.list":
                return [t for t in h.timers.values() if p.get("job") in (None, t["job"])]
            case "timer.cancel":
                h.timers.pop(p["id"], None)
                return None
            case "quota":
                key = (p["name"], str(h.clock.now().date()))
                h.quota_used[key] = h.quota_used.get(key, 0) + 1
                return h.quota_used[key] <= p["per_day"]
            case "poke":
                h.pokes.append(p["kind"])
                return None
            case "nudge.push":
                h.pushed.append((p["kind"], Nudge.model_validate(p["nudge"])))
                return None
            case "nudge.withdraw":
                h.pushed = [(k, n) for k, n in h.pushed if (k, n.about) != (p["kind"], p["about"])]
                return None
            case "nudge.list":
                return [{"kind": k, "about": n.about, "level": n.level, "facts": n.facts, "state": "waiting"}
                        for k, n in h.pushed if p.get("kind") in (None, k)]
            case "entity.set":
                name = p["key"] if p.get("entry") is None else f"{p['key']}[{p['entry']}]"
                h.entities[name] = p["value"]
                return None
        raise AssertionError(f"unknown operation {op}")


class Harness:
    """One extension, running against fakes."""

    def __init__(self, target: str | Path, *, settings: dict[str, Any] | None = None,
                 secrets: dict[str, str] | None = None, http: httpx.AsyncBaseTransport | None = None,
                 ha: FakeHA | None = None, clock: FakeClock | None = None, home: bool = True,
                 facts: list[dict[str, Any]] | None = None, llm: Any = None,
                 others: dict[str, dict[str, Any]] | None = None, day_parts: dict[str, str] | None = None):
        self.ext = load(str(target))
        self.clock = clock or FakeClock()
        self.ha = ha or FakeHA()
        self.home, self.facts, self.llm, self.others = home, list(facts or []), llm, others or {}
        self.entities: dict[str, Any] = {}
        self.pushed: list[tuple[str, Nudge]] = []
        self.timers: dict[str, dict[str, Any]] = {}
        self.pokes: list[str] = []
        self.quota_used: dict[tuple[str, str], int] = {}
        self.log: list[tuple[str, str]] = []
        self._folder = tempfile.mkdtemp(prefix=f"winston-{self.ext.manifest.id}-")
        self.rt = Runtime(self.ext, services=FakeServices(self), clock=self.clock, state_dir=self._folder,
                          transport=http, log_write=lambda level, msg: self.log.append((level, msg)))
        self._settings, self._secrets, self._day_parts = settings or {}, secrets or {}, day_parts
        self._started = False

    @property
    def store(self) -> dict[str, Any]:
        return self.rt.store

    async def _start(self) -> None:
        if not self._started:
            result = await self.rt.configure(self._settings, self._secrets, day_parts=self._day_parts)
            if not result["ok"]:
                raise HandlerFailed(f"settings: {result['errors']}")
            self._started = True

    async def configure(self, settings: dict[str, Any], secrets: dict[str, str] | None = None,
                        validate: bool = True) -> list[dict[str, Any]]:
        """Saves new settings as the card would; returns the field errors, empty when they took."""
        await self._start()
        result = await self.rt.configure(settings, secrets or self._secrets, validate=validate)
        return result.get("errors", [])

    @staticmethod
    def _ok(result: dict[str, Any]) -> Any:
        if not result.get("ok"):
            raise HandlerFailed(result.get("error") or result.get("errors"))
        return result.get("result")

    async def run(self, job: str, /, **data: Any) -> None:
        await self._start()
        self._ok(await self.rt.run(job, data))

    async def run_due_timers(self) -> int:
        """Runs the timers whose time has come on the fake clock; returns how many ran."""
        await self._start()
        due = [t for t in self.timers.values() if datetime.fromisoformat(t["at"]) <= self.clock.now()]
        for t in due:
            self.timers.pop(t["id"], None)
            await self.run(t["job"], data=t["data"])
        return len(due)

    async def tool(self, name: str, /, **args: Any) -> Any:
        await self._start()
        return await self.rt.call_tool(name, args)

    async def check(self, kind: str) -> list[Nudge]:
        await self._start()
        result = self._ok_nudges(await self.rt.check(kind))
        return [Nudge.model_validate(n) for n in result]

    @staticmethod
    def _ok_nudges(result: dict[str, Any]) -> list[dict[str, Any]]:
        if not result.get("ok"):
            raise HandlerFailed(result.get("error"))
        return result["nudges"]

    async def act(self, action: str, nudge: Nudge | NudgeInfo, kind: str | None = None) -> Any:
        await self._start()
        info = nudge if isinstance(nudge, NudgeInfo) else NudgeInfo(
            kind=kind or next(iter(self.ext.kinds)), about=nudge.about, level=nudge.level, facts=nudge.facts)
        return self._ok(await self.rt.act(action, info.model_dump(mode="json")))

    async def nudge_event(self, what: str, nudge: Nudge | NudgeInfo, kind: str | None = None,
                          until: datetime | None = None) -> None:
        await self._start()
        info = nudge if isinstance(nudge, NudgeInfo) else NudgeInfo(
            kind=kind or next(iter(self.ext.kinds)), about=nudge.about, level=nudge.level, facts=nudge.facts)
        await self.rt.nudge_event(what, info.model_dump(mode="json"), until.isoformat() if until else None)

    async def press(self, key: str, entry: str | None = None) -> None:
        await self._start()
        self._ok(await self.rt.entity(key, entry, None, True))

    async def set_entity(self, key: str, value: Any, entry: str | None = None) -> None:
        await self._start()
        self._ok(await self.rt.entity(key, entry, value, False))

    async def hook(self, name: str, /, json: Any = None, headers: dict[str, str] | None = None) -> Any:
        await self._start()
        return self._ok(await self.rt.hook(name, json, headers or {}))

    async def status(self) -> str | None:
        await self._start()
        return await self.rt.status()

    async def context(self) -> str | None:
        await self._start()
        return await self.rt.context()

    def describe(self) -> dict[str, Any]:
        return self.rt.describe()

    async def close(self) -> None:
        await self.rt.close()
        shutil.rmtree(self._folder, ignore_errors=True)


def run_tests(path: str | Path, only: str | None = None) -> int:
    """Runs every `async def test_*` (and plain `def test_*`) in a file; prints one line each.
    Returns the number that failed."""
    path = Path(path).resolve()
    spec = importlib.util.spec_from_file_location(f"winston_tests_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    tests = [(n, f) for n, f in vars(module).items() if n.startswith("test_") and callable(f)
             and (only is None or only in n)]
    failed = 0
    for name, fn in tests:
        t0 = _time.perf_counter()
        try:
            result = fn()
            if inspect.iscoroutine(result):
                asyncio.run(result)
            print(f"ok   {name} ({(_time.perf_counter() - t0) * 1000:.0f} ms)")
        except Exception as e:                          # a failing test prints and the next one runs
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
            print("".join(traceback.format_exception(e)[-3:]).rstrip())
    print(f"\n{len(tests) - failed} passed, {failed} failed")
    return failed
