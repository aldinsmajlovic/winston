# /// script
# [tool.winston]
# id = "reminders"
# name = "Reminders"
# description = "Reminders you ask for: \"remind me to call Mum at six\", every weekday if you like."
# icon = "alarm-clock"
# api = 1
# permissions = {}
# ///
"""Reminders: made by asking, listed, cancelled, and said when their time comes.

Each time a reminder is due is one nudge of the requested kind "due", about the reminder's id and
that time, so putting one off, a restart or a dismissal needs nothing here: the core keeps the
nudge. A repeating reminder pushes its next time once the current one is said or done."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Literal

from winston_sdk import Extension, Nudge, When

ext = Extension()
ext.kind("due", title="Reminders", requested=True, outside=False,
         description="Says each reminder and timer you set when its time comes.")
next_one = ext.entity("next", kind="sensor", name="Next reminder", device_class="timestamp")

Repeat = Literal["daily", "weekdays", "weekly"]
SAYS = {"daily": "every day", "weekdays": "every weekday", "weekly": "every week"}


def _when(at: datetime, now: datetime) -> str:
    """"today at 18:00", "tomorrow at 09:00", "Friday 2 October at 07:30"."""
    day = "today" if at.date() == now.date() else "tomorrow" if at.date() == now.date() + timedelta(1) \
        else f"{at:%A} {at.day} {at:%B}"
    return f"{day} at {at:%H:%M}"


def _next(at: datetime, repeat: str, zone) -> datetime:
    """The time after `at` for a repeating reminder, at the same wall-clock time in the home's zone
    (daylight saving moves the instant, not the time on the clock)."""
    step = 7 if repeat == "weekly" else 1
    nxt = at.astimezone(zone).replace(tzinfo=None) + timedelta(days=step)
    while repeat == "weekdays" and nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt.replace(tzinfo=zone)


def _about(rid: str, at: datetime) -> str:
    return f"{rid}@{at.isoformat()}"


async def _push(ctx, rid: str) -> None:
    r = ctx.store["reminders"][rid]
    at = datetime.fromisoformat(r["at"])
    await ctx.nudge("due", Nudge(about=_about(rid, at), say=f"A reminder: {r['what']}.",
                                 facts={"what": r["what"], "at": r["at"], "repeat": r.get("repeat")},
                                 not_before=at))


async def _update_entity(ctx) -> None:
    upcoming = sorted(datetime.fromisoformat(r["at"]) for r in ctx.store.get("reminders", {}).values())
    await next_one.set(upcoming[0] if upcoming else None)


def _matching(ctx, which: str) -> list[str]:
    rems = ctx.store.get("reminders", {})
    if which in rems:
        return [which]
    words = set(re.findall(r"\w+", which.lower())) - {"the", "a", "an", "to", "my", "reminder", "about"}
    return [rid for rid, r in rems.items() if words and words <= set(re.findall(r"\w+", r["what"].lower()))]


@ext.tool(tier="act", outside=False, title="Add a reminder",
          summary="Sets a reminder or a timer, once or repeating, said when it is due.")
async def add(ctx, what: str, at: When, repeat: Repeat | None = None) -> str:
    """Set a reminder for the user, or a timer ("a timer for ten minutes" is a reminder in ten minutes): you
    say it at that time, wherever they are.

    Args:
        what: What to remind them of, in a few words: "call Mum", "take the bins out".
        at: When, as the user said it: "at six", "in 20 minutes", "tomorrow morning", "Friday 7:30". For a
            repeating one, only the first time ("at 7:30"); the days go in repeat.
        repeat: For one that comes back: "daily", "weekdays" or "weekly", from that time on.
    """
    now = ctx.now()
    what = what.strip().rstrip(".")
    if not 0 < len(what) <= 200:
        raise ValueError("say what to remind them of in a few words")
    if at <= now:
        raise ValueError(f"{_when(at, now)} has passed; ask for a later time")
    for rid, r in ctx.store.get("reminders", {}).items():         # asked twice (a retry): set once
        if r["what"].lower() == what.lower() and r["at"] == at.isoformat() and r.get("repeat") == repeat:
            return f"Already set for {_when(at, now)}: {what}."
    rid = uuid.uuid4().hex[:8]
    ctx.store.setdefault("reminders", {})[rid] = {"what": what, "at": at.isoformat(),
                                                  "repeat": repeat, "made": now.isoformat()}
    await _push(ctx, rid)
    await _update_entity(ctx)
    return f"Set for {_when(at, now)}{', then ' + SAYS[repeat] if repeat else ''}: {what}."


@ext.tool(tier="read", outside=False, title="List reminders",
          summary="Lists your reminders and timers still to come.")
async def upcoming(ctx) -> list[dict] | str:
    """The user's reminders that have not come yet, soonest first, with any that were put off."""
    now = ctx.now()
    held = {n.about: n for n in await ctx.nudges("due") if n.state in ("waiting", "held")}
    out = []
    for rid, r in sorted(ctx.store.get("reminders", {}).items(), key=lambda kv: kv[1]["at"]):
        at = datetime.fromisoformat(r["at"])
        n = held.get(_about(rid, at))
        entry = {"id": rid, "what": r["what"], "when": _when(at, now)}
        if r.get("repeat"):
            entry["repeats"] = SAYS[r["repeat"]]
        if n is not None and n.held_until:
            entry["put_off_until"] = _when(n.held_until, now)
        out.append(entry)
    return out or "No reminders set."


