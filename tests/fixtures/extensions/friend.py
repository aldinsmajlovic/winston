# /// script
# [tool.winston]
# id = "friend"
# name = "Friend"
# description = "Uses memory, the model, a quota and another extension, for the host's tests."
# api = 1
# permissions = { memory = "write", llm = true, extensions = ["probe"] }
# ///
from pydantic import BaseModel

from winston_sdk import Extension

ext = Extension()


class Verdict(BaseModel):
    yes: bool


@ext.tool(tier="act", outside=False)
async def note(ctx, text: str) -> str:
    """Remembers a fact about the home."""
    return await ctx.memory.remember(text, "home")


@ext.tool(tier="read", outside=False)
async def notes(ctx) -> list:
    """The facts about the home."""
    return [f.text for f in await ctx.memory.facts("home")]


@ext.tool(tier="read", outside=False)
async def ask(ctx, question: str) -> bool:
    """Asks the background model a yes-or-no question."""
    return (await ctx.llm.ask(question, schema=Verdict)).yes


@ext.tool(tier="read", outside=False)
async def spend(ctx) -> bool:
    """Takes one of two uses a day."""
    return await ctx.quota("calls", per_day=2)


@ext.tool(tier="read")
async def peek(ctx, tool: str) -> dict:
    """Calls one of probe's tools."""
    return await ctx.ext("probe").call(tool)


@ext.tool(tier="read")
async def lights(ctx) -> dict:
    """Tries Home Assistant, which it has no permission for."""
    return {"state": (await ctx.ha.state("light.taklampa")).state}
