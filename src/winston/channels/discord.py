"""Discord channel: talk to Winston in DMs from anywhere, and get announcements there. Runs as
its own process, started by the supervisor:

    python -m winston.channels.discord

Only the one user in channels.discord.allowed_user (or, left empty, the variable allowed_user_env
names) is answered; every other message
is ignored (plan §6: single user, and a message from anyone else is never an instruction). The
bot connects out to Discord, nothing listens on the LAN. A conversation starts fresh after
session_idle_mins, which also re-reads memory. /room says where you are: the conversation joins that
room's and "the lights" means its lights, until room_mins pass without a message from you."""
import asyncio
import os
import sys
import time
import uuid

import discord
import httpx
from discord import app_commands
from loguru import logger

from winston.channels.text import TextSession, error_text, stale
from winston.config import load
from winston.config.schema import WinstonConfig
from winston.core.announce import nudge_note, subscribe
from winston.core.common import key
from winston.core.rooms import RoomLink, area, configured, http_sync

LIMIT = 2000        # Discord's message length


def chunks(text: str, limit: int = LIMIT) -> list[str]:
    """Split on paragraph, then line, then word boundaries so no piece exceeds the limit."""
    out, rest = [], text.strip() or "(no reply)"
    while len(rest) > limit:
        cut = max(rest.rfind(sep, 0, limit) for sep in ("\n\n", "\n", " "))
        cut = cut if cut > limit // 2 else limit
        out.append(rest[:cut].rstrip()); rest = rest[cut:].lstrip()
    return out + [rest]


