"""Discord channel: talk to Winston in DMs from anywhere, and get announcements there. Runs as
its own process, started by the supervisor:

    python -m winston.channels.discord

Only the one user in channels.discord.allowed_user (or, left empty, the variable allowed_user_env
names) is answered; every other message
is ignored (plan §6: single user, and a message from anyone else is never an instruction). The
bot connects out to Discord, nothing listens on the LAN. A conversation starts fresh after
session_idle_mins, which also re-reads memory."""
import asyncio
import os
import sys
import time

import discord
from loguru import logger

from winston.channels.text import TextSession
from winston.config import load
from winston.config.schema import WinstonConfig
from winston.core.announce import subscribe

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
        self._session_lock = asyncio.Lock()
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    async def current(self) -> TextSession:
        async with self._session_lock:
            if self.session is None:
                self.session = await TextSession(self.cfg, channel="discord").start()
                self.session.notify = self.correction
                logger.info("new conversation")
            return self.session

    async def reaper(self):
        """Close a conversation after it sits idle, so the next message starts fresh."""
        idle = self.cfg.channels.discord.session_idle_mins * 60
        while True:
            await asyncio.sleep(60)
            async with self._session_lock:
                s = self.session
                if s and not s.busy and time.monotonic() - s.last_used > idle:
                    self.session = None
                    await s.close()
                    logger.info("conversation closed after idling")

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
        async for ev in subscribe(self.cfg, "discord"):
            try:
                await (await self.dm()).send(ev["text"])
                await (await self.current()).announced(ev["text"], ev["context"])
                logger.info(f"announcement sent: {ev['text']!r}")
            except discord.DiscordException as e:
                logger.warning(f"announcement not delivered: {e}")

    async def on_ready(self):
        logger.info(f"connected to Discord as {self.client.user}")
        if not getattr(self, "_started", False):         # on_ready fires again after reconnects
            self._started = True
            asyncio.create_task(self.reaper())
            if self.cfg.announce.enabled:
                asyncio.create_task(self.announcements())

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
            reply = await (await self.current()).ask(text)
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
