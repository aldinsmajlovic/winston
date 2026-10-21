"""Runs an extension: calls its handlers with `ctx` (and `event` where the handler takes one), keeps
its store, applies its settings, reads `When` arguments, starts its background tasks and describes
it. The MCP server (`server.py`) and the test harness (`testing.py`) are both thin layers on this:
each `winston/*` method is the method here of the same name, taking its params and returning its result."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import traceback
import types
import typing
from collections.abc import Callable
from datetime import datetime, time
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any

import httpx
import pydantic
from mcp.server.mcpserver.exceptions import ToolError as MCPToolError
from mcp.server.mcpserver.tools.base import Tool as MCPTool

from winston_sdk._version import __version__
from winston_sdk.context import Clock, Ctx, State, make_http
from winston_sdk.errors import SettingsError
from winston_sdk.extension import Extension, JobSpec, ToolSpec
from winston_sdk.fields import Choice, Status, When, own_choices, secret_fields, to_json
from winston_sdk.nudge import Nudge, NudgeInfo
from winston_sdk.when import parse_when

CONTEXT_MAX = 300


class Event(SimpleNamespace):
    """The second argument a handler may take: what started it."""


def _takes_event(fn: Callable) -> bool:
    params = [p for p in inspect.signature(fn).parameters.values()
              if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return len(params) >= 2


def _is_when(annotation: Any) -> bool:
    if annotation is When:
        return True
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        return any(a is When for a in typing.get_args(annotation))
    return False


class ToolError(MCPToolError):
    """A tool failed; the message goes to the brain. MCP's own ToolError, since the protocol keeps
    only its message and hides any other exception's."""


