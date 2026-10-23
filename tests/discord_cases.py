"""/room in Discord DMs: names match the boxes' rooms whatever their case, an unknown one is refused, none
clears it, and it lapses room_mins after your last message. No Discord connection; nothing on the control
port, so Home Assistant's areas are not listed.

    uv run python tests/discord_cases.py
"""
import asyncio
import os

from harness import check, finish_checks, free_port, scratch_data

scratch_data("winston-discord-")
os.environ["DISCORD_BOT_TOKEN"] = "made-up"

from loguru import logger  # noqa: E402
from winston.channels.discord import DiscordChannel  # noqa: E402  (after WINSTON_DATA is set)
from winston.config.schema import WinstonConfig  # noqa: E402

logger.remove()
cfg = WinstonConfig.model_validate({
    "llm": {"model": "x"}, "control": {"port": free_port()},
    "channels": {"discord": {"enabled": True, "allowed_user": "123456789012345678", "room_mins": 60},
                 "satellite": {"devices": {"main room": {"host": "10.0.0.2", "area": "Main Room"}}}}})
d = DiscordChannel(cfg)


async def cases():
    check("nothing set", (await d.set_room(None)).startswith("No room set"), True)
    check("unknown refused", ((await d.set_room("attic")).startswith("No room called attic"), d.room), (True, None))
    await d.set_room("MAIN  room")
    check("matched to the box's room", d.room, "main room")
    d.lapse(d.room_used + 59 * 60)
    check("kept within the hour", (d.room, d.room_gone), ("main room", None))
    d.lapse(d.room_used + 61 * 60)
    check("lapsed after it", (d.room, d.room_gone), (None, "main room"))
    await d.set_room("main room")
    check("set again forgets the lapse", d.room_gone, None)
    check("none clears", (await d.set_room("none"), d.room), ("No room now.", None))

asyncio.run(cases())
finish_checks("discord /room: matching, refusing, clearing and lapsing ok")
