# /// script
# [tool.winston]
# id = "everything"
# name = "Everything"
# description = "Uses every part of the SDK, for its tests."
# api = 1
# permissions = { network = ["example.com"], home_assistant = "act", memory = "write", presence = true, llm = true, extensions = ["other"] }
# ///
import asyncio
from datetime import time, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel

from winston_sdk import (Choice, Choices, Duration, Entity, Extension, Field, From, Model, Nudge, Secret, Settings,
                         SettingsError, TimeOfDay, Topic, When)

ext = Extension()


class Chore(Model):
    every_days: int = Field(7, ge=1, description="How often it is due.")
    at: time | None = TimeOfDay(None, description="A preferred time of day.")


class Everything(Settings):
    key: str = Secret("EVERYTHING_KEY", description="A made-up key.")
    heater: str | None = Entity(domain=["switch", "climate"], default=None, description="Optional heater.")
    topic: str = Topic(default="home")
    remind_at: time = TimeOfDay("07:30")
    wait: timedelta = Duration("1h30m")
    mode: Literal["calm", "keen"] = Field("calm")
    chores: dict[str, Chore] = Field(default_factory=dict)
    words: list[str] = Field(default_factory=list, level="advanced")    # deprecated: still accepted, and ignored
    pick: str | None = Field(None, choices="moods")
    nicknames: dict[str, str] = Field(default_factory=dict, key_choices="moods")


ext.settings(Everything)
done = ext.entity("done", kind="button", name="{entry} done", per=From("chores"))
level = ext.entity("level", kind="number", name="Level", min=0, max=10, step=1)
count = ext.entity("count", kind="sensor", name="Count", state_class="measurement")


class Answer(BaseModel):
    yes: bool


@ext.tool(tier="act", outside=False, timeout=2)
async def mark(ctx, chore: Annotated[str, Choices(From("chores"))], when: When | None = None) -> dict:
    """Mark a chore as done.

    Args:
        chore: Which chore.
        when: When it was done, if not now.
    """
    at = when or ctx.now()
    ctx.store.setdefault("done", {})[chore] = at.isoformat()
    await count.set(len(ctx.store["done"]))
    return {"chore": chore, "at": at.isoformat()}


@ext.tool(tier="read", outside=False, timeout=1)
async def slow(ctx) -> str:
    """Takes too long, on purpose."""
    await asyncio.sleep(3)
    return "never"


@ext.tool(tier="read")
async def fetch(ctx, host: str) -> int:
    """Fetches a page.

    Args:
        host: example.com or elsewhere.
    """
    r = await ctx.http.get(f"https://{host}/page")
    return r.status_code


@ext.tool(tier="restricted", outside=False)
async def wipe(ctx) -> str:
    """Forgets every chore done."""
    ctx.store["done"] = {}
    return "wiped"


@ext.every("15m", align=True)
async def tick(ctx):
    ctx.store["ticks"] = ctx.store.get("ticks", 0) + 1


@ext.at(From("remind_at"), days="mon-fri")
async def morning(ctx, event):
    ctx.store["morning"] = event.time.isoformat()


@ext.cron("0 7 * * 1")
async def weekly(ctx):
    ctx.store["weekly"] = True


@ext.job
async def later(ctx, event):
    ctx.store["later"] = event.data


@ext.on_state(From("heater"), to="on")
async def heater_on(ctx, event):
    ctx.store["heater"] = [event.entity_id, event.old.state if event.old else None, event.new.state]


@ext.on_event("winston_test")
async def on_test(ctx, event):
    ctx.store["event"] = event.data


@ext.on_arrive
async def arrive(ctx):
    ctx.store["arrived"] = True


@ext.on_start
async def start(ctx):
    ctx.store["started"] = ctx.store.get("started", 0) + 1


@ext.on_settings
async def changed(ctx, event):
    ctx.store["old_mode"] = event.old.mode


@ext.hook("ping")
async def ping(ctx, event):
    return {"pong": event.json}


@ext.background
async def forever(ctx):
    ctx.store["background"] = True
    await asyncio.sleep(3600)


@ext.nudge("due", title="A chore is due", deliver={1: "next_talk", 2: "when_home"}, repeat="1d", every="1h")
async def due(ctx):
    out = []
    for name in ctx.settings.chores:
        last = ctx.store.get("done", {}).get(name)
        if last is None:
            out.append(Nudge(about=name, say=f"The {name} is due, sir.", facts={"chore": name}, actions=["done_it"]))
    return out


ext.kind("told", title="Things you asked for", requested=True, outside=False)


@ext.action("It is done", kinds=["due"])
async def done_it(ctx, event):
    ctx.store.setdefault("done", {})[event.nudge.about] = ctx.now().isoformat()
    return f"Marked {event.nudge.about} done."


@ext.on_nudge(kinds=["due", "told"])
async def heard(ctx, event):
    ctx.store.setdefault("heard", []).append([event.what, event.nudge.about])


@done.on_press
async def pressed(ctx, event):
    ctx.store.setdefault("done", {})[event.entry] = ctx.now().isoformat()


@level.on_change
async def level_changed(ctx, event):
    ctx.store["level"] = event.value


@ext.status
async def status(ctx):
    return f"{len(ctx.store.get('done', {}))} done"


@ext.context
async def context(ctx):
    return "x" * 400


@ext.hint_words
async def hints(ctx):
    return list(ctx.settings.chores)


@ext.choices
async def moods(ctx):
    return ["calm", Choice(value="keen", hint="Eager", image="https://example.com/keen.png")]


@ext.validate
async def check_key(ctx, event):
    if event.settings.key == "bad":
        raise SettingsError("key", "That key was refused.")


async def remember_and_ask(ctx):
    await ctx.memory.remember("The bins go out on Mondays.", "home")
    facts = await ctx.memory.facts("home")
    answer = await ctx.llm.ask("Is it Monday?", schema=Answer)
    home = await ctx.presence.home()
    other = await ctx.ext("other").call("lookup", q="x")
    return facts, answer, home, other
