"""Speaking up (docs/extensions.md#speaking-up), in two parts.

The rules: the nudge engine against a stand-in host, a fake clock, fake presence and a fake speaker,
so every rule is checked at the minute it matters.

Through winstond: a real one on a spare port with the fake Home Assistant, the docs' plants and
reminders extensions and a trainer standing in for Hevy, two speakers in two rooms listening on
/api/events, a fake phone (Home Assistant's notify service) and the engine's clock moved by hand.

No model, no real Home Assistant, a throwaway data directory; data/ is never touched.

    uv run python tests/nudges_cases.py
"""
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import harness
from harness import expect, finish, free_port, scratch_data, serve, until, winstond_data

DATA = scratch_data("winston-nudges-")
os.environ["TZ"] = "Europe/Stockholm"
harness.detail_on_pass = False

from winston.config.schema import ExtensionConfig, WinstonConfig  # noqa: E402
from winston.extension_host.nudges import Nudges, nudge_id  # noqa: E402

Z = ZoneInfo("Europe/Stockholm")


def at(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=Z).timestamp()


class Proc:
    """An extension process: its checks answer from `returns`, its actions and events are recorded."""

    def __init__(self):
        self.state, self.returns, self.calls, self.events = "running", {}, [], []

    async def call(self, method, timeout=None, **kw):
        self.calls.append((method, kw))
        if method == "check":
            r = self.returns.get(kw["kind"], [])
            return r if isinstance(r, dict) else {"ok": True, "nudges": r}
        if method == "act":
            return {"ok": True, "result": f"did {kw['action']}"}
        if method == "nudge_event":
            self.events.append((kw["what"], kw["nudge"]["about"]))
            return {"ok": True}
        return {"ok": True}


KINDS = [
    {"kind": "rest", "title": "Time to train", "checked": True, "deliver": "when_home", "where": "auto",
     "repeat": 86400.0, "may_be_urgent": False, "requested": False, "outside": True, "triggers": {"every": 3600.0}},
    {"kind": "thirsty", "title": "A plant needs water", "checked": True, "deliver": "next_talk", "where": "auto",
     "repeat": None, "may_be_urgent": False, "requested": False, "outside": False, "triggers": {"on_state": ["s"]}},
    {"kind": "due", "title": "Reminders", "checked": False, "deliver": "now", "where": "auto", "repeat": None,
     "may_be_urgent": False, "requested": True, "outside": False, "triggers": {}},
    {"kind": "story", "title": "Breaking news", "checked": False, "deliver": "now", "where": "auto", "repeat": None,
     "may_be_urgent": True, "requested": False, "outside": True, "triggers": {}},
    {"kind": "digest", "title": "News digest", "checked": False, "deliver": "briefing", "where": "auto",
     "repeat": None, "may_be_urgent": False, "requested": False, "outside": True, "triggers": {}},
    {"kind": "bye", "title": "Take an umbrella", "checked": False, "deliver": "when_leaving", "where": "auto",
     "repeat": None, "may_be_urgent": False, "requested": False, "outside": True, "triggers": {}},
]
ACTIONS = [{"name": "rest_day", "title": "Log a rest day", "kinds": ["rest"], "tier": "act"},
           {"name": "cancel_gym", "title": "Cancel the gym booking", "kinds": ["rest"], "tier": "restricted"}]


class Host:
    def __init__(self):
        self.cfg = WinstonConfig.model_validate({"llm": {"model": "fake"},
                                                 "speak_up": {"home": "person.someone", "max_per_day": 3},
                                                 "announce": {"quiet_hours": "22:00-07:00"}})
        self.described = {"gym": {"manifest": {"name": "Gym"}, "kinds": KINDS, "actions": ACTIONS,
                                  "has": {"on_nudge": True}}}
        self.procs = {"gym": Proc()}
        self.kind_choices = {}
        self.notes, self.pended = [], []

    def config(self, ext_id):
        return ExtensionConfig(enabled=True, kinds=self.kind_choices)

    def enabled(self, ext_id):
        return True

    def _note(self, ext_id, where, msg):
        self.notes.append((where, msg))

    def pend_action(self, rec, act, channel):
        self.pended.append((rec["id"], act["name"], channel))
        return {"waiting": True, "note": "needs your confirmation"}


