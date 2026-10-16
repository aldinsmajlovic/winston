# /// script
# [tool.winston]
# id = "agenda"
# name = "Agenda"
# description = "Your calendars and to-do lists from Home Assistant: a word before each event, and the day's events and tasks in the morning briefing."
# icon = "calendar"
# api = 1
# permissions = { home_assistant = "read" }
# ///
"""Agenda: the calendars Home Assistant has (Google Calendar, CalDAV, a local one), read through its
`calendar.get_events`, so Home Assistant handles accounts and repeating events, and the open tasks
with a due date in its to-do lists (Google Tasks, a local list), through `todo.get_items`. A task is
read like an event at its due time; one due on a day only is like an all-day event, and one overdue
stays until it is done.

Every 10 minutes it keeps the events from the start of today to 36 hours ahead. Two kinds:

- "soon": each timed event, held until `before` its start and checked again then, so a moved or
  cancelled event is never said. Asked for (you put it in your calendar), so it comes through quiet
  hours and the daily limit.
- "today": the day's events, and the tasks due today or overdue, for the morning briefing."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from winston_sdk import Duration, Entity, Extension, Field, Nudge, Settings

ext = Extension()
AHEAD = timedelta(hours=36)


class AgendaSettings(Settings):
    calendars: list[str] = Entity(domain="calendar", default=[],
                                  description="The calendars he reads. Picking one lets him read it, exposed or not.")
    lists: list[str] = Entity(domain="todo", default=[], title="To-do lists",
                              description="Lists whose open tasks with a due date he reads, like events at their "
                                          "due time; due today or overdue, in the briefing.")
    before: timedelta = Duration("30m", description="How long before an event he mentions it.")
    all_day: bool = Field(True, title="All-day events in the briefing",
                          description="Name all-day events (birthdays, holidays) in the morning briefing.")


ext.settings(AgendaSettings)


def _start_of(day: date, zone) -> datetime:
    return datetime.combine(day, time(), zone)


def _parse(value: str, zone) -> tuple[datetime, bool]:
    """An event's start or end as an aware datetime, and whether it is a whole day ("2026-10-12")."""
    if "T" not in value:
        return _start_of(date.fromisoformat(value), zone), True
    at = datetime.fromisoformat(value)
    return (at if at.tzinfo else at.replace(tzinfo=zone)), False


def _when(at: datetime, all_day: bool, now: datetime, task: bool = False) -> str:
    """"today 14:00", "tomorrow, all day", "Friday 16 October 09:30"; a task "today", "overdue since ..."."""
    day = "today" if at.date() == now.date() else "tomorrow" if at.date() == now.date() + timedelta(1) \
        else f"{at:%A} {at.day} {at:%B}"
    if task and at.date() < now.date():
        return f"overdue since {day}"
    return (day if task else f"{day}, all day") if all_day else f"{day} {at:%H:%M}"


def _named(e: dict) -> dict:
    return {"task" if e.get("task") else "event": e["summary"]}


async def _events(ctx, start: datetime, end: datetime) -> list[dict]:
    """Every picked calendar's events between start and end, and every picked list's open tasks due
    before end (overdue ones too), soonest first."""
    s, zone = ctx.settings, ctx.now().tzinfo
    out = []
    if s.calendars:
        answer = await ctx.ha.query("calendar", "get_events", target={"entity_id": s.calendars},
                                    data={"start_date_time": start.isoformat(), "end_date_time": end.isoformat()})
        for cal, found in (answer or {}).items():
            for e in (found or {}).get("events") or []:
                if not e.get("start") or not e.get("summary"):
                    continue
                at, all_day = _parse(e["start"], zone)
                out.append({"calendar": cal, "summary": e["summary"][:200], "start": at.isoformat(),
                            "all_day": all_day, "location": (e.get("location") or "")[:200]})
    if s.lists:
        answer = await ctx.ha.query("todo", "get_items", target={"entity_id": s.lists},
                                    data={"status": ["needs_action"]})
        for lst, found in (answer or {}).items():
            for t in (found or {}).get("items") or []:
                if not t.get("due") or not t.get("summary"):
                    continue
                at, all_day = _parse(t["due"], zone)
                if at < end:
                    out.append({"calendar": lst, "summary": t["summary"][:200], "start": at.isoformat(),
                                "all_day": all_day, "location": "", "task": True})
    return sorted(out, key=lambda e: e["start"])