@ext.tool(tier="act", outside=False, title="Cancel a reminder",
          summary="Cancels a reminder or a timer, or only the next time of a repeating one.")
async def cancel(ctx, which: str, all_times: bool = True) -> str:
    """Cancel one of the user's reminders or timers.

    Args:
        which: Its id from upcoming, or words from it ("the dentist").
        all_times: For a repeating one: false cancels only its next time.
    """
    found = _matching(ctx, which)
    if not found:
        raise ValueError(f"no reminder matches {which!r}; check upcoming")
    if len(found) > 1:
        rems = ctx.store["reminders"]
        raise ValueError("more than one matches: " + "; ".join(f"{rid}: {rems[rid]['what']}" for rid in found))
    rid = found[0]
    r = ctx.store["reminders"][rid]
    at = datetime.fromisoformat(r["at"])
    await ctx.withdraw("due", _about(rid, at))
    if r.get("repeat") and not all_times:
        r["at"] = _next(at, r["repeat"], ctx.now().tzinfo).isoformat()
        await _push(ctx, rid)
        await _update_entity(ctx)
        return f"Skipped {_when(at, ctx.now())}; next {_when(datetime.fromisoformat(r['at']), ctx.now())}: {r['what']}."
    del ctx.store["reminders"][rid]
    await _update_entity(ctx)
    return f"Cancelled: {r['what']}."


@ext.on_nudge(kinds=["due"])
async def heard(ctx, event):
    """Said, dismissed or ended: a one-off is done, a repeating one moves on to its next time. Put
    off: it stays listed, and the core brings it back."""
    rid, _, at_text = event.nudge.about.partition("@")
    r = ctx.store.get("reminders", {}).get(rid)
    if r is None or r["at"] != at_text or event.what in ("put_off", "muted"):
        return
    if r.get("repeat"):
        zone = ctx.now().tzinfo
        nxt = _next(datetime.fromisoformat(r["at"]), r["repeat"], zone)
        while nxt <= ctx.now():                     # long down: the next time still to come
            nxt = _next(nxt, r["repeat"], zone)
        r["at"] = nxt.isoformat()
        await _push(ctx, rid)
    else:
        del ctx.store["reminders"][rid]
    await _update_entity(ctx)


@ext.status
async def status(ctx) -> str:
    rems = ctx.store.get("reminders", {})
    if not rems:
        return "No reminders"
    r = min(rems.values(), key=lambda v: v["at"])
    return f"{len(rems)} set; next {_when(datetime.fromisoformat(r['at']), ctx.now())}: {r['what']}"