class World:
    """The clock, where you are, whether a conversation runs, and what reached a speaker or the phone."""

    def __init__(self, start: str):
        self.now = at(start)
        self.home, self.talking, self.speakers = True, False, 1
        self.said: list[tuple[str, str, str]] = []        # (where, kind, say)

    async def deliver(self, ev, where):
        if where == "speakers" and self.speakers:
            self.said.append(("speakers", ev["kind"], ev["say"], ev))
            return "speakers:1"
        self.said.append(("phone", ev["kind"], ev["say"], ev))
        return "phone"

    async def presence(self):
        return self.home


def engine(world: World, host: Host | None = None) -> tuple[Nudges, Host]:
    """A fresh engine with nothing remembered."""
    (DATA / "state" / "nudges.json").unlink(missing_ok=True)
    host = host or Host()
    n = Nudges(host, deliver=world.deliver, busy=lambda: world.talking, home=world.presence, clock=lambda: world.now)
    return n, host


def gap(days: int, since: str = "2026-09-26T18:00", level: int | None = None) -> dict:
    return {"about": since, "say": f"It has been {days} days since your last workout, sir.",
            "facts": {"days_since": days}, "level": level or (2 if days >= 5 else 1)}


async def tick(n: Nudges, world: World, secs: float = 0):
    world.now += secs
    n._home = (-1e12, None)                  # presence read afresh, as after its 30 s
    await n.evaluate()


def said_kinds(world: World) -> list[str]:
    return [k for _, k, *_ in world.said]


