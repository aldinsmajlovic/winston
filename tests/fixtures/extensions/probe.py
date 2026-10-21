# /// script
# [tool.winston]
# id = "probe"
# name = "Probe"
# description = "Misbehaves on request, for the extension host's tests."
# api = 1
# permissions = { home_assistant = "read", presence = true }
# ///
import os
import time

from winston_sdk import Entity, Extension, Field, Secret, Settings, SettingsError

ext = Extension()


class ProbeSettings(Settings):
    key: str = Secret("PROBE_KEY", description="A made-up key.")
    pick: str | None = Entity(domain="light", default=None, description="A light it may read.")
    mode: str = Field("calm")


ext.settings(ProbeSettings)
hogged: list[bytes] = []


@ext.validate
async def check(ctx, event):
    if event.settings.mode == "bad":
        raise SettingsError("mode", "Bad is not a mode.")


@ext.tool(tier="read", outside=False)
async def seen(ctx) -> dict:
    """What it can see: its environment's variable names and its key."""
    return {"env": sorted(os.environ), "key": ctx.settings.key, "mode": ctx.settings.mode}


@ext.tool(tier="read")
async def state(ctx, entity_id: str) -> dict:
    """A Home Assistant state."""
    s = await ctx.ha.state(entity_id)
    return {"state": s.state if s else None}


@ext.tool(tier="read")
async def query(ctx, entity_id: str) -> dict:
    """A response-only service."""
    return await ctx.ha.query("weather", "get_forecasts", {"type": "daily"}, {"entity_id": entity_id})


@ext.tool(tier="act")
async def switch_off(ctx, entity_id: str) -> str:
    """Tries to switch something off; read permission only, so it is refused."""
    await ctx.ha.call("light", "turn_off", target={"entity_id": entity_id})
    return "done"


@ext.tool(tier="read", outside=False)
async def home(ctx) -> bool:
    """Whether the user is home."""
    return await ctx.presence.home()


@ext.tool(tier="read", outside=False)
async def die(ctx) -> str:
    """Ends its own process."""
    os._exit(3)


@ext.tool(tier="read", outside=False, timeout=5)
async def hog(ctx, mb: int) -> str:
    """Holds on to this many megabytes."""
    hogged.append(bytearray(mb * 1024 * 1024))
    for chunk in hogged:
        chunk[::4096] = b"x" * len(chunk[::4096])
    return f"holding {sum(len(c) for c in hogged) // 1024 // 1024} MB"


@ext.tool(tier="read", outside=False, timeout=2)
async def block(ctx, seconds: int) -> str:
    """Blocks its own event loop, as a careless synchronous call would."""
    time.sleep(seconds)                     # noqa: ASYNC251  (on purpose)
    return "woke"


@ext.tool(tier="restricted", outside=False)
async def wipe(ctx) -> str:
    """Forgets everything it holds."""
    ctx.store.clear()
    return "wiped"


@ext.tool(tier="read", outside=False)
async def long(ctx) -> str:
    """A result far too long for one turn."""
    return "x" * 10000
