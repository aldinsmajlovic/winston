"""winstond as an MCP server for other apps (Claude Code and the like), at /mcp on its own address.

It lists only the extension tools marked "Offer to other apps" on their cards, with their tiers as MCP
annotations, and calls them through the host like the brain does: a restricted one waits for you to
confirm it on the Talk page. Stateless, so there are no sessions to keep."""
from __future__ import annotations

import json
from typing import Any

import mcp.types as mt
from mcp.server.lowlevel import Server

from winston import __version__


def build(host) -> Server:
    async def list_tools(ctx: Any, params: Any) -> mt.ListToolsResult:
        return mt.ListToolsResult(tools=[
            mt.Tool(name=t["name"], description=t["description"], input_schema=t["parameters"] or {"type": "object"},
                    annotations=mt.ToolAnnotations(read_only_hint=t["tier"] == "read",
                                                   destructive_hint=t["tier"] == "restricted",
                                                   open_world_hint=t["outside"]))
            for t in host.offered_tools()])

    async def call_tool(ctx: Any, params: mt.CallToolRequestParams) -> mt.CallToolResult:
        tool = next((t for t in host.offered_tools() if t["name"] == params.name), None)
        if tool is None:
            return mt.CallToolResult(content=[mt.TextContent(type="text", text=f"No tool {params.name} is offered.")],
                                     is_error=True)
        r = await host.call_tool(tool["extension"], tool["tool"], dict(params.arguments or {}), channel="mcp")
        if r["ok"]:
            result = r["result"]
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
            return mt.CallToolResult(content=[mt.TextContent(type="text", text=text)], is_error=False)
        return mt.CallToolResult(content=[mt.TextContent(type="text", text=r["error"])], is_error=True)

    return Server("winston", version=__version__, title="Winston",
                  description="Tools from Winston's extensions that you chose to offer.",
                  on_list_tools=list_tools, on_call_tool=call_tool)
