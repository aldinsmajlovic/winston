# /// script
# [tool.winston]
# id = "trainer"
# name = "Trainer"
# description = "A stand-in for Hevy in tests: whether you trained is set by a tool, not a web service."
# icon = "dumbbell"
# api = 1
# permissions = {}
# ///
"""Speaking up through winstond: a checked kind with an action of its own (one restricted), a pushed
kind, and a record of what happened to its nudges."""
from winston_sdk import Extension, Nudge

ext = Extension()
ext.kind("news", title="A story", deliver="briefing")


@ext.nudge("rest", title="Time to train", deliver="when_home", repeat="1d")
async def rest(ctx):
    gap = ctx.store.get("gap")
    if not gap:
        return None
    return Nudge(about=gap["since"], level=2 if gap["days"] >= 5 else 1,
                 say=f"It has been {gap['days']} days since your last workout, sir.",
                 facts={"days_since": gap["days"], "last_workout": gap["since"]})


@ext.tool(tier="act", outside=False)
async def set_gap(ctx, days: int, since: str = "2026-09-26T18:00") -> str:
    """Days since the last workout; 0 means he trained.

    Args:
        days: Days since the last workout.
        since: When the last workout started.
    """
    ctx.store["gap"] = {"days": days, "since": since} if days else None
    return "set"


@ext.tool(tier="act", outside=False)
async def story(ctx, about: str, say: str) -> str:
    """Pushes a story for the briefing.

    Args:
        about: The story's link.
        say: Its line.
    """
    await ctx.nudge("news", Nudge(about=about, say=say))
    return "pushed"


@ext.action("Log a rest day", kinds=["rest"])
async def rest_day(ctx, event):
    ctx.store["rest_days"] = ctx.store.get("rest_days", 0) + 1
    ctx.store["gap"] = None
    return "Logged a rest day."


@ext.action("Cancel the gym booking", kinds=["rest"], tier="restricted")
async def cancel_gym(ctx, event):
    ctx.store["cancelled"] = True
    return "Cancelled."


@ext.on_nudge
async def heard(ctx, event):
    ctx.store["events"] = [*ctx.store.get("events", []), [event.what, event.nudge.about]]


@ext.tool(tier="read", outside=False)
async def events(ctx) -> dict:
    """What happened to its nudges, and its rest days."""
    return {"events": ctx.store.get("events", []), "rest_days": ctx.store.get("rest_days", 0),
            "cancelled": ctx.store.get("cancelled", False)}
