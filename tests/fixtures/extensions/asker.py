# /// script
# [tool.winston]
# id = "asker"
# name = "Asker"
# description = "Asks the background model over and over, as a news extension judging stories would."
# api = 1
# permissions = { llm = true }
# ///
"""For tests/background_llm_voice.py: while `store["asking"]` is set, one question after another to the
background model, each a story of a few hundred words and a yes-or-no answer."""
import asyncio

from pydantic import BaseModel
from winston_sdk import Extension

ext = Extension()


class Verdict(BaseModel):
    yes: bool


STORY = ("A regional council met on Tuesday to discuss the budget for road repairs across the county. "
         "Several members argued that the winter damage was worse than expected, while others wanted the money "
         "spent on public transport instead. ") * 12


@ext.background
async def ask(ctx):
    n = 0
    while True:
        if not ctx.store.get("asking"):
            await asyncio.sleep(0.5)
            continue
        n += 1
        try:
            await ctx.llm.ask(f"Story {n}: {STORY}\n\nIs this story about a topic in [energy, Sweden, AI]?",
                              schema=Verdict)
            ctx.store["asked"] = ctx.store.get("asked", 0) + 1
        except Exception as e:  # noqa: BLE001
            ctx.log.warning(f"ask failed: {e}")
            await asyncio.sleep(1)


@ext.tool(tier="act", outside=False)
async def asking(ctx, on: bool) -> dict:
    """Start or stop asking.

    Args:
        on: Ask, or stop.
    """
    ctx.store["asking"] = on
    return {"asking": on, "asked": ctx.store.get("asked", 0)}
