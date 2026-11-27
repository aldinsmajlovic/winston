"""Lockdown over HTTP: the switch, and holding a call until the owner confirms it on the phone.
The state itself is core/lockdown; this is where winstond changes it and queues what it holds."""
import asyncio
from typing import Any

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from winston.core import lockdown
from winston.core.live import LiveEvent
from winston.extension_host.host import Pending, in_words
from winston.providers.home_assistant import HomeAssistant


def label(what: str, args: dict) -> str:
    """The phone's question, "Lockdown: Turn on: Kitchen light": what will run and what it was given, cut to
    fit a notification (the arguments are a guest's words)."""
    given = ", ".join(map(str, args.values()))
    text = f"Lockdown: {what}: {given}" if given else f"Lockdown: {what}"
    return text if len(text) <= 150 else text[:149] + "…"


class LockdownState(BaseModel):
    on: bool
    since: float | None = None
    by: str | None = None


class LockdownChange(BaseModel):
    on: bool
    channel: str | None = None      # the conversation asking; turning off from one waits for the phone


class LockdownAnswer(BaseModel):
    on: bool
    waiting: bool = False
    note: str | None = None


class Hold(BaseModel):
    name: str
    args: dict[str, Any] = {}
    channel: str


class Held(BaseModel):
    waiting: bool | None = None
    note: str | None = None
    error: str | None = None


def set_lockdown(app: FastAPI, on: bool, by: str) -> None:
    """The one place a change happens: the file, every conversation forgotten, the voice restarted
    (it reads the state at start), and the feed told."""
    if not (lockdown.turn_on(by) if on else lockdown.turn_off(by)):
        return
    rooms = app.state.rooms
    for r in list(rooms._rooms.values()):
        rooms.clear(r)
    sup = app.state.supervisor
    if (m := sup.items.get("voice")) is not None and m.state != "stopped":
        async def restart():
            await sup.stop("voice")
            await sup.start("voice")
        asyncio.create_task(restart())
    app.state.feed.publish(LiveEvent(channel="system", type="lockdown", on=on, since=(lockdown.state() or {}).get("since"), by=by))


def router(app: FastAPI) -> APIRouter:
    r = APIRouter(tags=["lockdown"])
    host = app.state.extensions

    @r.get("/api/lockdown", response_model=LockdownState, summary="Whether lockdown is on, since when and who did it")
    async def get_state():
        s = lockdown.state()
        return LockdownState(on=s is not None, since=s and s["since"], by=s and s["by"])

    @r.post("/api/lockdown", response_model=LockdownAnswer,
            summary="Turn lockdown on at once; off at once, or from a conversation (channel) once confirmed on the phone")
    async def change(body: LockdownChange):
        if body.on or body.channel is None:
            set_lockdown(app, body.on, body.channel or "his computer")
            return LockdownAnswer(on=lockdown.on())

        async def run():
            set_lockdown(app, False, body.channel)
            return {"ok": True, "result": "Lockdown is off."}
        host.add_pending(Pending("lockdown", "off", {}, "Lockdown: turn off", body.channel, run=run))
        return LockdownAnswer(on=lockdown.on(), waiting=True, note=lockdown.WAITING)

    @r.post("/api/lockdown/hold", response_model=Held, response_model_exclude_none=True,
            summary="Queue a call for the owner's confirmation instead of running it")
    async def hold(body: Hold):
        name, args = body.name, body.args
        if (t := host.find_tool(name)) is not None:
            what, ext, tool = f"{t['title']}: {t['tool_title']}", t["extension"], t["tool"]

            async def run():
                return await host.call_tool(ext, tool, args, confirmed=True)
        else:
            try:
                ha = HomeAssistant(app.state.cfg)
                row = next((x for x in await ha.tools() if name in x.names), None)
            except Exception:                                           # noqa: BLE001
                row = None
            if row is None:
                return Held(error=lockdown.UNAVAILABLE)
            # Its own name, not its row's label: HassTurnOn and HassTurnOff share "Turn things on and off".
            what, ext, tool = in_words(name.split("__")[-1].removeprefix("Hass")), "home_assistant", name

            async def run():
                text = await ha.call_tool(name, args)
                return {"ok": False, "error": text} if text.startswith("Error") else {"ok": True, "result": text}
        host.add_pending(Pending(ext, tool, args, label(what, args), body.channel, run=run))
        return Held(waiting=True, note=lockdown.WAITING)
    return r
