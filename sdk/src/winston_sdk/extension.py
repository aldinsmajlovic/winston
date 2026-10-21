"""The `Extension` object: every decorator records what the extension offers, nothing more. The
runtime (`runtime.py`) runs it; the core decides when."""
from __future__ import annotations

import inspect
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import time, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from winston_sdk import manifest as manifest_mod
from winston_sdk.fields import From, Settings, parse_duration, parse_time_of_day, to_json

Tier = Literal["read", "act", "restricted"]
DELIVERIES = ("now", "when_home", "when_leaving", "next_talk", "briefing", "silent")
WHERE = ("auto", "speakers", "phone")
ENTITY_KINDS = ("sensor", "binary_sensor", "event", "switch", "button", "number", "select", "text")

# Every Extension made in this process, so the runner can find the one a module defines.
_made: list[Extension] = []


def _doc(fn: Callable) -> tuple[str, dict[str, str]]:
    """The docstring's first paragraph, and an `Args:` section as name -> description."""
    doc = inspect.cleandoc(fn.__doc__ or "")
    head = doc.partition("\n\n")[0]
    args: dict[str, str] = {}
    m = re.search(r"(?ms)^Args:\s*\n(.*?)(?:^\S|\Z)", doc)
    if m:
        current = None
        for line in m.group(1).splitlines():
            item = re.match(r"^\s{2,}(\w+)(?:\s*\([^)]*\))?:\s*(.*)$", line)
            if item and (current is None or len(line) - len(line.lstrip()) <= 4):
                current = item.group(1)
                args[current] = item.group(2).strip()
            elif current and line.strip():
                args[current] += " " + line.strip()
    description = " ".join(head.split()) if not head.startswith("Args:") else ""
    return description, args


def _days(days: str | None) -> list[int] | None:
    """"mon-fri", "sat,sun", "mon,wed-fri" -> weekday numbers."""
    if days is None:
        return None
    names = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    out: set[int] = set()
    for part in days.lower().replace(" ", "").split(","):
        a, _, b = part.partition("-")
        if a[:3] not in names or (b and b[:3] not in names):
            raise ValueError(f"not a day: {part!r} (use mon-fri, sat,sun)")
        i, j = names.index(a[:3]), names.index((b or a)[:3])
        out.update(range(i, j + 1) if i <= j else [*range(i, 7), *range(j + 1)])
    return sorted(out)


def _check_cron(expr: str) -> str:
    if len(expr.split()) != 5:
        raise ValueError(f"cron needs five fields: {expr!r}")
    return expr


@dataclass
class ToolSpec:
    name: str
    fn: Callable
    tier: Tier
    outside: bool
    timeout: float
    filler: str | None
    filler_after: float
    description: str
    arg_docs: dict[str, str]
    title: str | None = None            # for people: a short verb phrase, "Play music"
    summary: str | None = None          # for people: one sentence on what it does


@dataclass
class JobSpec:
    name: str
    fn: Callable
    trigger: dict[str, Any]
    timeout: float | None = 120
    catch_up: bool = True


@dataclass
class KindSpec:
    kind: str
    title: str
    check: Callable | None
    deliver: Any = "now"
    where: Any = "auto"
    repeat: timedelta | None = None
    may_be_urgent: bool = False
    requested: bool = False
    outside: bool = True
    triggers: dict[str, Any] = field(default_factory=dict)
    description: str | None = None      # for people: what he will say and when


@dataclass
class ActionSpec:
    name: str
    title: str
    kinds: list[str] | None
    tier: Tier
    fn: Callable


@dataclass
class EntitySpec:
    key: str
    kind: str
    name: str
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None
    options: list[str] | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    per: From | None = None
    on_change: Callable | None = None
    on_press: Callable | None = None


