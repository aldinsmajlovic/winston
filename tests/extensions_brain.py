"""The built-in extensions with the real brain (Extensions track step 4): does the voice channel's brain
(thinking off, the spoken prompt) use Reminders, News, Agenda and Later the way you would ask? A
throwaway winstond with the fake Home Assistant (a made-up calendar), those four switched on (News
reads the real public feeds of its presets, nothing personal); data/ is only read (config for the
model, the persona).

Needs the LLM engine up.

    uv run python tests/extensions_brain.py
"""
import asyncio
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from harness import free_port, scratch_data, serve, winstond_data

REAL = Path(os.environ.get("WINSTON_DATA") or Path(__file__).resolve().parent.parent / "data")
DATA = scratch_data("winston-extensions-brain-")

import httpx  # noqa: E402
import yaml  # noqa: E402
from fake_ha import FakeHA  # noqa: E402

failed = 0


def expect(name: str, ok: bool, detail=""):
    global failed
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} {name}{'' if ok else f'  {detail}'}")


def setup(api_port: int, ha_port: int):
    real = yaml.safe_load((REAL / "config.yaml").read_text())
    cfg = {"assistant": real.get("assistant", {}), "llm": real["llm"],
           "channels": {c: {"enabled": False} for c in ("voice", "satellite", "discord")},
           "providers": {"home_assistant": {"url": f"http://127.0.0.1:{ha_port}", "token_env": "HA_TOKEN",
                                            "expose": {"light.taklampa": {}, "light.main_room": {}}}},
           "control": {"port": api_port, "host": "127.0.0.1"},
           "speak_up": {"home": "person.someone"},
           "extensions": {"reminders": {"enabled": True}, "later": {"enabled": True},
                          "agenda": {"enabled": True, "settings": {"calendars": ["calendar.home"]}},
                          "news": {"enabled": True, "settings": {"sweden_en": True, "world": True}}}}
    winstond_data(DATA, cfg, REAL / "persona.md")


def calls(since: float) -> list[tuple[str, dict]]:
    path = DATA / "logs" / "tool_calls.jsonl"
    rows = [json.loads(ln) for ln in path.read_text().splitlines()] if path.is_file() else []
    return [(r["tool"], r.get("args") or {}) for r in rows if r["ts"] >= since and r.get("event") == "call"]


async def main() -> bool:
    api_port, ha_port = free_port(), free_port()
    setup(api_port, ha_port)
    from winston.api.app import create_app
    from winston.config import load
    async with FakeHA(ha_port, seed="home") as ha:
        today = datetime.now().astimezone().date()
        tomorrow = today + timedelta(days=1)
        ha.events["calendar.home"] = [
            {"summary": "Anna's birthday", "start": today.isoformat(), "end": tomorrow.isoformat()},
            {"summary": "Dentist", "start": f"{tomorrow}T09:30:00+02:00", "end": f"{tomorrow}T10:15:00+02:00",
             "location": "Storgatan 4"}]
        app = create_app(load(), autostart=False)
        stop_server = await serve(app, api_port)
        host = app.state.extensions
        await host.start()
        host.scheduler.start()                          # News reads its feeds at start
        api = httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}", timeout=60)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 180:
            status = {e["id"]: (e["state"], e.get("status") or "") for e in (await api.get("/api/extensions")).json()}
            if status.get("news", ("", ""))[1].startswith(tuple("123456789")) and "read" in status["news"][1] \
                    and "not yet" not in status["news"][1] and status.get("reminders", ("",))[0] == "running":
                break
            await asyncio.sleep(2)
        expect("Reminders runs and News has read its feeds", "not yet" not in status.get("news", ("", "not yet"))[1],
               status)
        print(f"  news: {status.get('news')}")

        import winston.channels.text as text_channel
        brain_class = text_channel.Brain
        text_channel.Brain = lambda cfg, channel="text": brain_class(cfg, channel="voice")
        try:
            s = await text_channel.TextSession(load(), channel="voice-test").start()
        finally:
            text_channel.Brain = brain_class

        async def turn(said: str) -> tuple[str, list[tuple[str, dict]]]:
            t = time.time()
            reply = await s.ask(said)
            used = calls(t)
            print(f"\n> {said}\n  {reply!r}\n  {used}")
            return reply, used

        reply, used = await turn("Remind me to call Mum at six.")
        add = [a for n, a in used if n == "reminders_add"]
        expect("\"remind me to call Mum at six\" sets one for 18:00", add and "mum" in add[0].get("what", "").lower()
               and re.search(r"\b(six|6|18)\b", str(add[0].get("at", ""))) and re.search(r"(six|6|18)", reply.lower()),
               (used, reply))
        reply, used = await turn("And every weekday at half past seven, remind me to take my vitamins.")
        add = [a for n, a in used if n == "reminders_add"]
        expect("a weekday reminder repeats", add and add[0].get("repeat") == "weekdays", used)
        reply, used = await turn("What reminders do I have?")
        expect("they are listed", any(n == "reminders_upcoming" for n, _ in used) and "mum" in reply.lower()
               and "vitamin" in reply.lower(), (used, reply))
        reply, used = await turn("Cancel the one about Mum.")
        cancel = [a for n, a in used if n == "reminders_cancel"]
        left = [r["what"] for r in (await api.post("/api/extensions/reminders/tools/upcoming", json={})).json()
                ["result"]] if cancel else []
        expect("one is cancelled by its words", cancel and left == ["take my vitamins"], (used, left))
        reply, used = await turn("What's in the news?")
        expect("the news comes from get_news, in English", any(n == "news_get_news" for n, _ in used)
               and len(reply) > 40 and not re.search(r"[åäö]", reply.lower()), (used, reply))
        reply, used = await turn("Anything about Sweden?")
        news = [a for n, a in used if n == "news_get_news"]
        expect("a topic is asked for as a topic", news and "swed" in str(news[0].get("topic", "")).lower(), used)

        reply, used = await turn("What's on my calendar today?")
        expect("the calendar comes from agenda_upcoming", any(n == "agenda_upcoming" for n, _ in used)
               and "birthday" in reply.lower() and "dentist" not in reply.lower(), (used, reply))
        reply, used = await turn("And tomorrow?")
        expect("tomorrow is asked for", any(n == "agenda_upcoming" and a.get("days", 1) >= 2 for n, a in used)
               and "dentist" in reply.lower(), (used, reply))

        reply, used = await turn("Turn off the main room lights when I leave.")
        add = [a for n, a in used if n == "later_add"]
        expect("\"when I leave\" waits for leaving, nothing done now", add and add[0].get("on") == "leave"
               and add[0].get("tool", "").endswith("HassTurnOff") and not any("HassTurnOff" in n for n, _ in used),
               (used, reply))
        await ha.change("person.someone", "not_home")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 15 and not any(w["type"].endswith("HassTurnOff") for w in ha.writes):
            await asyncio.sleep(0.5)
        expect("leaving turns them off", [w for w in ha.writes if w["type"].endswith("HassTurnOff")], ha.writes)
        await s.close()

        await host.stop_all()
        await host.scheduler.stop()
        await stop_server()
        await api.aclose()
    print(f"\n{'all passed' if not failed else f'{failed} failed'}")
    return not failed


if __name__ == "__main__":
    try:
        ok = asyncio.run(main())
    finally:
        shutil.rmtree(DATA, ignore_errors=True)
    sys.exit(0 if ok else 1)
