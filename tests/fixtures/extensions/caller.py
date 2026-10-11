# /// script
# [tool.winston]
# id = "caller"
# name = "Caller"
# description = "Lists and calls the brain's tools and reads the diary, for the host's tests."
# api = 1
# permissions = { tools = "act", memory = "read" }
# ///
import json

from winston_sdk import Extension

ext = Extension()


@ext.tool(tier="read")
async def listing(ctx) -> list:
    """The brain's tools as ctx.tools.list gives them."""
    return [vars(t) for t in await ctx.tools.list()]


@ext.tool(tier="act")
async def use(ctx, name: str, args: str = "{}"):
    """Calls one of the brain's tools."""
    return await ctx.tools.call(name, json.loads(args))


@ext.tool(tier="read")
async def diary(ctx) -> list:
    """The diary of the last week."""
    return [{"day": str(e.day), "text": e.text} for e in await ctx.memory.diary(7)]
