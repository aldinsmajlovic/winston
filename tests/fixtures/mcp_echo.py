"""A plain MCP server (the mcp SDK, no Winston SDK), for the extension host's tests: added in config as
a tools-only extension."""
import os

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

srv = MCPServer("echo")


@srv.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
async def echo(text: str) -> str:
    """Says the text back."""
    return text


@srv.tool()
async def delete_all() -> str:
    """Deletes everything (no annotations, so it counts as destructive)."""
    return "deleted"


@srv.tool(annotations=ToolAnnotations(read_only_hint=True))
async def env() -> str:
    """The names in its environment."""
    return ",".join(sorted(os.environ))


@srv.tool(annotations=ToolAnnotations(read_only_hint=True))
async def hidden() -> str:
    """Not on the allowlist, so never offered."""
    return "seen"


srv.run("stdio")
