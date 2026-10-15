# /// script
# [tool.winston]
# id = "awareness"
# name = "Awareness"
# description = "Small things Winston notices on his own: a goodnight when the evening goes quiet."
# icon = "eye"
# api = 1
# permissions = { network = ["localhost", "127.0.0.1"] }
# ///
"""Unprompted moments: things Winston says without being asked.

Goodnight: after 21:00, when there have been a few turns today and then 30+ minutes of silence,
he says goodnight. One per night, expires at 06:00."""
from __future__ import annotations

import os
from datetime import timedelta

from winston_sdk import Extension, Nudge

ext = Extension()
ext.kind("goodnight", title="Goodnight", deliver="now", where="speakers", outside=False)

MIN_TURNS = 3
QUIET_MINUTES = 30
EVENING_HOUR = 21
EXPIRES_HOUR = 6


@ext.every("15m")
async def check_goodnight(ctx):
    now = ctx.now()
    if EXPIRES_HOUR <= now.hour < EVENING_HOUR:
        return

    existing = await ctx.nudges("goodnight")
    today = now.strftime("%Y-%m-%d")
    if any(n.about == today for n in existing):
        return

    api = os.environ.get("WINSTON_API", "http://localhost:8700")
    r = await ctx.http.get(f"{api}/api/turns?day={today}")
    if r.status_code != 200:
        return
    turns = r.json().get("turns", [])
    answered = [t for t in turns if t.get("outcome") == "answered"]
    if len(answered) < MIN_TURNS:
        return

    last_ts = max(t["ts"] for t in answered)
    quiet_for = now.timestamp() - last_ts
    if quiet_for < QUIET_MINUTES * 60:
        return

    tomorrow = (now + timedelta(days=1)).replace(hour=EXPIRES_HOUR, minute=0, second=0, microsecond=0)
    await ctx.nudge("goodnight", Nudge(
        about=today,
        say="Goodnight, sir. Sleep well.",
        facts={"turns": len(answered), "quiet_minutes": int(quiet_for / 60)},
        expires=tomorrow,
    ))
