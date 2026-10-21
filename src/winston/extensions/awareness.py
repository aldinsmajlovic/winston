# /// script
# [tool.winston]
# id = "awareness"
# name = "Awareness"
# description = "Small things he notices on his own: a goodnight when the evening goes quiet."
# icon = "eye"
# api = 1
# permissions = { network = ["localhost", "127.0.0.1"] }
# ///
"""Unprompted moments: things Winston says without being asked.

Goodnight: after 21:00, when there have been a few turns that day and then 30+ minutes of silence,
he says goodnight. One per night, expires at 06:00. A night is the evening it starts on: after
midnight the check still counts that evening's turns and files the goodnight under its date."""
from __future__ import annotations

import os
from datetime import datetime, time, timedelta

from winston_sdk import Extension, Nudge

ext = Extension()
ext.kind("goodnight", title="Goodnight", deliver="now", where="speakers", outside=False,
         description="Says goodnight after 21:00 once you have gone quiet for half an hour, on a day you talked "
                     "to him.")

MIN_TURNS = 3
QUIET_MINUTES = 30
EVENING_HOUR = 21
EXPIRES_HOUR = 6


@ext.every("15m")
async def check_goodnight(ctx):
    now = ctx.now()
    if EXPIRES_HOUR <= now.hour < EVENING_HOUR:
        return

    evening = ctx.today() - timedelta(days=1 if now.hour < EXPIRES_HOUR else 0)
    existing = await ctx.nudges("goodnight")
    if any(n.about == str(evening) for n in existing):
        return

    api = os.environ.get("WINSTON_API", "http://localhost:8700")
    answered = []
    for day in sorted({evening, now.date()}):
        r = await ctx.http.get(f"{api}/api/turns?day={day}")
        if r.status_code != 200:
            return
        answered += [t for t in r.json().get("turns", []) if t.get("outcome") == "answered"]
    if len(answered) < MIN_TURNS:
        return

    last_ts = max(t["ts"] for t in answered)
    quiet_for = now.timestamp() - last_ts
    if quiet_for < QUIET_MINUTES * 60:
        return

    await ctx.nudge("goodnight", Nudge(
        about=str(evening),
        say="Goodnight, sir. Sleep well.",
        facts={"turns": len(answered), "quiet_minutes": int(quiet_for / 60)},
        expires=datetime.combine(evening + timedelta(days=1), time(EXPIRES_HOUR), now.tzinfo),
    ))
