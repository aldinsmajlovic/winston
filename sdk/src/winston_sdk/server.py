"""The extension as an MCP server over stdio. Tools are plain MCP tools, so any MCP client can use
them; the rest travels on Winston's extension to the protocol (SEP-2133), `winston/*` methods that
winstond calls: describe, configure, run, check, act, nudge_event, entity, hook, status, context,
hint_words, errors and info."""
from __future__ import annotations

import os
from typing import Any

from mcp.server.extension import Extension as MCPExtension
from mcp.server.extension import MethodBinding
from mcp.server.mcpserver import MCPServer
import mcp.types as mt
from mcp.types import RequestParams
from pydantic import ConfigDict

from winston_sdk._version import __version__
from winston_sdk.runtime import Runtime

IDENTIFIER = "io.github.aldinsmajlovic/winston"
PROTOCOL_VERSION = 1


class Params(RequestParams):
    """Parameters travel under their own names: MCP's base class would read only camelCase ones."""
    model_config = ConfigDict(alias_generator=None)


class Empty(Params):
    pass


class ConfigureParams(Params):
    settings: dict[str, Any] = {}
    secrets: dict[str, str] = {}
    validate_only: bool = False
    validate_first: bool = False
    day_parts: dict[str, str] | None = None
    time_zone: str | None = None


class RunParams(Params):
    job: str
    data: dict[str, Any] = {}


class CheckParams(Params):
    kind: str


class ActParams(Params):
    action: str
    nudge: dict[str, Any]


class NudgeEventParams(Params):
    what: str
    nudge: dict[str, Any]
    until: str | None = None
    where: str | None = None


class EntityParams(Params):
    key: str
    entry: str | None = None
    value: Any = None
    press: bool = False


class HookParams(Params):
    name: str
    body: Any = None
    headers: dict[str, str] = {}


class WinstonProtocol(MCPExtension):
    identifier = IDENTIFIER

    def __init__(self, runtime: Runtime):
        self.rt = runtime

    def settings(self) -> dict[str, Any]:
        return {"version": PROTOCOL_VERSION, "sdk": __version__}

    def methods(self) -> list[MethodBinding]:
        rt = self.rt

        async def describe(ctx, p: Empty):
            return rt.describe()

        async def configure(ctx, p: ConfigureParams):
            return await rt.configure(p.settings, p.secrets, validate=p.validate_first or p.validate_only,
                                      apply=not p.validate_only, day_parts=p.day_parts, time_zone=p.time_zone)

        async def run(ctx, p: RunParams):
            return await rt.run(p.job, p.data)

        async def check(ctx, p: CheckParams):
            return await rt.check(p.kind)

        async def act(ctx, p: ActParams):
            return await rt.act(p.action, p.nudge)

        async def nudge_event(ctx, p: NudgeEventParams):
            return await rt.nudge_event(p.what, p.nudge, p.until, p.where)

        async def entity(ctx, p: EntityParams):
            return await rt.entity(p.key, p.entry, p.value, p.press)

        async def hook(ctx, p: HookParams):
            return await rt.hook(p.name, p.body, p.headers)

        async def status(ctx, p: Empty):
            return {"text": await rt.status()}

        async def context(ctx, p: Empty):
            return {"text": await rt.context()}

        async def hint_words(ctx, p: Empty):
            return {"words": await rt.hint_words()}

        async def errors(ctx, p: Empty):
            return {"errors": rt.errors}

        async def info(ctx, p: Empty):
            return {"pid": os.getpid(), "sdk": __version__, "id": rt.ext.manifest.id}

        table = [("describe", Empty, describe), ("configure", ConfigureParams, configure), ("run", RunParams, run),
                 ("check", CheckParams, check), ("act", ActParams, act),
                 ("nudge_event", NudgeEventParams, nudge_event), ("entity", EntityParams, entity),
                 ("hook", HookParams, hook), ("status", Empty, status), ("context", Empty, context),
                 ("hint_words", Empty, hint_words), ("errors", Empty, errors), ("info", Empty, info)]
        return [MethodBinding(f"winston/{name}", params, handler) for name, params, handler in table]


def request(method: str, **params: Any) -> mt.Request:
    """A `winston/<method>` request, for `ClientSession.send_request` (winstond and tests)."""
    return mt.Request[dict[str, Any] | None, str](method=f"winston/{method}", params=params or None)


def build(runtime: Runtime) -> MCPServer:
    m = runtime.ext.manifest
    return MCPServer(m.id, title=m.name, description=m.description or None, version=__version__,
                     tools=[runtime.mcp_tool(spec) for spec in runtime.ext.tools.values()],
                     extensions=[WinstonProtocol(runtime)], log_level="WARNING")
