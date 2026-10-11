# /// script
# [tool.winston]
# id = "later"
# name = "Later"
# description = "Things to do later instead of now: \"clean the kitchen when I leave\", \"turn the heater on at six\"."
# icon = "clock"
# api = 1
# permissions = { tools = "act", presence = true, home_assistant = "read" }
# ///
"""Later: an action the user asks for now and wants done on a trigger. The brain names the exact tool
call it would make now and when to make it; nothing is decided by a model when it runs.

Triggers: a time, the user leaving or getting home, or a Home Assistant entity reaching a state. When
it runs he is told it was done (or why it was not), on the speakers at home or the phone away. One
waiting on leaving, arriving or a state is dropped after a day, so a vacuum never starts days after
it was asked for. Restricted tools are never run (they wait for the user to confirm them)."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Literal

from winston_sdk import Extension, Nudge, When

ext = Extension()
ext.kind("done", title="Done as asked", requested=True, outside=True)
ext.kind("failed", title="Could not do it", requested=True, outside=True)
KEEP = timedelta(days=1)
ON = {"leave": "when you leave", "arrive": "when you get home"}


def _when(item: dict, now: datetime) -> str:
    if item.get("at"):
        at = datetime.fromisoformat(item["at"]).astimezone(now.tzinfo)
        day = "today" if at.date() == now.date() else "tomorrow" if at.date() == now.date() + timedelta(1) \
            else f"{at:%A} {at.day} {at:%B}"
        return f"{day} at {at:%H:%M}"
    if item.get("on"):
        return ON[item["on"]]
    return f"when {item['entity']} is {item['state']}"


async def _run(ctx, rid: str) -> None:
    item = ctx.store.get("items", {}).pop(rid, None)
    if item is None:
        return
    try:
        result = await ctx.tools.call(item["tool"], item["args"])
    except Exception as e:                                              # noqa: BLE001  (told, not raised)
        why = str(e)[:300] or type(e).__name__
        ctx.log.warning(f"{item['what']}: {why}")
        await ctx.nudge("failed", Nudge(about=rid, level=2, say=f"I could not {item['what']}, as you asked.",
                                        facts={"what": item["what"], "tool": item["tool"], "error": why}))
        return
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    await ctx.nudge("done", Nudge(about=rid, say=f"As you asked: {item['what']}, done.",
                                  facts={"what": item["what"], "tool": item["tool"], "result": text[:500]}))


@ext.tool(tier="act", outside=False)
async def add(ctx, what: str, tool: str, args: str = "{}", at: When | None = None,
              on: Literal["leave", "arrive"] | None = None, entity: str | None = None,
              state: str | None = None) -> str:
    """Do something later instead of now: call one of your other tools at a time, when the user leaves
    or gets home, or when a Home Assistant entity reaches a state. "Clean the kitchen when I leave",
    "turn the heater on at six", "when the washing machine is off, play some music". Give exactly one
    of at, on, or entity with state. The user is told when it is done.

    Args:
        what: What it does, in a few words: "clean the kitchen".
        tool: The tool to call then, by its exact name, as you would call it now.
        args: Its arguments as a JSON object, exactly as you would pass them now.
        at: When, as the user said it: "at six", "in 20 minutes", "tomorrow morning".
        on: "leave" for when the user leaves home, "arrive" for when the user gets home.
        entity: A Home Assistant entity id to watch; give state too.
        state: The state it should reach: "off", "idle", "docked".
    """
    now = ctx.now()
    what = what.strip().rstrip(".")
    if not 0 < len(what) <= 120:
        raise ValueError("say what it does in a few words")
    if sum(x is not None for x in (at, on, entity)) != 1 or (entity is None) != (state is None):
        raise ValueError("give exactly one trigger: at, on, or entity with state")
    try:
        call_args = json.loads(args or "{}")
    except ValueError as e:
        raise ValueError(f"args is not JSON: {e}") from e
    if not isinstance(call_args, dict):
        raise TypeError("args must be a JSON object")
    found = next((t for t in await ctx.tools.list() if t.name == tool), None)
    if found is None:
        raise ValueError(f"there is no tool {tool}; use the exact name of one of your tools")
    if found.tier == "restricted":
        raise ValueError(f"{tool} needs the user to confirm it, so it cannot be done later")
    item = {"what": what, "tool": tool, "args": call_args, "made": now.isoformat()}
    if at is not None:
        if at <= now:
            raise ValueError("that time has passed; ask for a later one")
        item["at"] = at.isoformat()
    elif on is not None:
        try:
            home = await ctx.presence.home()
        except Exception as e:                                          # no person entity set
            raise ValueError(f"cannot tell when the user leaves or gets home: {e}") from e
        if home != (on == "leave"):
            raise ValueError("the user is already away" if on == "leave" else "the user is already home")
        item["on"] = on
    else:
        current = await ctx.ha.state(entity)
        if current is None:
            raise ValueError(f"{entity} is not an entity he can see")
        if current.state.lower() == state.lower():
            raise ValueError(f"{entity} is already {state}; do it now instead")
        item.update(entity=entity, state=state)
    for rid, other in ctx.store.get("items", {}).items():         # asked twice (a retry): kept once
        if {k: v for k, v in other.items() if k != "made"} == {k: v for k, v in item.items() if k != "made"}:
            return f"Already waiting, {_when(other, now)}: {what}."
    rid = uuid.uuid4().hex[:8]
    ctx.store.setdefault("items", {})[rid] = item
    if at is not None:
        await ctx.later(at, "due", {"id": rid})
    return f"Will do, {_when(item, now)}: {what}."


@ext.job
async def due(ctx, event):
    await _run(ctx, event.data["id"])


@ext.on_leave
async def left(ctx, event):
    for rid in [r for r, i in ctx.store.get("items", {}).items() if i.get("on") == "leave"]:
        await _run(ctx, rid)


@ext.on_arrive
async def arrived(ctx, event):
    for rid in [r for r, i in ctx.store.get("items", {}).items() if i.get("on") == "arrive"]:
        await _run(ctx, rid)


# ponytail: polls watched entities every minute; a per-item state trigger in the standard if a minute is too slow
@ext.every("1m", catch_up=False)
async def watch(ctx):
    now = ctx.now()
    items = ctx.store.get("items", {})
    for rid, item in list(items.items()):
        if item.get("at"):
            continue
        if now - datetime.fromisoformat(item["made"]) > KEEP:
            ctx.log.info(f"dropped after a day: {item['what']} ({_when(item, now)})")
            del items[rid]
            continue
        if item.get("entity"):
            current = await ctx.ha.state(item["entity"])
            if current is not None and current.state.lower() == item["state"].lower():
                await _run(ctx, rid)


@ext.tool(tier="read", outside=False)
async def waiting(ctx) -> list[dict] | str:
    """What the user asked to have done later and is still waiting, soonest first."""
    now = ctx.now()
    items = sorted(ctx.store.get("items", {}).items(), key=lambda kv: kv[1].get("at") or "9")
    return [{"id": rid, "what": i["what"], "when": _when(i, now)} for rid, i in items] or "Nothing is waiting."


@ext.tool(tier="act", outside=False)
async def cancel(ctx, which: str) -> str:
    """Cancel something waiting to be done later.

    Args:
        which: Its id from waiting, or words from it ("the kitchen").
    """
    items = ctx.store.get("items", {})
    words = set(re.findall(r"\w+", which.lower())) - {"the", "a", "an", "to", "my"}
    found = [which] if which in items else \
        [rid for rid, i in items.items() if words and words <= set(re.findall(r"\w+", i["what"].lower()))]
    if not found:
        raise ValueError(f"nothing waiting matches {which!r}; check waiting")
    if len(found) > 1:
        raise ValueError("more than one matches: " + "; ".join(f"{r}: {items[r]['what']}" for r in found))
    item = items.pop(found[0])
    for t in await ctx.timers("due"):
        if (t.data or {}).get("id") == found[0]:
            await t.cancel()
    return f"Cancelled: {item['what']}."


@ext.status
async def status(ctx) -> str:
    items = ctx.store.get("items", {})
    if not items:
        return "Nothing waiting"
    first = min(items.values(), key=lambda i: i.get("at") or "9")
    return f"{len(items)} waiting; next: {first['what']} {_when(first, ctx.now())}"
