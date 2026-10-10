"""Settings and the field helpers that draw them.

A settings model is a Pydantic model. The helpers add the hints Winston's settings form already
reads (`level`, `options` with `options_query`, `secret`, `widget`), so an extension's card is drawn
by the same form as the rest of settings. Times and durations are kept as the text you type
("07:00", "30m") and read into `datetime.time` and `timedelta` when the settings load."""
from __future__ import annotations

import re
import types
import typing
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

import pydantic
from pydantic import BaseModel, ConfigDict, GetJsonSchemaHandler, model_validator
from pydantic_core import CoreSchema

_UNSET: Any = ...
_DURATION = re.compile(r"^\s*(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?\s*$")


def parse_duration(value: str | int | float | timedelta) -> timedelta:
    """"90s", "15m", "1h30m", "1d", seconds as a number, or a timedelta."""
    if isinstance(value, timedelta):
        return value
    if isinstance(value, (int, float)):
        return timedelta(seconds=value)
    m = _DURATION.match(value or "")
    if not m or not any(m.groups()):
        raise ValueError(f"not a duration: {value!r} (use forms like 90s, 15m, 1h30m, 1d)")
    d, h, mi, s = (int(g or 0) for g in m.groups())
    return timedelta(days=d, hours=h, minutes=mi, seconds=s)


def format_duration(td: timedelta) -> str:
    total = int(td.total_seconds())
    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if total >= size:
            parts.append(f"{total // size}{unit}")
            total %= size
    return "".join(parts) or "0s"


def parse_time_of_day(value: str | time) -> time:
    if isinstance(value, time):
        return value
    return time.fromisoformat(value.strip())


def _ui(level: str, **extra: Any) -> dict[str, Any]:
    return {"level": level, **{k: v for k, v in extra.items() if v is not None}}


def Field(default: Any = _UNSET, *, title: str | None = None, description: str | None = None,
          level: str = "basic", placeholder: str | None = None, **kwargs: Any) -> Any:
    """Pydantic's Field with `level` ("basic" or "advanced", which puts it behind Advanced) and
    `placeholder`, what the form shows while it is empty ("None: every county")."""
    return pydantic.Field(default, title=title, description=description,
                          json_schema_extra=_ui(level, placeholder=placeholder), **kwargs)


def Secret(var: str, *, title: str | None = "Key variable", description: str | None = None,
           label: str = "Key") -> Any:
    """A key. The card draws a key field and stores the key in data/.env under `var`; the config
    keeps the variable's name. In code the field holds the key itself."""
    return pydantic.Field(var, title=title, description=description,
                          json_schema_extra=_ui("advanced", secret={"default": var, "label": label}))


def Entity(domain: str | list[str] | None = None, *, device_class: str | None = None, default: Any = _UNSET,
           title: str | None = None, description: str | None = None, level: str = "basic") -> Any:
    """A picker of Home Assistant entities. Picking one is consent for the extension to use it."""
    query: dict[str, str] = {}
    if domain:
        query["domain"] = ",".join(domain) if isinstance(domain, (list, tuple)) else domain
    if device_class:
        query["device_class"] = device_class
    return pydantic.Field(default, title=title, description=description,
                          json_schema_extra=_ui(level, options="ha_entities", options_query=query or None,
                                                entity=True))


def Extensions(tool: str, *, default: Any = _UNSET, title: str | None = None, description: str | None = None,
               level: str = "basic") -> Any:
    """A picker of the switched-on extensions and MCP servers that have a read tool named `tool`; the
    value is their ids (`list[str]`, or `str` for one). Picking one is consent for the extension to call
    that tool through `ctx.ext(id).call(tool)`."""
    return pydantic.Field(default, title=title, description=description,
                          json_schema_extra=_ui(level, options="extension_tools", options_query={"tool": tool},
                                                extension_tool=tool))


def _deref(node: dict, defs: dict) -> dict:
    while "$ref" in node:
        node = defs.get(node["$ref"].rsplit("/", 1)[-1], {})
    return node


def picked(settings_schema: dict[str, Any], settings: Any, marker: str) -> list[tuple[Any, str]]:
    """(the marker's value, a value picked) for each field whose schema carries `marker` (`entity` for
    Entity, `extension_tool` for Extensions), at any depth: one plant's sensor in a dict of plants counts.
    winstond and the test harness both read consent this way."""
    defs, out = settings_schema.get("$defs", {}), []

    def walk(node: dict, value: Any) -> None:
        node = _deref(node, defs)
        if node.get(marker):
            for v in value if isinstance(value, list) else [value]:
                if isinstance(v, str) and v:
                    out.append((node[marker], v))
        for option in node.get("anyOf", []):
            walk(option, value)
        if isinstance(value, dict):
            props, extra = node.get("properties", {}), node.get("additionalProperties")
            for k, v in value.items():
                if k in props:
                    walk(props[k], v)
                elif isinstance(extra, dict):
                    walk(extra, v)
        elif isinstance(value, list) and isinstance(node.get("items"), dict):
            for v in value:
                walk(node["items"], v)

    walk(settings_schema, settings)
    return out