class EntityHandle:
    """What `ext.entity(...)` returns: `await e.set(value)`, `e[entry]` for a `per` entity, and the
    `on_change` and `on_press` decorators for what Home Assistant sets."""

    def __init__(self, ext: Extension, spec: EntitySpec, entry: str | None = None):
        self._ext, self.spec, self.entry = ext, spec, entry

    def __getitem__(self, entry: str) -> EntityHandle:
        if self.spec.per is None:
            raise TypeError(f"entity {self.spec.key!r} is not a per entity")
        return EntityHandle(self._ext, self.spec, entry)

    async def set(self, value: Any, **attributes: Any) -> None:
        if self.spec.per is not None and self.entry is None:
            raise TypeError(f"entity {self.spec.key!r} is per {self.spec.per.name!r}: set entity[entry]")
        await self._ext._runtime().entity_set(self.spec, self.entry, value, attributes)

    def on_change(self, fn: Callable) -> Callable:
        if self.spec.kind not in ("switch", "number", "select", "text"):
            raise TypeError(f"on_change is for switch, number, select and text, not {self.spec.kind}")
        self.spec.on_change = fn
        return fn

    def on_press(self, fn: Callable) -> Callable:
        if self.spec.kind != "button":
            raise TypeError("on_press is for buttons")
        self.spec.on_press = fn
        return fn


