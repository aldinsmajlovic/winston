# /// script
# [tool.winston]
# id = "agenda"
# name = "Agenda"
# description = "Your calendars from Home Assistant: a word before each event, and the day's events in the morning briefing."
# icon = "calendar"
# api = 1
# permissions = { home_assistant = "read" }
# ///
"""Agenda: the calendars Home Assistant has (Google Calendar, CalDAV, a local one), read through its
`calendar.get_events`, so Home Assistant handles accounts and repeating events.

Every 10 minutes it keeps the events from the start of today to 36 hours ahead. Two kinds:

- "soon": each timed event, held until `before` its start and checked again then, so a moved or
  cancelled event is never said. Asked for (you put it in your calendar), so it comes through quiet
  hours and the daily limit.
- "today": the day's events, for the morning briefing."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from winston_sdk import Duration, Entity, Extension, Field, Nudge, Settings

ext = Extension()
AHEAD = timedelta(hours=36)


class AgendaSettings(Settings):
    calendars: list[str] = Entity(domain="calendar", default=[],
                                  description="The calendars he reads. Picking one lets him read it, exposed or not.")
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


def _when(at: datetime, all_day: bool, now: datetime) -> str:
    """"today 14:00", "tomorrow, all day", "Friday 16 October 09:30"."""
    day = "today" if at.date() == now.date() else "tomorrow" if at.date() == now.date() + timedelta(1) \
        else f"{at:%A} {at.day} {at:%B}"
    return f"{day}, all day" if all_day else f"{day} {at:%H:%M}"


async def _events(ctx, start: datetime, end: datetime) -> list[dict]:
    """Every picked calendar's events between start and end, soonest first."""
    cals = ctx.settings.calendars
    if not cals:
        return []
    answer = await ctx.ha.query("calendar", "get_events", target={"entity_id": cals},
                                data={"start_date_time": start.isoformat(), "end_date_time": end.isoformat()})
    zone = ctx.now().tzinfo
    out = []
    for cal, found in (answer or {}).items():
        for e in (found or {}).get("events") or []:
            if not e.get("start") or not e.get("summary"):
                continue
            at, all_day = _parse(e["start"], zone)
            out.append({"calendar": cal, "summary": e["summary"][:200], "start": at.isoformat(), "all_day": all_day,
                        "location": (e.get("location") or "")[:200]})
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
        facts = {"event": e["summary"], "starts": _when(at, False, now), "in_minutes": minutes}
        if e["location"]:
            facts["location"] = e["location"]
        out.append(Nudge(about=f"{e['calendar']}|{e['summary']}|{e['start']}",
                         say=f"{e['summary']} starts in {minutes} minutes.", facts=facts,
                         not_before=at - ctx.settings.before, expires=at))
    return out


@ext.nudge("today", title="Today's events", deliver="briefing", outside=True)
async def today(ctx):
    now = ctx.now()
    events = [{"event": e["summary"], "when": "all day" if e["all_day"] else f"{at:%H:%M}",
               **({"location": e["location"]} if e["location"] else {})}
              for e, at in _kept(ctx)
              if at.date() == now.date() and (e["all_day"] and ctx.settings.all_day or not e["all_day"] and at > now)]
    if not events:
        return None
    heads = [f"{e['event']} at {e['when']}" if e["when"] != "all day" else e["event"] for e in events[:4]]
    return Nudge(about=f"today:{now.date().isoformat()}", say=f"On your calendar today: {'; '.join(heads)}."[:299],
                 facts={"events": events})


@ext.tool(tier="read")
async def upcoming(ctx, days: int = 1) -> list[dict] | str:
    """The user's calendar: events from the start of today for this many days, soonest first.

    Args:
        days: 1 for today, 2 for today and tomorrow, 7 for the week; at most 31.
    """
    if not ctx.settings.calendars:
        return "No calendar is picked on the Agenda card."
    now = ctx.now()
    start = _start_of(now.date(), now.tzinfo)
    found = await _events(ctx, start, start + timedelta(days=max(1, min(days, 31))))
    out = []
    for e in found:
        at = datetime.fromisoformat(e["start"]).astimezone(now.tzinfo)
        out.append({"event": e["summary"], "when": _when(at, e["all_day"], now),
                    **({"location": e["location"]} if e["location"] else {}),
                    **({"past": True} if not e["all_day"] and at < now else {})})
    return out or "Nothing on the calendar."


@ext.status
async def status(ctx) -> str:
    if not ctx.settings.calendars:
        return "No calendar picked"
    now = ctx.now()
    nxt = next(((e, at) for e, at in _kept(ctx) if not e["all_day"] and at > now), None)
    n = len(ctx.settings.calendars)
    head = f"{n} calendar{'s' if n > 1 else ''}"
    return f"{head}; next: {nxt[0]['summary']} {_when(nxt[1], False, now)}" if nxt else f"{head}; nothing in the next day"
