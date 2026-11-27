"""Lockdown over HTTP: the switch, and holding a call until the owner confirms it on the phone.
The state itself is core/lockdown; this is where winstond changes it and queues what it holds."""
import asyncio
from typing import Any

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from winston.core import lockdown
from winston.core.live import LiveEvent
from winston.extension_host.host import Pending
from winston.providers.home_assistant import HomeAssistant


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
    waiting: bool = False
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
    app.state.feed.publish(LiveEvent(channel="system", type="lockdown", text="on" if on else "off", name=by))


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

    @r.post("/api/lockdown/hold", response_model=Held,
            summary="Queue a call for the owner's confirmation instead of running it")
    async def hold(body: Hold):
        name, args = body.name, body.args
        if (t := host.find_tool(name)) is not None:
            label, ext, tool = f"Lockdown: {t['title']}: {t['tool_title']}", t["extension"], t["tool"]

            async def run():
                return await host.call_tool(ext, tool, args, confirmed=True)
        else:
            ha = HomeAssistant(app.state.cfg)
            try:
                row = next((x for x in await ha.tools() if name in x.names), None)
            except Exception:                                           # noqa: BLE001
                row = None
            if row is None:
                return Held(error=lockdown.UNAVAILABLE)
            label, ext, tool = f"Lockdown: {row.label}: {', '.join(map(str, args.values()))}", "home_assistant", name

            async def run():
                text = await ha.call_tool(name, args)
                return {"ok": False, "error": text} if text.startswith("Error") else {"ok": True, "result": text}
        host.add_pending(Pending(ext, tool, args, label, body.channel, run=run))
        return Held(waiting=True, note=lockdown.WAITING)
    return r
