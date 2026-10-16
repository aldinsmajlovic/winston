# /// script
# [tool.winston]
# id = "clock"
# name = "Clock"
# description = "One job per kind of trigger, for the scheduler's tests."
# api = 1
# permissions = { presence = true, home_assistant = "read" }
# ///
from datetime import timedelta

from winston_sdk import Entity, Extension, From, Settings

ext = Extension()


class ClockSettings(Settings):
    watch: str | None = Entity(domain="light", default=None, description="A light to watch.")


ext.settings(ClockSettings)


def bump(ctx, key):
    ctx.store[key] = ctx.store.get(key, 0) + 1


@ext.every("1h")
async def hourly(ctx):
    bump(ctx, "hourly")


@ext.job
async def later(ctx, event):
    ctx.store.setdefault("later", []).append(event.data)


@ext.on_state(From("watch"), to="off")
async def watched(ctx, event):
    ctx.store["watched"] = [event.entity_id, event.old.state, event.new.state]


@ext.on_arrive
async def arrived(ctx):
    bump(ctx, "arrived")


@ext.on_leave
async def left(ctx):
    bump(ctx, "left")


@ext.on_speakers_stop
async def silenced(ctx):
    bump(ctx, "silenced")


@ext.on_event("winston_test")
async def happened(ctx, event):
    ctx.store["event"] = event.data


@ext.tool(tier="read", outside=False)
async def store(ctx) -> dict:
    """Everything it has stored."""
    return dict(ctx.store)


@ext.tool(tier="act", outside=False)
async def remind(ctx, seconds: int) -> str:
    """Sets a timer that runs its later job."""
    timer = await ctx.later(timedelta(seconds=seconds), "later", data={"n": seconds})
    return timer.id


@ext.context
async def context(ctx):
    return f"The clock has ticked {ctx.store.get('hourly', 0)} times."


@ext.hint_words
async def words(ctx):
    return ["Hektar"]
