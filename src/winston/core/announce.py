"""Announcements: Winston speaking without being spoken to ("The washing machine is done").

winstond is the hub. Home Assistant (an automation fires `winston_announce` with {text, context}),
`POST /api/announce` and `winston announce` publish; channel processes subscribe to
`GET /api/events?channel=voice|discord` and deliver. The hub decides where each announcement goes:
spoken on every connected speaker unless it is quiet hours, and to Discord per `announce.discord`.

The text is spoken as given, never rephrased by the model. Each channel also adds the
announcement and its context to its conversation, so "how long did it run?" has an answer.

    hub = Hub(cfg); q = hub.subscribe("voice"); hub.publish(Announcement(text="..."))
    async for ev in subscribe(cfg, "voice"): ...      # in a channel process"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime
from datetime import time as dtime
from typing import Any, AsyncIterator

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from winston.config import resolve
from winston.config.schema import WinstonConfig

MAX_TEXT = 300


class Announcement(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT, description="Spoken as is.")
    context: dict[str, Any] = Field(default_factory=dict,
                                    description="Details Winston keeps for follow-up questions but does not read out.")
    source: str = Field("api", description="Who asked: api, cli, home_assistant.")


class Delivery(BaseModel):
    id: str
    spoken_on: int              # connected speakers it was sent to
    discord: bool
    quiet: bool


def in_quiet_hours(spec: str | None, now: dtime) -> bool:
    """"22:00-07:00" wraps midnight; "13:00-14:00" does not; empty or None is never quiet."""
    if not spec:
        return False
    a, b = (dtime.fromisoformat(x.strip()) for x in spec.split("-"))
    return a <= now < b if a <= b else now >= a or now < b


def route(cfg: WinstonConfig, speakers: int, now: dtime) -> tuple[bool, bool, bool]:
    """(speak, discord, quiet) for one announcement given how many speakers are connected."""
    quiet = in_quiet_hours(cfg.announce.quiet_hours, now)
    speak = not quiet and speakers > 0
    mode = cfg.announce.discord
    return speak, mode == "always" or (mode == "fallback" and not speak), quiet


def note(text: str, context: dict) -> str:
    """What goes into a conversation before the spoken announcement, so a reply has context."""
    details = f" Details: {json.dumps(context, ensure_ascii=False)}." if context else ""
    return (f"[Home notification, not said by the user. You announced: \"{text}\".{details} "
            f"Answer follow-up questions about it from these details.]")


class Hub:
    """In-process fan-out to channel subscribers; one queue per connected session."""

    def __init__(self, cfg: WinstonConfig):
        self.cfg = cfg
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._log = resolve(cfg.logging.tool_calls).parent / "announcements.jsonl"

    def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subs.setdefault(channel, set()).add(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue):
        self._subs.get(channel, set()).discard(q)

    def count(self, channel: str) -> int:
        return len(self._subs.get(channel, ()))

    def publish(self, a: Announcement, now: datetime | None = None) -> Delivery:
        now = now or datetime.now()
        speakers = self.count("voice")
        speak, discord, quiet = route(self.cfg, speakers, now.time())
        discord = discord and self.count("discord") > 0
        ev = {"id": uuid.uuid4().hex[:12], "ts": time.time(), "text": a.text, "context": a.context,
              "source": a.source, "speak": speak}
        for channel, send in (("voice", speak), ("discord", discord)):
            for q in list(self._subs.get(channel, ())) if send else ():
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:           # a stuck subscriber must not block the rest
                    logger.warning(f"announcement dropped for a stuck {channel} subscriber")
        d = Delivery(id=ev["id"], spoken_on=speakers if speak else 0, discord=discord, quiet=quiet)
        self._log.parent.mkdir(parents=True, exist_ok=True)
        with self._log.open("a") as f:
            f.write(json.dumps({**ev, **d.model_dump()}, ensure_ascii=False) + "\n")
        logger.info(f"announce {a.text!r} from {a.source}: spoken on {d.spoken_on}, discord {d.discord}, quiet {quiet}")
        return d


async def subscribe(cfg: WinstonConfig, channel: str) -> AsyncIterator[dict]:
    """Announcements for this channel from winstond, reconnecting for as long as the caller
    iterates. A channel started without winstond simply never gets any."""
    url = f"http://{cfg.control.host}:{cfg.control.port}/api/events"
    delay = 1.0
    while True:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=None)) as c:
                async with c.stream("GET", url, params={"channel": channel}) as r:
                    r.raise_for_status()
                    delay = 1.0
                    async for line in r.aiter_lines():
                        if line.startswith("data: "):
                            yield json.loads(line[6:])
        except (httpx.HTTPError, ValueError) as e:
            logger.debug(f"announcement stream: {e}; retrying in {delay:.0f} s")
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)