@ext.every("10m")
async def read(ctx):
    now = ctx.now()
    ctx.store["events"] = await _events(ctx, _start_of(now.date(), now.tzinfo), now + AHEAD)
    ctx.store["read_at"] = now.isoformat()


@ext.on_settings
async def settings_changed(ctx, event):
    await read(ctx)


def _kept(ctx) -> list[tuple[dict, datetime]]:
    zone = ctx.now().tzinfo
    return [(e, datetime.fromisoformat(e["start"]).astimezone(zone)) for e in ctx.store.get("events", [])]


@ext.nudge("soon", title="Coming up", requested=True, outside=True)
async def soon(ctx):
    """Every timed event still to come, each held until `before` its start."""
    now = ctx.now()
    out = []
    for e, at in _kept(ctx):
        if e["all_day"] or at <= now:
            continue
        minutes = max(1, round((at - now).total_seconds() / 60))
        facts = {**_named(e), "starts": _when(at, False, now), "in_minutes": minutes}
        if e["location"]:
            facts["location"] = e["location"]
        out.append(Nudge(about=f"{e['calendar']}|{e['summary']}|{e['start']}",
                         say=f"{e['summary']} {'is due' if e.get('task') else 'starts'} in {minutes} minutes.",
                         facts=facts,
                         not_before=at - ctx.settings.before, expires=at))
    return out


@ext.nudge("today", title="Today's events", deliver="briefing", outside=True)
async def today(ctx):
    now = ctx.now()
    rows, heads = [], []
    for e, at in _kept(ctx):
        if e.get("task"):
            if at.date() > now.date():                  # due later; one overdue stays until it is done
                continue
            when = _when(at, e["all_day"], now, task=True)
            heads.append(f"{e['summary']}, {when}" if when.startswith("overdue") else e["summary"])
        elif at.date() == now.date() and (ctx.settings.all_day if e["all_day"] else at > now):
            when = "all day" if e["all_day"] else f"{at:%H:%M}"
            heads.append(e["summary"] if e["all_day"] else f"{e['summary']} at {when}")
        else:
            continue
        rows.append({**_named(e), "when": when, **({"location": e["location"]} if e["location"] else {})})
    if not rows:
        return None
    return Nudge(about=f"today:{now.date().isoformat()}", say=f"On your calendar today: {'; '.join(heads[:4])}."[:299],
                 facts={"events": rows})


@ext.tool(tier="read")
async def upcoming(ctx, days: int = 1) -> list[dict] | str:
    """The user's calendar and to-do lists: events from the start of today for this many days, and
    tasks due by then or overdue, soonest first.

    Args:
        days: 1 for today, 2 for today and tomorrow, 7 for the week; at most 31.
    """
    if not ctx.settings.calendars and not ctx.settings.lists:
        return "No calendar or to-do list is picked on the Agenda card."
    now = ctx.now()
    start = _start_of(now.date(), now.tzinfo)
    found = await _events(ctx, start, start + timedelta(days=max(1, min(days, 31))))
    out = []
    for e in found:
        at = datetime.fromisoformat(e["start"]).astimezone(now.tzinfo)
        out.append({**_named(e), "when": _when(at, e["all_day"], now, e.get("task", False)),
                    **({"location": e["location"]} if e["location"] else {}),
                    **({"past": True} if not e.get("task") and not e["all_day"] and at < now else {})})
    return out or "Nothing on the calendar."


@ext.status
async def status(ctx) -> str:
    s = ctx.settings
    if not s.calendars and not s.lists:
        return "No calendar picked"
    now = ctx.now()
    nxt = next(((e, at) for e, at in _kept(ctx) if not e["all_day"] and at > now), None)
    head = ", ".join(f"{n} {word}{'s' if n > 1 else ''}"
                     for n, word in ((len(s.calendars), "calendar"), (len(s.lists), "list")) if n)
    return f"{head}; next: {nxt[0]['summary']} {_when(nxt[1], False, now)}" if nxt else f"{head}; nothing in the next day"
