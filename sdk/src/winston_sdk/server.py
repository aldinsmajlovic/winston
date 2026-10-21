"""The extension as an MCP server over stdio. Tools are plain MCP tools, so any MCP client can use
them; the rest travels on Winston's extension to the protocol (SEP-2133), `winston/*` methods that
winstond calls: configure, run, check, act, nudge_event, entity, hook, status, context, hint_words,
choices and info. Each is the Runtime method of the same name, its params that method's arguments."""
from __future__ import annotations

from typing import Any

from mcp.server.extension import Extension as MCPExtension
from mcp.server.extension import MethodBinding
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
import mcp.types as mt
from mcp.types import INVALID_PARAMS, RequestParams
from pydantic import ConfigDict, ValidationError, validate_call

from winston_sdk._version import __version__
from winston_sdk.runtime import Runtime

IDENTIFIER = "io.github.aldinsmajlovic/winston"
PROTOCOL_VERSION = 1
METHODS = ("configure", "run", "check", "act", "nudge_event", "entity", "hook", "status", "context", "hint_words",
           "choices", "info")


class Params(RequestParams):
    """The params as sent, under their own names, for the method to take as arguments."""
    model_config = ConfigDict(extra="allow")


class WinstonProtocol(MCPExtension):
    identifier = IDENTIFIER

    def __init__(self, runtime: Runtime):
        self.rt = runtime

    def settings(self) -> dict[str, Any]:
        return {"version": PROTOCOL_VERSION, "sdk": __version__}

    def methods(self) -> list[MethodBinding]:
        def bind(name: str) -> MethodBinding:
            # Checked against the method's signature: a missing or mistyped param is refused by name, and
            # one it does not take is ignored, so a newer winstond can add params.
            fn = validate_call(getattr(self.rt, name), config=ConfigDict(extra="ignore"))

            async def handler(ctx, p: Params):
                try:
                    return await fn(**(p.model_extra or {}))
                except ValidationError as e:
                    problems = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
                    raise MCPError(INVALID_PARAMS, f"winston/{name}: {problems}") from e
            return MethodBinding(f"winston/{name}", Params, handler)
        return [bind(name) for name in METHODS]


def request(method: str, **params: Any) -> mt.Request:
    """A `winston/<method>` request, for `ClientSession.send_request` (winstond and tests)."""
    return mt.Request[dict[str, Any] | None, str](method=f"winston/{method}", params=params or None)


def build(runtime: Runtime) -> MCPServer:
    m = runtime.ext.manifest
    return MCPServer(m.id, title=m.name, description=m.description or None, version=__version__,
                     tools=[runtime.mcp_tool(spec) for spec in runtime.ext.tools.values()],
                     extensions=[WinstonProtocol(runtime)], log_level="WARNING")
