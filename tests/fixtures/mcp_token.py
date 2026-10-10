"""A plain MCP server over Streamable HTTP that wants a bearer token, for the extension host's tests:
added in config by URL with `token_env`. A missing or wrong token gets 401 before MCP sees the request.

    python tests/fixtures/mcp_token.py PORT TOKEN
"""
import secrets
import sys

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

port, token = int(sys.argv[1]), sys.argv[2]
srv = MCPServer("remote", instructions="A remote machine for the host's tests: its status, and a poke.")


@srv.tool(annotations=ToolAnnotations(read_only_hint=True))
async def status() -> dict:
    """How the remote machine is doing."""
    return {"up": True}


@srv.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
async def poke() -> str:
    """Changes something small (an act tool)."""
    return "poked"


def guarded(app):
    want = f"Bearer {token}".encode()

    async def asgi(scope, receive, send):
        if scope["type"] == "http":
            got = dict(scope["headers"]).get(b"authorization", b"")
            if not secrets.compare_digest(got, want):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"token refused"})
                return
        await app(scope, receive, send)
    return asgi


uvicorn.run(guarded(srv.streamable_http_app()), host="127.0.0.1", port=port, log_level="warning")