class Runtime:
    def __init__(self, ext: Extension, *, services: Any, clock: Clock | None = None,
                 state_dir: str | Path | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 log_write: Callable[[str, str], None] | None = None, background: bool = True):
        self.ext = ext
        ext._bound = self
        self.services = services
        self.clock = clock or Clock()
        self.state_dir = Path(state_dir or os.environ.get("WINSTON_STATE") or ".")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._store_path = self.state_dir / "store.json"
        self.store: dict[str, Any] = self._load_store()
        self._transport = transport
        self._log_write = log_write or (lambda level, msg: None)
        self.settings: Any = ext.settings_model.model_construct()
        self.day_parts: dict[str, time] = {}
        self.configured = False
        self._run_background = background                  # off in tests that drive those tasks themselves
        self._background: list[asyncio.Task] = []
        self.ctx = Ctx(self)

    # plumbing

    def write_log(self, level: str, msg: str) -> None:
        self._log_write(level, msg)

    def make_http(self, ctx: Ctx) -> httpx.AsyncClient:
        return make_http(ctx, self.ext.manifest.permissions.network, self._transport)

    def _load_store(self) -> dict[str, Any]:
        try:
            data = json.loads(self._store_path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_store(self) -> None:
        tmp = self._store_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.store, default=str, ensure_ascii=False))
            tmp.replace(self._store_path)
        except (TypeError, ValueError) as e:
            self.write_log("error", f"the store could not be saved (only JSON values fit): {e}")

    def _error(self, where: str, e: BaseException) -> str:
        msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        self.write_log("error", f"{where}: {msg}\n{''.join(traceback.format_exception(e))}")
        return msg

    async def invoke(self, fn: Callable, event: Event | None = None, *, timeout: float | None = None,
                     where: str = "") -> Any:
        """Calls one handler; the store is saved after it, whatever happens."""
        args = [self.ctx, event if event is not None else Event()] if _takes_event(fn) else [self.ctx]
        try:
            if timeout is None:
                return await fn(*args)
            return await asyncio.wait_for(fn(*args), timeout)
        except TimeoutError as e:
            raise TimeoutError(f"{where or fn.__name__} took longer than {timeout:g} s") from e
        finally:
            self.save_store()

    # settings

    def ready_to_run(self) -> bool:
        """Before its settings arrive it runs nothing that needs them: a key would still be its variable's
        name. One with no keys and nothing required runs on its defaults, so a plain MCP client that
        never sends settings can still use it."""
        if self.configured:
            return True
        model = self.ext.settings_model
        return not secret_fields(model) and not any(f.is_required() for f in model.model_fields.values())

    def _secret_names(self) -> dict[str, str]:
        return secret_fields(self.ext.settings_model)

    async def configure(self, settings: dict[str, Any] | None = None, secrets: dict[str, Any] | None = None,
                        validate_only: bool = False, validate_first: bool = False,
                        day_parts: dict[str, str] | None = None, time_zone: str | None = None) -> dict[str, Any]:
        """New settings. With `validate_first` the extension's own check runs before they take effect
        (and `@ext.on_settings` jobs run when they change after the first time); `validate_only` runs
        the check and keeps the old ones."""
        validate, apply = validate_first or validate_only, not validate_only
        values = {**(settings or {}), **(secrets or {})}
        try:
            new = self.ext.settings_model.model_validate(values)
        except pydantic.ValidationError as e:
            return {"ok": False, "errors": [{"field": ".".join(str(p) for p in err["loc"]) or None,
                                             "message": err["msg"]} for err in e.errors()]}
        if validate and self.ext.validate_fn is not None:
            try:
                await self.invoke(self.ext.validate_fn, Event(settings=new), timeout=10, where="validate")
            except SettingsError as e:
                return {"ok": False, "errors": [{"field": e.field, "message": e.message}]}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "errors": [{"field": None, "message": self._error("validate", e)}]}
        if not apply:
            return {"ok": True}
        if day_parts:
            self.day_parts = {k: time.fromisoformat(v) for k, v in day_parts.items()}
        if time_zone:
            from zoneinfo import ZoneInfo
            self.clock.zone = ZoneInfo(time_zone)
        old, first = self.settings, not self.configured
        self.settings, self.configured = new, True
        if first and self._run_background:
            self._start_background()
        elif old != new:
            for spec in self._jobs("on_settings"):
                await self._run_job(spec, Event(old=old))
        return {"ok": True}

    # jobs

    def _jobs(self, kind: str) -> list[JobSpec]:
        return [j for j in self.ext.jobs.values() if j.trigger["kind"] == kind]

    def _start_background(self) -> None:
        for spec in self._jobs("background"):
            self._background.append(asyncio.create_task(self._forever(spec)))

    async def _forever(self, spec: JobSpec) -> None:
        while True:
            try:
                await self.invoke(spec.fn, Event(time=self.clock.now()), where=spec.name)
                return                                   # it finished on its own: not restarted
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._error(spec.name, e)
                await asyncio.sleep(5)

    def _event(self, spec: JobSpec, data: dict[str, Any]) -> Event:
        kind = spec.trigger["kind"]
        now = datetime.fromisoformat(data["time"]) if data.get("time") else self.clock.now()
        if kind == "on_state":
            return Event(entity_id=data.get("entity_id"), old=State.of(data.get("old")), new=State.of(data.get("new")))
        if kind == "on_event":
            return Event(type=data.get("type"), data=data.get("data") or {})
        if kind == "job":
            return Event(time=now, data=data.get("data"))
        return Event(time=now)

    async def _run_job(self, spec: JobSpec, event: Event) -> dict[str, Any]:
        if not self.ready_to_run():
            return {"ok": False, "error": "its settings have not arrived yet"}
        try:
            await self.invoke(spec.fn, event, timeout=spec.timeout, where=spec.name)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": self._error(spec.name, e)}

    async def run(self, job: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        spec = self.ext.jobs.get(job)
        if spec is None or spec.trigger["kind"] in ("background", "hook"):
            return {"ok": False, "error": f"no job named {job!r}"}
        return await self._run_job(spec, self._event(spec, data or {}))

    async def hook(self, name: str, body: Any = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        spec = next((j for j in self._jobs("hook") if j.trigger["name"] == name), None)
        if spec is None:
            return {"ok": False, "error": f"no hook named {name!r}"}
        try:
            result = await self.invoke(spec.fn, Event(json=body, headers=headers or {}), timeout=spec.timeout,
                                       where=f"hook {name}")
            return {"ok": True, "result": result}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": self._error(f"hook {name}", e)}

    # tools

    def tool_wrapper(self, spec: ToolSpec) -> Callable:
        """The tool as MCP sees it: the handler's arguments without `ctx`, described from the
        docstring, a `When` taken as text and read here."""
        sig = inspect.signature(spec.fn)
        hints = typing.get_type_hints(spec.fn, include_extras=True)
        params, whens = [], set()
        for p in list(sig.parameters.values())[1:]:
            ann = hints.get(p.name, str)
            desc = spec.arg_docs.get(p.name)
            if _is_when(ann):
                whens.add(p.name)
                optional = ann is not When
                desc = desc or "A time as the user said it, e.g. \"at six\", \"in 20 minutes\"."
                ann = Annotated[str | None if optional else str,
                                pydantic.Field(description=desc, json_schema_extra={"x-winston-when": True})]
            elif desc:
                ann = Annotated[ann, pydantic.Field(description=desc)]
            params.append(p.replace(annotation=ann, kind=inspect.Parameter.KEYWORD_ONLY))

        async def wrapper(**kwargs: Any) -> Any:
            if not self.ready_to_run():
                raise ToolError("It is still starting: its settings have not arrived yet. Try again in a moment.")
            for name in whens:
                if kwargs.get(name) is not None:
                    at = parse_when(kwargs[name], self.clock.now(), self.day_parts)
                    if at is None:
                        raise ToolError(f"Could not read the time {kwargs[name]!r}. Ask for one like \"at six\", "
                                        "\"in 20 minutes\" or \"tomorrow 17:30\".")
                    kwargs[name] = When.fromisoformat(at.isoformat())
            try:
                result = await self.invoke(lambda ctx: spec.fn(ctx, **kwargs), timeout=spec.timeout,
                                           where=f"tool {spec.name}")
                # One JSON text, so a list or a string arrives as exactly what the tool returned.
                return json.dumps(result, default=str, ensure_ascii=False)
            except ToolError:
                raise
            except Exception as e:
                raise ToolError(self._error(f"tool {spec.name}", e)) from e

        wrapper.__signature__ = inspect.Signature(params)
        wrapper.__name__ = spec.name
        wrapper.__doc__ = spec.description
        return wrapper

    def mcp_tool(self, spec: ToolSpec) -> MCPTool:
        from mcp.types import ToolAnnotations
        annotations = ToolAnnotations(read_only_hint=spec.tier == "read", destructive_hint=spec.tier == "restricted",
                                      open_world_hint=spec.outside)
        return MCPTool.from_function(self.tool_wrapper(spec), name=spec.name, title=spec.title,
                                     description=spec.description, annotations=annotations)

    async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
        """A tool called directly (the harness); over MCP the same wrapper runs."""
        spec = self.ext.tools.get(name)
        if spec is None:
            raise ToolError(f"no tool named {name!r}")
        tool = self.mcp_tool(spec)
        try:
            validated = tool.fn_metadata.validate_arguments(args)
        except pydantic.ValidationError as e:
            raise ToolError(f"Error executing tool {name}: {e}") from e
        return json.loads(await tool.fn(**validated))

    # speaking up

    def check_actions(self, kind: str, nudge: Nudge) -> None:
        if nudge.actions:
            known = {a.name for a in self.ext.actions.values() if a.kinds is None or kind in a.kinds}
            unknown = set(nudge.actions) - known
            if unknown:
                raise ValueError(f"nudge names actions its kind does not have: {', '.join(sorted(unknown))}")

    async def check(self, kind: str) -> dict[str, Any]:
        spec = self.ext.kinds.get(kind)
        if spec is None or spec.check is None:
            return {"ok": False, "error": f"no checked kind named {kind!r}"}
        try:
            result = await self.invoke(spec.check, timeout=5, where=f"check {kind}")
            nudges = [] if result is None else [result] if isinstance(result, Nudge) else list(result)
            for n in nudges:
                if not isinstance(n, Nudge):
                    raise TypeError(f"a check returns None, a Nudge or a list of them, not {type(n).__name__}")
                self.check_actions(kind, n)
            return {"ok": True, "nudges": [n.model_dump(mode="json") for n in nudges]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": self._error(f"check {kind}", e)}

    async def act(self, action: str, nudge: dict[str, Any]) -> dict[str, Any]:
        spec = self.ext.actions.get(action)
        if spec is None:
            return {"ok": False, "error": f"no action named {action!r}"}
        try:
            result = await self.invoke(spec.fn, Event(nudge=NudgeInfo.model_validate(nudge)), timeout=30,
                                       where=f"action {action}")
            return {"ok": True, "result": result}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": self._error(f"action {action}", e)}

    async def nudge_event(self, what: str, nudge: dict[str, Any], until: str | None = None,
                          where: str | None = None) -> dict[str, Any]:
        info = NudgeInfo.model_validate(nudge)
        event = Event(what=what, nudge=info, until=datetime.fromisoformat(until) if until else None, where=where)
        for kinds, fn in self.ext.nudge_listeners:
            if kinds is None or info.kind in kinds:
                try:
                    await self.invoke(fn, event, timeout=30, where="on_nudge")
                except Exception as e:  # noqa: BLE001
                    self._error("on_nudge", e)
        return {"ok": True}

    # entities

    async def entity_set(self, spec, entry: str | None, value: Any, attributes: dict[str, Any]) -> None:
        if isinstance(value, datetime):
            value = value.isoformat()
        await self.services.call("entity.set", key=spec.key, entry=entry, value=value, attributes=attributes)

    async def entity(self, key: str, entry: str | None = None, value: Any = None,
                     press: bool = False) -> dict[str, Any]:
        handle = self.ext.entities.get(key)
        if handle is None:
            return {"ok": False, "error": f"no entity {key!r}"}
        fn = handle.spec.on_press if press else handle.spec.on_change
        if fn is None:
            return {"ok": False, "error": f"entity {key!r} does not take that from Home Assistant"}
        event = Event(entry=entry) if press else Event(value=value, entry=entry)
        try:
            await self.invoke(fn, event, timeout=30, where=f"entity {key}")
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": self._error(f"entity {key}", e)}

    # the rest

    async def _optional(self, fn: Callable | None, where: str) -> Any:
        if fn is None:
            return None
        try:
            return await self.invoke(fn, timeout=5, where=where)
        except Exception as e:  # noqa: BLE001
            self._error(where, e)
            return None

    async def status(self) -> dict[str, str | None]:
        """Its line, and a `Status`'s warning and note."""
        out = await self._optional(self.ext.status_fn, "status")
        if out is None:
            return {"text": None, "warning": None, "note": None}
        s = out if isinstance(out, Status) else Status(text=str(out))
        return {"text": s.text[:200], "warning": (s.warning or "")[:200] or None, "note": (s.note or "")[:200] or None}

    async def context(self) -> dict[str, str | None]:
        text = await self._optional(self.ext.context_fn, "context")
        return {"text": str(text)[:CONTEXT_MAX] if text else None}

    async def hint_words(self) -> dict[str, list[str]]:
        words = await self._optional(self.ext.hint_words_fn, "hint_words")
        return {"words": [str(w) for w in (words or [])][:200]}

    async def choices(self, name: str) -> dict[str, Any]:
        """What its `@ext.choices` function `name` offers a picker in its settings."""
        fn = self.ext.choice_fns.get(name)
        if fn is None:
            return {"ok": False, "error": f"no choices named {name!r}"}
        if not self.ready_to_run():
            return {"ok": False, "error": "its settings have not arrived yet"}
        try:
            items = await self.invoke(fn, timeout=5, where=f"choices {name}")
            listed = [Choice(value=c) if isinstance(c, str) else Choice.model_validate(c) for c in items or []]
            return {"ok": True, "choices": [{**c.model_dump(), "label": c.label or c.value} for c in listed]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": self._error(f"choices {name}", e)}

    async def info(self) -> dict[str, Any]:
        """Its process id, for winstond's watch on crashes and memory."""
        return {"pid": os.getpid(), "sdk": __version__, "id": self.ext.manifest.id}

    def describe(self) -> dict[str, Any]:
        ext = self.ext
        schema = own_choices(ext.settings_model.model_json_schema(), ext.manifest.id)
        tools = []
        for spec in ext.tools.values():
            t = self.mcp_tool(spec)
            tools.append({"name": spec.name, "description": spec.description, "tier": spec.tier,
                          "title": spec.title or spec.name.replace("_", " ").capitalize(),
                          "summary": spec.summary or "",
                          "outside": spec.outside, "timeout": spec.timeout, "filler": spec.filler,
                          "filler_after": spec.filler_after,
                          "input_schema": t.parameters})
        return {
            "manifest": ext.manifest.model_dump(),
            "sdk": __version__,
            "settings": {"schema": schema, "secrets": self._secret_names()},
            "tools": tools,
            "jobs": [{"name": j.name, "trigger": j.trigger, "timeout": j.timeout, "catch_up": j.catch_up}
                     for j in ext.jobs.values()],
            "kinds": [{"kind": k.kind, "title": k.title, "description": k.description or "",
                       "checked": k.check is not None,
                       "deliver": to_json(k.deliver), "where": to_json(k.where),
                       "repeat": k.repeat.total_seconds() if k.repeat else None,
                       "may_be_urgent": k.may_be_urgent, "requested": k.requested, "outside": k.outside,
                       "triggers": k.triggers} for k in ext.kinds.values()],
            "actions": [{"name": a.name, "title": a.title, "kinds": a.kinds, "tier": a.tier}
                        for a in ext.actions.values()],
            "entities": [{"key": h.spec.key, "kind": h.spec.kind, "name": h.spec.name, "unit": h.spec.unit,
                          "device_class": h.spec.device_class, "state_class": h.spec.state_class,
                          "icon": h.spec.icon, "options": h.spec.options, "min": h.spec.min, "max": h.spec.max,
                          "step": h.spec.step, "per": to_json(h.spec.per) if h.spec.per else None,
                          "takes": ("press" if h.spec.on_press else "change" if h.spec.on_change else None)}
                         for h in ext.entities.values()],
            "has": {"status": ext.status_fn is not None, "context": ext.context_fn is not None,
                    "hint_words": ext.hint_words_fn is not None, "validate": ext.validate_fn is not None,
                    "on_nudge": bool(ext.nudge_listeners)},
        }

    async def close(self) -> None:
        for task in self._background:
            task.cancel()
        if self.ctx._http is not None:
            await self.ctx._http.aclose()