async def rules():
    # A checked nudge is said once, then again only when repeat has passed or its level rises.
    w = World("2026-09-29T18:00")
    n, host = engine(w)
    proc = host.procs["gym"]
    proc.returns["rest"] = [gap(3)]
    await n.check("gym", "rest")
    await tick(n, w)
    await tick(n, w, 60)
    await n.check("gym", "rest")
    await tick(n, w, 60)
    expect("a nudge is said once, on the speakers while you are home", said_kinds(w) == ["Time to train"]
           and w.said[0][0] == "speakers", w.said)
    await asyncio.sleep(0.01)                       # its events travel in the background
    expect("its extension hears that it was said", ("said", "2026-09-26T18:00") in proc.events, proc.events)
    await tick(n, w, 3600 * 3)
    expect("not said again within its repeat", len(w.said) == 1, w.said)
    proc.returns["rest"] = [gap(5)]
    await n.check("gym", "rest")
    await tick(n, w)
    expect("said again when its level rises (firmer, day five)", len(w.said) == 2 and "5 days" in w.said[1][2], w.said)
    w.now = at("2026-09-30T20:00")
    await n.check("gym", "rest")
    await tick(n, w)
    expect("not again within a day of the last time", len(w.said) == 2, w.said)
    w.now = at("2026-09-30T21:30")
    await n.check("gym", "rest")
    await tick(n, w)
    expect("said again once its repeat (1 day) has passed", len(w.said) == 3, w.said)

    # Quiet hours and conversations hold; urgent and requested ones go through quiet hours.
    w = World("2026-09-29T23:00")
    n, host = engine(w)
    await n.handle("gym", "nudge.push", {"kind": "story", "nudge": {"about": "a", "say": "Story A."}})
    await n.handle("gym", "nudge.push", {"kind": "story", "nudge": {"about": "b", "say": "Story B.", "urgent": True}})
    await n.handle("gym", "nudge.push", {"kind": "due", "nudge": {"about": "r1", "say": "Call Mum."}})
    await tick(n, w)
    says = [s for *_, s, _ in w.said]
    expect("quiet hours hold an ordinary nudge", "Story A." not in says, says)
    expect("an urgent one of a kind that may be urgent is spoken in quiet hours", "Story B." in says, says)
    expect("what you asked for (a reminder) comes in quiet hours", "Call Mum." in says, says)
    rec = n.records[nudge_id("gym", "story", "a")]
    expect("the held one says why", rec["state"] == "held" and rec["held_why"] == "quiet hours", rec)
    w.talking = True
    await tick(n, w, 8 * 3600)                           # 07:00, quiet hours over, but you are talking
    expect("a running conversation holds it past quiet hours", "Story A." not in [s for *_, s, _ in w.said],
           n.records[nudge_id("gym", "story", "a")]["held_why"])
    w.talking = False
    await tick(n, w, 31)
    expect("then it is said, once the conversation has been quiet", [s for *_, s, _ in w.said].count("Story A.") == 1)

    # The daily limit moves the rest to the briefing; the briefing comes at the first conversation.
    w = World("2026-09-29T09:00")
    n, host = engine(w)
    expect("the first conversation after 06:00 is the briefing, even with nothing in it", await n.mentions("hello")
           == [] and n.state["briefed"] == "2026-09-29")
    w.now = at("2026-09-29T10:00")
    for i in range(5):
        await n.handle("gym", "nudge.push", {"kind": "story", "nudge": {"about": f"s{i}", "say": f"Story {i}."}})
    await tick(n, w)
    expect("past speak_up.max_per_day (3) spoken nudges the rest wait", len(w.said) == 3, w.said)
    later = [r for r in n.records.values() if r["state"] != "said"]
    expect("they wait for the next briefing, and say so", all(r["held_why"] == "past today's limit of spoken "
                                                             "nudges" for r in later) and len(later) == 2, later)
    w.now = at("2026-09-29T11:00")
    expect("a later conversation that day is not the briefing", await n.mentions("turn on the lamp") == [])
    got = await n.mentions("what's new?")
    expect("asking what's new gives them", sorted(i["say"] for i in got) == ["Story 3.", "Story 4."], got)
    expect("each is handed out once", await n.mentions("what's new?") == [])

    # A briefing kind never interrupts; the first conversation after briefing_from gets it.
    w = World("2026-09-29T21:00")
    n, host = engine(w)
    await n.handle("gym", "nudge.push", {"kind": "digest", "nudge": {"about": "d1", "say": "Your digest."}})
    for _ in range(3):
        await tick(n, w, 3600)
    expect("a kind set to the morning briefing never interrupts", w.said == [], w.said)
    w.now = at("2026-09-30T05:30")
    expect("not before speak_up.briefing_from (06:00)", await n.mentions("good morning") == [])
    w.now = at("2026-09-30T06:40")
    got = await n.mentions("good morning")
    expect("the first conversation after it gets the briefing", [i["say"] for i in got] == ["Your digest."]
           and got[0]["part"] == "briefing", got)
    await n.handle("gym", "nudge.push", {"kind": "digest", "nudge": {"about": "d2", "say": "More."}})
    expect("and only the first that day", await n.mentions("lights on") == [])

    # Next talk: mentioned in the next conversation, never spoken on its own.
    w = World("2026-09-29T12:00")
    n, host = engine(w)
    host.procs["gym"].returns["thirsty"] = [{"about": "sensor.ficus", "say": "The ficus is thirsty, sir.",
                                             "facts": {"moisture": 22}}]
    await n.check("gym", "thirsty")
    await tick(n, w, 600)
    got = await n.mentions("what's the weather")
    expect("a next-talk nudge waits for you to talk, then is mentioned once", w.said == [] and len(got) == 1
           and got[0]["part"] == "next_talk" and (await n.mentions("hm")) == [], got)
    expect("facts marked as its own (outside=False) say so", got[0]["outside"] is False, got)

    # When home: held while away, said when you get home; auto goes to the phone for what may come away.
    w = World("2026-09-29T15:00")
    w.home = False
    n, host = engine(w)
    host.procs["gym"].returns["rest"] = [gap(3)]
    await n.check("gym", "rest")
    await tick(n, w)
    expect("when_home holds while you are away", w.said == [] and n.records[nudge_id("gym", "rest", "2026-09-26T18:00")]
           ["held_why"] == "until you get home")
    w.home = True
    n.arrived()
    await tick(n, w, 5)
    expect("and it is said when you arrive, checked again first", said_kinds(w) == ["Time to train"]
           and sum(1 for m, kw in host.procs["gym"].calls if m == "check") == 2, host.procs["gym"].calls)
    w2 = World("2026-09-29T15:00")
    w2.home = False
    n2, _ = engine(w2)
    await n2.handle("gym", "nudge.push", {"kind": "story", "nudge": {"about": "x", "say": "Big story."}})
    await tick(n2, w2)
    expect("auto: to the phone while you are away", w2.said and w2.said[0][0] == "phone", w2.said)
    await n2.handle("gym", "nudge.push", {"kind": "bye", "nudge": {"about": "u", "say": "Take an umbrella."}})
    w2.home = True
    await tick(n2, w2, 60)
    expect("when_leaving waits while you are home", "Take an umbrella." not in [s for *_, s, _ in w2.said])
    w2.home = False
    await n2.left()
    expect("and comes as you leave", "Take an umbrella." in [s for *_, s, _ in w2.said], w2.said)

    # Later: "Yeah I know Winston, I'm training tomorrow, remind me after work".
    w = World("2026-09-29T18:30")
    n, host = engine(w)
    proc = host.procs["gym"]
    proc.returns["rest"] = [gap(3)]
    await n.check("gym", "rest")
    await tick(n, w)
    nid = nudge_id("gym", "rest", "2026-09-26T18:00")
    r = await n.follow_up(nid, "later", "tomorrow 17:30", reason="training tomorrow", channel="voice")
    expect("later with a time memory filled in (work ends 17:00, so 17:30)", r.get("put_off_until") ==
           "tomorrow at 17:30", r)
    await asyncio.sleep(0.01)
    expect("its extension hears it was put off", any(e[0] == "put_off" for e in proc.events), proc.events)
    await tick(n, w, 3600)
    w.now = at("2026-09-30T17:29")
    await tick(n, w)
    expect("held until then", len(w.said) == 1, w.said)
    proc.returns["rest"] = [gap(4)]
    w.now = at("2026-09-30T17:31")
    await tick(n, w)
    expect("then checked again and said, with the reason he gave", len(w.said) == 2 and
           w.said[1][3]["reason"] == "training tomorrow" and "4 days" in w.said[1][2], w.said[-1])

    w = World("2026-09-29T18:30")
    n, host = engine(w)
    proc = host.procs["gym"]
    proc.returns["rest"] = [gap(3)]
    await n.check("gym", "rest")
    await tick(n, w)
    r = await n.follow_up(nid, "later", "tomorrow after work", reason="training tomorrow", channel="voice")
    expect("\"tomorrow after work\" with nothing in memory: when you get home tomorrow",
           r.get("put_off_until", "").startswith("when you get home tomorrow, or at 18:00"), r)
    w.home = False
    w.now = at("2026-09-30T08:00")
    await tick(n, w)                                    # off to work
    proc.returns["rest"] = []                           # and he trained at lunch
    w.home = True
    w.now = at("2026-09-30T17:10")
    await tick(n, w)
    rec = n.records[nid]
    expect("home from work: checked again; he trained, so nothing is said and it ends", len(w.said) == 1
           and rec["state"] == "ended", (w.said, rec["state"]))

    w = World("2026-09-29T18:30")
    n, host = engine(w)
    proc = host.procs["gym"]
    proc.returns["rest"] = [gap(3)]
    await n.check("gym", "rest")
    await tick(n, w)
    await n.follow_up(nid, "later", "tomorrow after work", reason="training tomorrow")
    w.now = at("2026-09-30T12:00")
    await tick(n, w)
    expect("working from home: nothing at noon", len(w.said) == 1)
    w.now = at("2026-09-30T18:01")
    await tick(n, w)
    expect("home all day: said at speak_up.fallback_time (18:00), with the reason", len(w.said) == 2
           and w.said[1][3]["reason"] == "training tomorrow", w.said)

    # Dismiss and mute; why.
    w = World("2026-09-29T18:30")
    n, host = engine(w)
    proc = host.procs["gym"]
    proc.returns["rest"] = [gap(3)]
    await n.check("gym", "rest")
    await tick(n, w)
    await n.follow_up(nid, "dismiss")
    w.now = at("2026-09-30T19:00")
    await n.check("gym", "rest")
    await tick(n, w)
    expect("dismissed: not said again, even past its repeat", len(w.said) == 1, w.said)
    proc.returns["rest"] = [gap(5)]
    await n.check("gym", "rest")
    await tick(n, w)
    expect("until its level rises", len(w.said) == 2, w.said)
    why = await n.follow_up(nid, "why")
    expect("why names the extension, the kind and how it came", why.get("extension") == "Gym" and
           why.get("kind") == "Time to train" and "check" in why.get("because", "") and "every 1 hours"
           in why.get("because", "") and why["facts"] == {"days_since": 5}, why)
    r = await n.follow_up(nid, "mute", "Monday")
    expect("mute until Monday", r.get("until", "").startswith("Monday"), r)
    proc.returns["rest"] = [gap(8, level=3)]
    await n.check("gym", "rest")
    await tick(n, w)
    expect("a muted kind holds even a higher level", len(w.said) == 2)
    w.now = at("2026-10-05T09:00")
    await n.check("gym", "rest")
    await tick(n, w)
    expect("and speaks again once the mute is over", len(w.said) == 3, w.said)

    await n.handle("gym", "nudge.push", {"kind": "due", "nudge": {"about": "r2", "say": "Bins out."}})
    await tick(n, w)
    rid = nudge_id("gym", "due", "r2")
    await n.follow_up(rid, "dismiss")
    expect("a pushed nudge dismissed ends", n.records[rid]["state"] == "ended")

    # Its own actions, tiers, and nudges that end.
    r = await n.follow_up(nid, "rest_day", channel="voice")
    expect("an action of its own runs", r.get("done") == "Log a rest day" and r.get("result") == "did rest_day", r)
    r = await n.follow_up(nid, "cancel_gym", channel="voice")
    expect("a restricted action from the voice waits for your confirmation", r.get("waiting") and
           host.pended and host.pended[-1][1] == "cancel_gym", r)
    r = await n.follow_up(nid, "nonsense")
    expect("an unknown action lists the ones there are", "later, dismiss, mute, why, rest_day" in r.get("error", ""), r)
    await n.handle("gym", "nudge.push", {"kind": "story", "nudge": {"about": "old", "say": "Old news.",
                                                                    "expires": "2026-10-05T08:00:00+02:00"}})
    await tick(n, w)
    expect("an expired nudge ends unsaid", "Old news." not in [s for *_, s, _ in w.said]
           and n.records[nudge_id("gym", "story", "old")]["state"] == "ended")
    await n.handle("gym", "nudge.push", {"kind": "due", "nudge": {"about": "r3", "say": "Later.",
                                                                  "not_before": "2026-10-05T12:00:00+02:00"}})
    await n.handle("gym", "nudge.withdraw", {"kind": "due", "about": "r3"})
    w.now = at("2026-10-05T12:30")
    await tick(n, w)
    expect("a withdrawn one is never said", "Later." not in [s for *_, s, _ in w.said])
    await n.handle("gym", "nudge.push", {"kind": "due", "nudge": {"about": "r4", "say": "At one.",
                                                                  "not_before": "2026-10-05T13:00:00+02:00"}})
    await tick(n, w, 60)
    expect("not_before holds a reminder", "At one." not in [s for *_, s, _ in w.said])
    w.now = at("2026-10-05T13:00")
    await tick(n, w)
    expect("and it comes at its time", "At one." in [s for *_, s, _ in w.said])

    # Switched off on the card; nothing to deliver it with; a restart.
    host.kind_choices = {"story": {"enabled": False}}
    await n.handle("gym", "nudge.push", {"kind": "story", "nudge": {"about": "off", "say": "Switched off."}})
    await tick(n, w)
    expect("a kind switched off on its card says nothing", "Switched off." not in [s for *_, s, _ in w.said])
    host.kind_choices = {}
    w2 = World("2026-10-05T14:00")
    host2 = Host()
    (DATA / "state" / "nudges.json").unlink(missing_ok=True)

    async def nowhere(ev, where):
        return None
    n2 = Nudges(host2, deliver=nowhere, busy=lambda: False, home=w2.presence, clock=lambda: w2.now)
    await n2.handle("gym", "nudge.push", {"kind": "due", "nudge": {"about": "r5", "say": "Stretch."}})
    await tick(n2, w2)
    rec = n2.records[nudge_id("gym", "due", "r5")]
    expect("no speaker, no phone: held, and says so", rec["state"] == "held" and "nothing can deliver" in
           rec["held_why"], rec)
    n3 = Nudges(host2, deliver=w2.deliver, busy=lambda: False, home=w2.presence, clock=lambda: w2.now)
    await tick(n3, w2)
    await tick(n3, w2, 60)
    expect("after a restart it is said, once", [s for *_, s, _ in w2.said] == ["Stretch."], w2.said)
    n4 = Nudges(host2, deliver=w2.deliver, busy=lambda: False, home=w2.presence, clock=lambda: w2.now)
    await tick(n4, w2, 60)
    expect("and a second restart says nothing twice", len(w2.said) == 1, w2.said)

    lines = [json.loads(ln) for ln in (DATA / "logs" / "nudges.jsonl").read_text().splitlines()]
    whats = {ln["what"] for ln in lines}
    expect("every step is logged to logs/nudges.jsonl", {"new", "held", "said", "put off", "dismissed", "muted",
                                                          "ended", "action"} <= whats, whats)
    expect("with the reason for each hold", any(ln["what"] == "held" and ln["why"] == "quiet hours" for ln in lines))