def Topic(*, default: Any = _UNSET, title: str | None = None, description: str | None = None,
          level: str = "basic") -> Any:
    """A picker of memory topics."""
    return pydantic.Field(default, title=title, description=description,
                          json_schema_extra=_ui(level, options="memory_topics"))


def _as_text(widget: str, default_text: str | None):
    def extra(schema: dict[str, Any]) -> None:
        schema.pop("format", None)
        schema["type"] = "string"
        schema["widget"] = widget
        if default_text is not None:
            schema["default"] = default_text
    return extra


def _with(level: str, fn):
    def extra(schema: dict[str, Any]) -> None:
        fn(schema)
        schema.update(_ui(level))
    return extra


def TimeOfDay(default: str | time | None | Any = _UNSET, *, title: str | None = None,
              description: str | None = None, level: str = "basic") -> Any:
    """A time of day ("07:00"); a `datetime.time` in code."""
    value = default if default is _UNSET or default is None else parse_time_of_day(default)
    text = None if value is _UNSET or value is None else value.strftime("%H:%M")
    return pydantic.Field(value, title=title, description=description,
                          json_schema_extra=_with(level, _as_text("time", text)))


def Duration(default: str | timedelta | None | Any = _UNSET, *, title: str | None = None,
             description: str | None = None, level: str = "basic") -> Any:
    """A duration ("30m", "1h30m", "2d"); a `timedelta` in code."""
    value = default if default is _UNSET or default is None else parse_duration(default)
    text = None if value is _UNSET or value is None else format_duration(value)
    return pydantic.Field(value, title=title, description=description,
                          json_schema_extra=_with(level, _as_text("duration", text)))


def _base(annotation: Any) -> Any:
    """`timedelta` for `timedelta | None` and the like."""
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return args[0] if len(args) == 1 else annotation
    return annotation


def _sentence(name: str, *_: Any) -> str:
    """Titles in sentence case, as the rest of Winston's settings: rest_days is "Rest days"."""
    return name.replace("_", " ").capitalize()


class Model(BaseModel):
    """An entry of a `dict` setting (one plant, one chore). Same helpers as `Settings`."""
    model_config = ConfigDict(extra="ignore", field_title_generator=_sentence)

    @model_validator(mode="before")
    @classmethod
    def _friendly_values(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for name, field in cls.model_fields.items():
            value = out.get(name)
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                continue
            base = _base(field.annotation)
            if base is timedelta:
                out[name] = parse_duration(value)
            elif base is time and isinstance(value, str):
                out[name] = parse_time_of_day(value)
        return out


class Settings(Model):
    """An extension's settings: subclass it and pass it to `ext.settings(...)`."""


def secret_fields(model: type[BaseModel]) -> dict[str, str]:
    """Field name -> the variable it defaults to, for every `Secret` field."""
    out = {}
    for name, field in model.model_fields.items():
        extra = field.json_schema_extra if isinstance(field.json_schema_extra, dict) else {}
        if "secret" in extra:
            out[name] = extra["secret"]["default"]
    return out


@dataclass(frozen=True)
class From:
    """A setting's value, wherever a decorator takes one: `@ext.at(From("remind_at"))`."""
    name: str

    def to_json(self) -> dict[str, str]:
        return {"from": self.name}


def to_json(value: Any) -> Any:
    """A decorator argument as it travels to the core: `From` as {"from": name}, times as text."""
    if isinstance(value, From):
        return value.to_json()
    if isinstance(value, timedelta):
        return format_duration(value)
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, (list, tuple)):
        return [to_json(v) for v in value]
    return value


class Choices:
    """`Annotated[str, Choices(From("chores"))]`: a tool argument limited to the names of a `dict`
    setting. The core fills in the names when it offers the tool to the brain."""

    def __init__(self, source: From):
        self.source = source

    def __get_pydantic_json_schema__(self, core_schema: CoreSchema, handler: GetJsonSchemaHandler) -> dict:
        schema = handler(core_schema)
        schema["x-winston-choices"] = self.source.to_json()
        return schema


class When(datetime):
    """A tool argument for a time, as the user said it ("at six", "in 20 minutes", "tomorrow
    morning"). The brain passes the words; the extension gets an aware datetime."""