class Extension:
    """One extension. Make it once at the top of the module: `ext = Extension()`."""

    def __init__(self, manifest: manifest_mod.Manifest | None = None):
        caller = sys._getframe(1).f_globals.get("__file__")
        if manifest is None:
            if not caller:
                raise RuntimeError("Extension() needs to be made in a module file, or be given a manifest")
            manifest = manifest_mod.read(caller)
        self.manifest = manifest
        self.path = Path(caller) if caller else None
        self.settings_model: type[BaseModel] = Settings
        self.tools: dict[str, ToolSpec] = {}
        self.jobs: dict[str, JobSpec] = {}
        self.kinds: dict[str, KindSpec] = {}
        self.actions: dict[str, ActionSpec] = {}
        self.entities: dict[str, EntityHandle] = {}
        self.nudge_listeners: list[tuple[list[str] | None, Callable]] = []
        self.status_fn: Callable | None = None
        self.context_fn: Callable | None = None
        self.hint_words_fn: Callable | None = None
        self.validate_fn: Callable | None = None
        self.choice_fns: dict[str, Callable] = {}
        self._bound = None
        _made.append(self)

    def _runtime(self):
        if self._bound is None:
            raise RuntimeError("the extension is not running (use winston_sdk.testing.Harness in tests)")
        return self._bound

    # settings

    def settings(self, model: type[BaseModel]) -> type[BaseModel]:
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise TypeError("ext.settings takes a Settings subclass")
        self.settings_model = model
        return model

    # tools

    def tool(self, tier: Tier, name: str | None = None, outside: bool = True, timeout: float = 10,
             filler: str | None = None, filler_after: float = 1, title: str | None = None,
             summary: str | None = None) -> Callable[[Callable], Callable]:
        """A tool for the brain; its docstring is what he is told. `title` ("Play music") and `summary` (one
        sentence) are what people read in settings: the title defaults to the name in words."""
        if tier not in ("read", "act", "restricted"):
            raise ValueError(f"tier must be read, act or restricted, not {tier!r}")
        if not 0 < timeout <= 60:
            raise ValueError("a tool's timeout is at most 60 s")
        if filler_after < 0:
            raise ValueError("filler_after is seconds, 0 or more")

        def add(fn: Callable) -> Callable:
            _need_async(fn)
            tool_name = name or fn.__name__
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", tool_name):
                raise ValueError(f"tool name {tool_name!r}: lowercase letters, digits and underscores")
            if tool_name in self.tools:
                raise ValueError(f"two tools named {tool_name!r}")
            description, arg_docs = _doc(fn)
            if not description:
                raise ValueError(f"tool {tool_name!r} needs a docstring: its first paragraph describes it")
            self.tools[tool_name] = ToolSpec(tool_name, fn, tier, outside, timeout, filler, filler_after,
                                             description, arg_docs, title, summary)
            return fn
        return add

    # jobs

    def _job(self, fn: Callable, trigger: dict[str, Any], timeout: float | None = 120, catch_up: bool = True):
        _need_async(fn)
        if fn.__name__ in self.jobs:
            raise ValueError(f"{fn.__name__!r} already has a schedule or trigger: write a second function")
        if timeout is not None and not 0 < timeout <= 600:
            raise ValueError("a job's timeout is at most 600 s")
        self.jobs[fn.__name__] = JobSpec(fn.__name__, fn, trigger, timeout, catch_up)
        return fn

    def every(self, interval: str | timedelta, align: bool = False, catch_up: bool = True,
              timeout: float = 120) -> Callable[[Callable], Callable]:
        td = parse_duration(interval)
        if td < timedelta(seconds=10):
            raise ValueError("the shortest interval is 10 s")
        return lambda fn: self._job(fn, {"kind": "every", "seconds": td.total_seconds(), "align": align},
                                    timeout, catch_up)

    def at(self, when: str | time | From, days: str | None = None, catch_up: bool = True,
           timeout: float = 120) -> Callable[[Callable], Callable]:
        value = when if isinstance(when, From) else parse_time_of_day(when)
        return lambda fn: self._job(fn, {"kind": "at", "time": to_json(value), "days": _days(days)},
                                    timeout, catch_up)

    def cron(self, expr: str, catch_up: bool = True, timeout: float = 120) -> Callable[[Callable], Callable]:
        return lambda fn: self._job(fn, {"kind": "cron", "expr": _check_cron(expr)}, timeout, catch_up)

    def job(self, fn: Callable | None = None, *, timeout: float = 120):
        """A job with no schedule: run by a timer from `ctx.later`, a hook or `winston ext run`."""
        if fn is not None:
            return self._job(fn, {"kind": "job"}, timeout)
        return lambda f: self._job(f, {"kind": "job"}, timeout)

    def on_state(self, entity: str | list[str] | From, to: str | None = None, from_: str | None = None,
                 for_: str | timedelta | None = None, timeout: float = 120) -> Callable[[Callable], Callable]:
        entities = entity if isinstance(entity, (From, list)) else [entity]
        trigger = {"kind": "on_state", "entity": to_json(entities), "to": to, "from": from_,
                   "for": parse_duration(for_).total_seconds() if for_ is not None else None}
        return lambda fn: self._job(fn, trigger, timeout)

    def on_event(self, event_type: str, timeout: float = 120) -> Callable[[Callable], Callable]:
        return lambda fn: self._job(fn, {"kind": "on_event", "event_type": event_type}, timeout)

    def on_arrive(self, fn: Callable) -> Callable:
        return self._job(fn, {"kind": "on_arrive"})

    def on_leave(self, fn: Callable) -> Callable:
        return self._job(fn, {"kind": "on_leave"})

    def on_start(self, fn: Callable) -> Callable:
        return self._job(fn, {"kind": "on_start"})

    def on_settings(self, fn: Callable) -> Callable:
        return self._job(fn, {"kind": "on_settings"})

    def on_speakers_stop(self, fn: Callable) -> Callable:
        return self._job(fn, {"kind": "on_speakers_stop"})

    def hook(self, name: str, timeout: float = 30) -> Callable[[Callable], Callable]:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", name):
            raise ValueError(f"hook name {name!r}: lowercase letters, digits, - and _")
        return lambda fn: self._job(fn, {"kind": "hook", "name": name}, timeout)

    def background(self, fn: Callable) -> Callable:
        return self._job(fn, {"kind": "background"}, timeout=None)

    # speaking up

    def _kind(self, kind: str, title: str, check: Callable | None, options: dict[str, Any],
              description: str | None = None) -> KindSpec:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", kind):
            raise ValueError(f"kind {kind!r}: lowercase letters, digits and underscores")
        if kind in self.kinds:
            raise ValueError(f"two kinds named {kind!r}")
        for key in ("deliver", "where"):
            allowed = DELIVERIES if key == "deliver" else WHERE
            value = options.get(key)
            values = value.values() if isinstance(value, dict) else [value]
            if any(v not in allowed for v in values):
                raise ValueError(f"{key} must be one of {', '.join(allowed)}, or one per level")
        repeat = options.get("repeat")
        spec = KindSpec(kind, title, check, options["deliver"], options["where"],
                        parse_duration(repeat) if repeat is not None else None, options["may_be_urgent"],
                        options["requested"], options["outside"], options.get("triggers", {}), description)
        if repeat is not None and check is None:
            raise ValueError("repeat is for checked kinds; a pushed kind pushes again")
        self.kinds[kind] = spec
        return spec

    def nudge(self, kind: str, title: str, *, deliver: Any = "now", where: Any = "auto",
              repeat: str | timedelta | None = None, may_be_urgent: bool = False, requested: bool = False,
              outside: bool = True, every: str | timedelta | None = None, at: str | From | None = None,
              cron: str | None = None, on_state: str | list[str] | From | None = None,
              on_event: str | None = None, on_arrive: bool = False, on_leave: bool = False,
              description: str | None = None) -> Callable[[Callable], Callable]:
        """A checked kind: the function is its check, returning None, a Nudge or a list. `description` tells
        people what he will say and when."""
        triggers: dict[str, Any] = {}
        if every is not None:
            triggers["every"] = parse_duration(every).total_seconds()
        if at is not None:
            triggers["at"] = to_json(at if isinstance(at, From) else parse_time_of_day(at))
        if cron is not None:
            triggers["cron"] = _check_cron(cron)
        if on_state is not None:
            triggers["on_state"] = to_json(on_state if isinstance(on_state, (From, list)) else [on_state])
        if on_event is not None:
            triggers["on_event"] = on_event
        if on_arrive:
            triggers["on_arrive"] = True
        if on_leave:
            triggers["on_leave"] = True

        def add(fn: Callable) -> Callable:
            _need_async(fn)
            self._kind(kind, title, fn, {"deliver": deliver, "where": where, "repeat": repeat,
                                          "may_be_urgent": may_be_urgent, "requested": requested,
                                          "outside": outside, "triggers": triggers}, description)
            return fn
        return add

    def kind(self, kind: str, title: str, *, deliver: Any = "now", where: Any = "auto",
             may_be_urgent: bool = False, requested: bool = False, outside: bool = True,
             description: str | None = None) -> None:
        """A pushed kind, for `ctx.nudge(kind, Nudge(...))`."""
        self._kind(kind, title, None, {"deliver": deliver, "where": where, "may_be_urgent": may_be_urgent,
                                        "requested": requested, "outside": outside}, description)

    def action(self, title: str, kinds: list[str] | None = None, name: str | None = None,
               tier: Tier = "act") -> Callable[[Callable], Callable]:
        if tier not in ("read", "act", "restricted"):
            raise ValueError(f"tier must be read, act or restricted, not {tier!r}")

        def add(fn: Callable) -> Callable:
            _need_async(fn)
            action_name = name or fn.__name__
            if action_name in self.actions:
                raise ValueError(f"two actions named {action_name!r}")
            self.actions[action_name] = ActionSpec(action_name, title, kinds, tier, fn)
            return fn
        return add

    def on_nudge(self, fn: Callable | None = None, *, kinds: list[str] | None = None):
        def add(f: Callable) -> Callable:
            _need_async(f)
            self.nudge_listeners.append((kinds, f))
            return f
        return add(fn) if fn is not None else add

    # entities

    def entity(self, key: str, kind: str, name: str, unit: str | None = None, device_class: str | None = None,
               state_class: str | None = None, icon: str | None = None, options: list[str] | None = None,
               min: float | None = None, max: float | None = None, step: float | None = None,
               per: From | None = None) -> EntityHandle:
        if kind not in ENTITY_KINDS:
            raise ValueError(f"entity kind must be one of {', '.join(ENTITY_KINDS)}")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", key):
            raise ValueError(f"entity key {key!r}: lowercase letters, digits and underscores")
        if key in self.entities:
            raise ValueError(f"two entities with the key {key!r}")
        if per is not None and "{entry}" not in name:
            raise ValueError("a per entity's name needs {entry} in it, e.g. \"{entry} done\"")
        handle = EntityHandle(self, EntitySpec(key, kind, name, unit, device_class, state_class, icon, options,
                                               min, max, step, per))
        self.entities[key] = handle
        return handle

    # the rest

    def status(self, fn: Callable) -> Callable:
        self.status_fn = _need_async(fn)
        return fn

    def context(self, fn: Callable) -> Callable:
        self.context_fn = _need_async(fn)
        return fn

    def hint_words(self, fn: Callable) -> Callable:
        self.hint_words_fn = _need_async(fn)
        return fn

    def validate(self, fn: Callable) -> Callable:
        self.validate_fn = _need_async(fn)
        return fn

    def choices(self, fn: Callable) -> Callable:
        """A list for pickers in its settings, by the function's name: `Field(choices="playlists")` offers
        what `async def playlists(ctx)` returns, `Choice`s or plain values."""
        self.choice_fns[fn.__name__] = _need_async(fn)
        return fn


def _need_async(fn: Callable) -> Callable:
    if not inspect.iscoroutinefunction(fn):
        raise TypeError(f"{getattr(fn, '__name__', fn)!r} must be async (async def)")
    return fn