def times():
    w = World("2026-09-29T20:00")
    n, host = engine(w)
    cases = {"in ten minutes": "today at 20:10", "tomorrow 17:30": "tomorrow at 17:30", "tomorrow evening":
             "tomorrow at 19:00", "Monday": "Monday 5 October at 08:00", "tonight": "error",
             "when I'm home": "when you get home", "tomorrow after work": "when you get home tomorrow, or at 18:00 "
             "if you are home by then", "Friday when I get home": "when you get home Friday, or at 18:00 if you are "
             "home by then", "after work": "error", "": "error"}
    bad = {}
    for text, want in cases.items():
        got = n.read_when(text)
        got = "error" if isinstance(got, str) else got["text"]
        if got != want:
            bad[text] = got
    expect("follow-up times read by code, said back as set", not bad, bad)
    host.cfg.speak_up.home = None
    expect("\"when I'm home\" with no person entity asks for a time", isinstance(n.read_when("when I'm home"), str))
    host.cfg.speak_up.day_parts["evening"] = "18:30"
    host.cfg.speak_up.home = "person.someone"
    expect("parts of the day come from speak_up.day_parts", n.read_when("tomorrow evening")["text"] ==
           "tomorrow at 18:30")


def setup_winstond(ha_port: int, api_port: int):
    import re
    (DATA / "state" / "nudges.json").unlink(missing_ok=True)
    cfg = {"llm": {"model": "fake", "base_url": "http://127.0.0.1:9/v1"},
           "channels": {"voice": {"enabled": False}, "satellite": {"enabled": False}, "discord": {"enabled": False}},
           "providers": {"home_assistant": {"url": f"http://127.0.0.1:{ha_port}", "token_env": "HA_TOKEN",
                                            "expose": {"light.taklampa": {}}}},
           "control": {"port": api_port, "host": "127.0.0.1"},
           "speak_up": {"home": "person.someone", "phone": "notify.mobile_app_test_phone"},
           "extensions": {"plants": {"enabled": True, "settings": {"sensor": "sensor.ficus_moisture"}},
                          "remind": {"enabled": True}, "trainer": {"enabled": True}}}
    winstond_data(DATA, cfg, fixtures=("trainer.py",))
    doc = (harness.REPO / "docs" / "extensions.md").read_text()
    for ext_id in ("plants", "remind"):
        block = next(b for b in re.findall(r"```python\n(# /// script.*?)```", doc, re.S) if f'id = "{ext_id}"' in b)
        (DATA / "extensions" / f"{ext_id}.py").write_text(block)