class DiscordChannel:
    def __init__(self, cfg: WinstonConfig):
        c = cfg.channels.discord
        self.cfg = cfg
        self.token = os.environ.get(c.token_env, "")
        self.allowed = int(c.allowed_id() or 0)
        if not self.token:
            raise SystemExit(f"{c.token_env} must be set in data/.env")
        if not self.allowed:
            raise SystemExit(f"set your Discord user id (channels.discord.allowed_user) or {c.allowed_user_env} "
                             "in data/.env")
        intents = discord.Intents.default()
        intents.message_content = True          # also enable "Message Content Intent" in the developer portal
        self.client = discord.Client(intents=intents)
        self.session: TextSession | None = None
        self.session_room: str | None = None    # the room the session was started in
        self.room: str | None = None            # set with /room
        self.room_used = 0.0                    # when you last wrote with a room set
        self.room_gone: str | None = None       # a room that lapsed, said at your next message
        self._session_lock = asyncio.Lock()
        self.client.event(self.on_ready)
        self.client.event(self.on_message)
        self.tree = app_commands.CommandTree(self.client)
        self.tree.add_command(self.room_command())

    def lapse(self, now: float) -> None:
        """Clears a room room_mins after your last message with it set."""
        if self.room and now - self.room_used > self.cfg.channels.discord.room_mins * 60:
            self.room_gone, self.room = self.room, None

    async def current(self) -> TextSession:
        async with self._session_lock:
            self.lapse(time.monotonic())
            s, idle = self.session, self.cfg.channels.discord.session_idle_mins * 60
            # a room set or cleared starts the next conversation, once this one has answered
            if s is not None and ((self.session_room != self.room and not s.busy) or await stale(s, idle)):
                await s.close()
                self.session = None
            if self.session is None:
                room = self.session_room = self.room
                link = RoomLink(room, f"discord:{uuid.uuid4().hex[:8]}", http_sync(self.cfg)) if room else None
                self.session = await TextSession(self.cfg, channel="discord", room=link,
                                                 area=area(self.cfg, room) if room else None).start()
                self.session.notify = self.correction
                logger.info(f"new conversation{f' in {room}' if room else ''}")
            return self.session

    async def rooms(self) -> list[str]:
        """The boxes' rooms, then Home Assistant's areas (through winstond): the Talk page's room choices."""
        names = configured(self.cfg)
        try:
            async with httpx.AsyncClient(timeout=2) as c:
                r = await c.get(f"{self.cfg.control.url}/api/options/ha_areas")
            names += [ch["value"] for ch in r.json()["choices"]]
        except (httpx.HTTPError, ValueError, KeyError) as e:
            logger.warning(f"Home Assistant's areas not listed for /room: {e}")
        out: dict[str, str] = {}
        for n in names:
            out.setdefault(key(n), n)
        return list(out.values())

    async def set_room(self, name: str | None) -> str:
        """/room: none clears it, a name sets it, nothing says which is set. The reply to show."""
        mins = self.cfg.channels.discord.room_mins
        self.lapse(time.monotonic())
        if name is None:
            if not self.room:
                return "No room set. Name the room when you ask about its lights, or set one with /room."
            return f"In {self.room}. It clears after {mins} min without a message from you."
        if key(name) == "none":
            self.room = None
            return "No room now."
        names = await self.rooms()
        room = next((n for n in names if key(n) == key(name)), None)
        if room is None:
            return f"No room called {name}. Rooms: {', '.join(names) or 'none listed'}."
        self.room, self.room_used, self.room_gone = room, time.monotonic(), None
        return (f"In {room} now: \"the lights\" means its lights, and this conversation is the room's. "
                f"It clears after {mins} min without a message from you.")

    def room_command(self) -> app_commands.Command:
        @app_commands.command(name="room", description="Say which room you are in, so \"the lights\" means its lights")
        @app_commands.describe(room="A room, or none to clear it. Leave it out to see which is set.")
        @app_commands.allowed_contexts(guilds=False, dms=True, private_channels=False)
        async def room(interaction: discord.Interaction, room: str | None = None):
            if interaction.user.id != self.allowed:
                logger.warning(f"ignored /room from {interaction.user} ({interaction.user.id}), not the allowed user")
                return await interaction.response.send_message("Not available.", ephemeral=True)
            await interaction.response.defer(thinking=True)        # Home Assistant's areas can take a moment
            await interaction.followup.send(await self.set_room(room))

        @room.autocomplete("room")
        async def choices(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
            names = [*await self.rooms(), "none"]
            return [app_commands.Choice(name=n, value=n) for n in names if key(current) in key(n)][:25]
        return room

    async def dm(self) -> discord.DMChannel:
        user = self.client.get_user(self.allowed) or await self.client.fetch_user(self.allowed)
        return user.dm_channel or await user.create_dm()

    async def correction(self, text: str):
        """A state check's correction (core/verify.py), sent as its own message."""
        try:
            await (await self.dm()).send(text)
        except discord.DiscordException as e:
            logger.warning(f"correction not delivered: {e}")

    async def announcements(self):
        """Announcements, and nudges while you are away with no phone set: sent as a DM and added to the
        conversation, so a reply ("remind me when I'm home") has the context."""
        async for ev in subscribe(self.cfg, "discord"):
            try:
                await (await self.dm()).send(ev["text"])
                if ev.get("type") == "nudge":
                    await (await self.current()).noted(nudge_note(ev), ev["text"])
                else:
                    await (await self.current()).announced(ev["text"], ev["context"])
                logger.info(f"{ev.get('type', 'announcement')} sent: {ev['text']!r}")
            except discord.DiscordException as e:
                logger.warning(f"{ev.get('type', 'announcement')} not delivered: {e}")

    async def on_ready(self):
        logger.info(f"connected to Discord as {self.client.user}")
        if not getattr(self, "_started", False):         # on_ready fires again after reconnects
            self._started = True
            if self.cfg.announce.enabled:
                asyncio.create_task(self.announcements())
            try:
                await self.tree.sync()
            except discord.DiscordException as e:
                logger.warning(f"/room not registered with Discord: {e}")

    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is not None:
            return                                          # DMs only, never our own messages
        if message.author.id != self.allowed:
            logger.warning(f"ignored a DM from {message.author} ({message.author.id}), not the allowed user")
            return
        text = message.content.strip()
        if not text:
            return
        async with message.channel.typing():
            try:
                s = await self.current()
                if self.room_gone:
                    gone, self.room_gone = self.room_gone, None
                    await message.channel.send(f"No longer in {gone}: {self.cfg.channels.discord.room_mins} min "
                                               "passed without a message. Set it again with /room.")
                if self.room:
                    self.room_used = time.monotonic()
                reply = await s.ask(text)
            except Exception as e:                                      # noqa: BLE001
                logger.exception("no reply")
                reply = f"_{error_text(e)}_"                            # a note, not his words
        for part in chunks(reply):
            await message.channel.send(part)

    async def run(self):
        await self.client.start(self.token)


def main():
    cfg = load()
    logger.remove(); logger.add(sys.stderr, level="INFO")
    try:
        asyncio.run(DiscordChannel(cfg).run())
    except discord.LoginFailure as e:
        raise SystemExit(f"Discord refused the token: {e}")
    except discord.PrivilegedIntentsRequired:
        raise SystemExit("enable Message Content Intent for the bot in the Discord developer portal")


if __name__ == "__main__":
    main()
