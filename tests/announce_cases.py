"""Announcement cases: quiet hours, routing to speakers and Discord, fan-out to subscribers.
No hardware and no winstond needed.

    uv run python tests/announce_cases.py
"""
import asyncio
import os
import sys
import tempfile
from datetime import datetime
from datetime import time as t

os.environ["WINSTON_DATA"] = tempfile.mkdtemp(prefix="winston-announce-")

from winston.config.schema import WinstonConfig  # noqa: E402  (after WINSTON_DATA is set)
from winston.core.announce import Announcement, Hub, in_quiet_hours, note, route  # noqa: E402

failed = 0


def expect(name: str, ok: bool, detail=""):
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name} {detail}")


def cfg(**announce) -> WinstonConfig:
    return WinstonConfig.model_validate({"llm": {"model": "x"}, "announce": announce})


for spec, now, quiet in [("22:00-07:00", t(23, 30), True), ("22:00-07:00", t(6, 59), True),
                         ("22:00-07:00", t(7, 0), False), ("22:00-07:00", t(12, 0), False),
                         ("13:00-14:00", t(13, 30), True), ("13:00-14:00", t(14, 0), False),
                         (None, t(3, 0), False), ("", t(3, 0), False)]:
    expect(f"quiet {spec!r} at {now}", in_quiet_hours(spec, now) == quiet)

noon, night = t(12, 0), t(23, 0)
expect("fallback, speaker up: speak only", route(cfg(), 1, noon) == (True, False, False))
expect("fallback, no speaker: Discord", route(cfg(), 0, noon) == (False, True, False))
expect("fallback, quiet: Discord", route(cfg(), 1, night) == (False, True, True))
expect("always: both", route(cfg(discord="always"), 1, noon) == (True, True, False))
expect("never, no speaker: nowhere", route(cfg(discord="never"), 0, noon) == (False, False, False))
expect("note carries details", "started" in note("Washer done.", {"started": "13:02"}) and "not said by the user" in note("x", {}))


async def fan_out():
    hub = Hub(cfg())
    v1, v2, d = hub.subscribe("voice"), hub.subscribe("voice"), hub.subscribe("discord")
    r = hub.publish(Announcement(text="The washing machine is done.", context={"finished": "14:31"}),
                    now=datetime(2026, 9, 23, 12, 0))
    expect("both speakers get it", v1.qsize() == 1 and v2.qsize() == 1 and r.spoken_on == 2, r)
    expect("Discord skipped while a speaker spoke (fallback)", d.qsize() == 0 and not r.discord)
    ev = v1.get_nowait()
    expect("event carries text and context", ev["text"] == "The washing machine is done." and ev["context"]["finished"] == "14:31")
    r = hub.publish(Announcement(text="Door."), now=datetime(2026, 9, 23, 23, 0))
    expect("quiet hours: Discord only", d.qsize() == 1 and v2.qsize() == 1 and r.quiet and r.discord, r)
    hub.unsubscribe("voice", v1); hub.unsubscribe("voice", v2)
    r = hub.publish(Announcement(text="Nobody home."), now=datetime(2026, 9, 23, 12, 0))
    expect("no speaker: Discord", r.discord and r.spoken_on == 0, r)

asyncio.run(fan_out())
print(f"\n{'all passed' if not failed else f'{failed} failed'}")
sys.exit(1 if failed else 0)