async def through_winstond():
    import time
    import httpx
    from fake_ha import FakeHA
    ha_port, api_port = free_port(), free_port()
    setup_winstond(ha_port, api_port)
    from winston.api.app import create_app
    from winston.config import load
    from winston.core.live import LiveEvent
    from winston.core.speak_up import speaking_up

    async with FakeHA(ha_port, seed="home") as fake:
        app = create_app(load(), autostart=False)
        stop_server = await serve(app, api_port)
        host = app.state.extensions
        nudges = host.nudges
        offset = [0.0]
        nudges.clock = lambda: time.time() + offset[0]
        noon = at("2026-10-01T12:00") - time.time()
        offset[0] = noon                                    # a Thursday at noon, whatever the real time
        await host.start()
        host.scheduler.start()
        api = httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}", timeout=30)

        async def running(ext_id):
            return (await api.get(f"/api/extensions/{ext_id}")).json()["state"] == "running"
        ok = all([await until(lambda: running(i), 40) for i in ("plants", "remind", "trainer")])
        expect("three extensions with kinds run", ok)
        expect("the brain gets follow_up while any has kinds", await speaking_up(load()))

        heard: dict[str, list[dict]] = {"Kitchen": [], "Office": []}

        async def speaker(channel: str, room: str):
            async with httpx.AsyncClient(timeout=httpx.Timeout(5, read=None)) as c:
                async with c.stream("GET", f"http://127.0.0.1:{api_port}/api/events",
                                    params={"channel": channel, "room": room}) as r:
                    async for line in r.aiter_lines():
                        if line.startswith("data: "):
                            heard[room].append(json.loads(line[6:]))
        listeners = [asyncio.create_task(speaker("voice", "Kitchen")), asyncio.create_task(speaker("satellite", "Office"))]
        await until(lambda: asyncio.sleep(0, result=app.state.hub.speakers() == 2), 5)
        await api.post("/api/rooms/Kitchen/sync", json={"origin": "test", "messages": [
            {"role": "user", "content": "lights on in here"}]})

        # A checked kind: the trainer's tool changes what there is to say, its check runs after it.
        r = await api.post("/api/extensions/trainer/tools/set_gap", json={"days": 3})
        expect("a tool call runs", r.json().get("ok"), r.text)
        got = await until(lambda: asyncio.sleep(0, result=bool(heard["Kitchen"])), 10)
        await nudges.evaluate()
        got = got or await until(lambda: asyncio.sleep(0, result=bool(heard["Kitchen"])), 5)
        ev = heard["Kitchen"][0] if heard["Kitchen"] else {}
        expect("its check ran after the tool, and the nudge reached the speaker in the room you are in",
               got and ev.get("type") == "nudge" and ev.get("extension") == "Trainer" and not heard["Office"],
               (heard, nudges.listing()))
        expect("it carries what the channel words it from: the line, the facts, the follow-ups",
               "3 days" in ev.get("say", "") and ev.get("facts", {}).get("days_since") == 3
               and [a["name"] for a in ev.get("actions", [])] == ["rest_day", "cancel_gym"], ev)
        feed = [e for e in app.state.feed.backlog() if e.type == "nudge"]
        expect("the Talk page shows it", feed and feed[-1].text == ev.get("say") and feed[-1].name.startswith(
            "speakers"), feed)
        nid = ev.get("id", "")
        for _ in range(3):
            await nudges.evaluate()
        expect("said once", len(heard["Kitchen"]) == 1, heard)

        # "Yeah I know Winston, I'm training tomorrow, remind me after work."
        app.state.feed.publish(LiveEvent(channel="voice", type="user", text="yeah I know"))
        r = (await api.post(f"/api/nudges/{nid}/follow_up", json={"action": "later", "when": "tomorrow after work",
                                                                  "reason": "training tomorrow",
                                                                  "channel": "voice"})).json()
        expect("put off to when he gets home tomorrow", r.get("put_off_until", "").startswith(
            "when you get home tomorrow"), r)

        async def cli(*args: str) -> str:
            """`winston ext ...` in its own process, while this loop keeps serving it."""
            proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "winston.cli.main", "ext", *args,
                                                        env={**os.environ, "WINSTON_DATA": str(DATA)},
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await asyncio.wait_for(proc.communicate(), 60)
            return out.decode()
        out = await cli("nudges", "trainer")
        expect("`winston ext nudges` says where it stands and why", "put off until when you get home tomorrow" in out
               and nid in out, out)
        why = (await api.post(f"/api/nudges/{nid}/follow_up", json={"action": "why"})).json()
        expect("\"why are you telling me this?\" names the extension and the rule", why.get("extension") == "Trainer"
               and why.get("kind") == "Time to train" and why.get("delivered") == "when you are home", why)
        offset[0] = at("2026-10-02T08:00") - time.time()
        await fake.change("person.someone", "not_home")
        await until(lambda: asyncio.sleep(0, result=nudges._home[1] is False), 5)
        await nudges.evaluate()
        offset[0] = at("2026-10-02T17:10") - time.time()
        app.state.feed.publish(LiveEvent(channel="voice", type="user", text="I'm home, lights on"))
        await fake.change("person.someone", "home")
        await until(lambda: asyncio.sleep(0, result=nudges._home[1] is True), 5)
        await nudges.evaluate()
        expect("home, but talking: it waits for the conversation to end", len(heard["Kitchen"]) == 1
               and nudges.records[nid]["held_why"] == "a conversation is running", nudges.records[nid])
        app.state.feed.talked_at -= 31                      # 30 s of quiet
        await nudges.evaluate()
        await until(lambda: asyncio.sleep(0, result=len(heard["Kitchen"]) == 2), 5)
        back = heard["Kitchen"][1] if len(heard["Kitchen"]) > 1 else {}
        expect("home from work the next day: checked again, said, with the reason back",
               back.get("reason") == "training tomorrow" and back.get("id") == nid, heard["Kitchen"])

        # Its own actions; a restricted one waits for you, and the phone can confirm it.
        r = (await api.post(f"/api/nudges/{nid}/follow_up", json={"action": "cancel_gym", "channel": "voice"})).json()
        pending = (await api.get("/api/confirmations")).json()
        phone = [w for w in fake.writes if w["type"] == "service/notify.mobile_app_test_phone"]
        expect("a restricted action from the voice waits, on the Talk page and the phone", r.get("waiting")
               and pending and phone and phone[-1]["data"]["actions"][0]["title"] == "Confirm", (r, pending, phone))
        cid = pending[-1]["id"] if pending else ""
        await fake.fire("mobile_app_notification_action", {"action": f"WINSTON_CONFIRM_{cid}"})
        done = await until(lambda: asyncio.sleep(0, result=host.pending.get(cid) is not None
                                                 and host.pending[cid].state == "ok"), 10)
        evs = (await api.post("/api/extensions/trainer/tools/events", json={})).json()["result"]
        expect("Confirm on the phone runs it", done and evs["cancelled"], (host.pending.get(cid) and
                                                                          host.pending[cid].view(), evs))
        r = (await api.post(f"/api/nudges/{nid}/follow_up", json={"action": "rest_day", "channel": "voice"})).json()
        expect("an action of its own runs from the conversation", r.get("result") == "Logged a rest day.", r)
        ended = await until(lambda: asyncio.sleep(0, result=nudges.records[nid]["state"] == "ended"), 10)
        evs = (await api.post("/api/extensions/trainer/tools/events", json={})).json()["result"]
        expect("its check ran after the action and the nudge ended; the extension heard each step", ended and
               [e[0] for e in evs["events"]] == ["said", "put_off", "said", "ended"], evs)

        # A reminder while you are away goes to the phone, with buttons.
        await fake.change("person.someone", "not_home")
        await until(lambda: asyncio.sleep(0, result=nudges._home[1] is False), 5)
        offset[0] = 0                           # the reminder's time is the extension's, on the real clock
        r = (await api.post("/api/extensions/remind/tools/add", json={"what": "call Mum", "at": "in 20 minutes"})).json()
        expect("the docs' reminder extension sets one", r.get("ok"), r)
        writes = len(fake.writes)
        await nudges.evaluate()
        expect("not before its time", len(fake.writes) == writes)
        offset[0] += 21 * 60
        await nudges.evaluate()
        phone = [w for w in fake.writes[writes:] if w["type"] == "service/notify.mobile_app_test_phone"]
        expect("at its time, away from home: to the phone, with Later and Done", phone and
               phone[0]["message"] == "A reminder, sir: call Mum." and [a["title"] for a in phone[0]["data"]["actions"]]
               == ["When I'm home", "Done"], phone)
        rid = phone[0]["data"]["tag"] if phone else ""
        await fake.fire("mobile_app_notification_action", {"action": f"WINSTON_DISMISS_{rid}"})
        expect("Done on the phone ends it", await until(lambda: asyncio.sleep(
            0, result=nudges.records.get(rid, {}).get("state") == "ended"), 5))
        await fake.change("person.someone", "home")

        # The plants' check runs when its sensor changes; next-talk and briefing come in the conversation.
        await api.post("/api/extensions/trainer/tools/story", json={"about": "https://example.com/a",
                                                                   "say": "A story broke."})
        await fake.change("sensor.ficus_moisture", "21")
        found = await until(lambda: asyncio.sleep(0, result=any(r["kind"] == "thirsty" for r in nudges.records.values())),
                            10)
        expect("the plants' check ran when its sensor changed", found, nudges.listing())
        for _ in range(3):
            await nudges.evaluate()
        expect("neither interrupts", len(heard["Kitchen"]) == 2 and not heard["Office"], heard)
        offset[0] = at("2026-10-03T07:30") - time.time()
        got = (await api.post("/api/nudges/mentions", json={"text": "good morning"})).json()
        expect("the first conversation of the morning mentions both",
               sorted(g["part"] for g in got) == ["briefing", "next_talk"], got)
        again = (await api.post("/api/nudges/mentions", json={"text": "and the lights"})).json()
        expect("once", again == [], again)

        card = (await api.get("/api/extensions/trainer")).json()
        expect("its card lists its kinds and nudges", [k["title"] for k in card["kinds"]] == ["A story", "Time to train"]
               and card["nudges"], card["kinds"])
        out = await cli("check", "plants", "thirsty")
        expect("`winston ext check` shows what the check would say, saying nothing", "sensor.ficus_moisture" in out
               and "water" in out, out)
        expect("nudges are kept in data/state/nudges.json", (DATA / "state" / "nudges.json").is_file())

        for t in listeners:
            t.cancel()
        await api.aclose()
        await nudges.stop()
        await host.scheduler.stop()
        await host.stop_all()
        await stop_server()


async def main():
    times()
    await rules()
    await through_winstond()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        shutil.rmtree(DATA, ignore_errors=True)
    finish()
