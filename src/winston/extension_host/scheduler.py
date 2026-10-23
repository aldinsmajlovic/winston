"""The core owns time: every extension's schedules, timers and triggers run from here, so tests can
move one clock, missed runs are caught up in one place, and the card shows the next run.

- `every` runs once when the extension starts, then each interval after its last run; with `align`
  it runs on the clock instead (every 15 minutes at :00, :15, ...).
- `at` and `cron` run at their times in the home's time zone. One missed while Winston was down runs
  once when it is back, unless the job says `catch_up=False`.
- Timers from `ctx.later` are kept on disk and run their job when due, late if the extension was
  not running then.
- Home Assistant triggers come over its websocket: state changes (with `to`, `from` and `for`), the
  person entity arriving or leaving (for extensions with the presence permission), and events.
- A checked kind's triggers (`@ext.nudge(..., every=, on_state=, ...)`) run its check the same way,
  as a job named `check:<kind>:<trigger>`.
- Speaking up hears the person entity arrive and leave, and the phone's notification buttons
  (`mobile_app_notification_action`).
- Announcements from Home Assistant (`announce.ha_event`) come over the same connection, extensions or
  not; HA lets only an admin token subscribe to a custom event.
A job never runs twice at once; a run still going when the next is due is skipped and logged."""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, time as dtime, timedelta
from typing import Any

import websockets
from loguru import logger
from winston_sdk.context import local_zone

from winston.extension_host.sources import read_json, state_dir, write_json

TIME_KINDS = ("every", "at", "cron")
PHONE_EVENT = "mobile_app_notification_action"


def kind_jobs(described: dict[str, Any]) -> list[dict[str, Any]]:
    """A checked kind's triggers as jobs that run its check."""
    out = []
    for k in described.get("kinds", []):
        for trig, value in (k.get("triggers") or {}).items():
            trigger = {"every": lambda v: {"kind": "every", "seconds": v, "align": False},
                       "at": lambda v: {"kind": "at", "time": v, "days": None},
                       "cron": lambda v: {"kind": "cron", "expr": v},
                       "on_state": lambda v: {"kind": "on_state", "entity": v, "to": None, "from": None, "for": None},
                       "on_event": lambda v: {"kind": "on_event", "event_type": v},
                       "on_arrive": lambda v: {"kind": "on_arrive"},
                       "on_leave": lambda v: {"kind": "on_leave"}}[trig](value)
            out.append({"name": f"check:{k['kind']}:{trig}", "trigger": trigger, "timeout": 10, "catch_up": False,
                        "check": k["kind"]})
    return out


# cron

def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/")
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-"))
        else:
            a = int(part)
            b = hi if step > 1 else a
        out.update(range(a, b + 1, step))
    return out


def cron_next(expr: str, after: datetime) -> datetime:
    """The first time after `after` that five-field cron `expr` names, in `after`'s zone."""
    mi, ho, dom, mon, dow = expr.split()
    minutes, hours = _field(mi, 0, 59), _field(ho, 0, 23)
    days, months = _field(dom, 1, 31), _field(mon, 1, 12)
    weekdays = {d % 7 for d in _field(dow, 0, 7)}                     # 0 and 7 are both Sunday
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(2 * 366 * 24):                                     # at most about two years of hours
        if t.month not in months:
            t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
            continue
        dom_ok, dow_ok = t.day in days, t.isoweekday() % 7 in weekdays
        day_ok = (dom_ok and dow_ok) if dom == "*" or dow == "*" else (dom_ok or dow_ok)
        if not day_ok:
            t = t.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if t.hour not in hours:
            t = t.replace(minute=0) + timedelta(hours=1)
            continue
        later = sorted(m for m in minutes if m >= t.minute)
        if later:
            return t.replace(minute=later[0])
        t = t.replace(minute=0) + timedelta(hours=1)
    raise ValueError(f"cron {expr!r} never runs")


def at_next(when: dtime, days: list[int] | None, after: datetime) -> datetime:
    for d in range(9):
        candidate = datetime.combine(after.date() + timedelta(days=d), when, tzinfo=after.tzinfo)
        if candidate > after and (days is None or candidate.weekday() in days):
            return candidate
    raise ValueError("no day to run on")


def every_aligned(seconds: float, after: datetime) -> datetime:
    midnight = datetime.combine(after.date(), dtime(0), tzinfo=after.tzinfo)
    n = int((after - midnight).total_seconds() // seconds) + 1
    return midnight + timedelta(seconds=n * seconds)


class Scheduler:
    def __init__(self, host, services):
        self.host, self.services = host, services
        self.zone = local_zone()
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._in_flight: set[tuple[str, str]] = set()
        self._next: dict[tuple[str, str], float] = {}
        self._ran_since_start: dict[str, float] = {}      # extension -> the start its "every" jobs ran for
        self._states: dict[str, str] = {}                  # entity -> its latest state, from the events
        self._ha_wants: tuple | None = None
        self._jobs = False                                 # the extensions' clock and triggers run
        self.on_phone_action = None                        # set by winstond: a notification button pressed
        self.announce_event: str | None = None             # set by winstond: Home Assistant's event to announce
        self.on_announce = None                            # and what to do with one

    def now(self) -> datetime:
        return datetime.now(self.zone)

    def start(self, jobs: bool = True) -> None:
        """Home Assistant's events for announcements, and with jobs the extensions' clock and triggers.
        Called again with jobs, it adds those."""
        if not self._tasks:
            self._tasks.append(asyncio.create_task(self._ha_loop(), name="Home Assistant events"))
        if jobs and not self._jobs:
            self._jobs = True
            self._tasks.append(asyncio.create_task(self._loop(), name="extension scheduler"))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks, self._jobs = [], False

    def wake(self) -> None:
        self._wake.set()

    def next_run(self, ext_id: str, job: str) -> float | None:
        return self._next.get((ext_id, job))

    # state kept per extension: when each job last ran

    def _last(self, ext_id: str) -> dict[str, float]:
        return read_json(state_dir(ext_id) / "schedule.json", {}).get("last", {})

    def _ran(self, ext_id: str, job: str, at: float) -> None:
        write_json(state_dir(ext_id) / "schedule.json", {"last": self._last(ext_id) | {job: at}})

    def setting(self, ext_id: str, name: str) -> Any:
        """A setting as saved, else its described default."""
        saved = self.host.config(ext_id).settings
        if name in saved:
            return saved[name]
        prop = self.host.described.get(ext_id, {}).get("settings", {}).get("schema", {}).get("properties", {})
        return prop.get(name, {}).get("default")

    def _resolve(self, ext_id: str, value: Any) -> Any:
        return self.setting(ext_id, value["from"]) if isinstance(value, dict) and "from" in value else value

    def _due(self, ext_id: str, job: dict[str, Any], last: float | None, started: float, now: datetime) -> float | None:
        trig = job["trigger"]
        kind = trig["kind"]
        if kind == "every":
            if trig.get("align"):
                if last is not None and job.get("catch_up", True):
                    missed = every_aligned(trig["seconds"], datetime.fromtimestamp(last, self.zone))
                    if missed <= now:
                        return now.timestamp()
                return every_aligned(trig["seconds"], now).timestamp()
            if self._ran_since_start.get(ext_id) != started and (last is None or last < started):
                return now.timestamp()                    # the first run at start
            return (last or started) + trig["seconds"]
        after = datetime.fromtimestamp(last, self.zone) if last is not None and job.get("catch_up", True) else now
        if kind == "at":
            when = self._resolve(ext_id, trig["time"])
            if not when:
                return None
            nxt = at_next(dtime.fromisoformat(str(when)), trig.get("days"), after)
        else:
            nxt = cron_next(trig["expr"], after)
        return nxt.timestamp()

    # running

    def jobs(self, ext_id: str) -> list[dict[str, Any]]:
        """Its jobs, and its checked kinds' triggers as jobs."""
        d = self.host.described.get(ext_id, {})
        return d.get("jobs", []) + kind_jobs(d)

    def speakers_stopped(self, ext_id: str) -> int:
        """He stopped what ext_id plays on the speakers ("stop" to a box while it played): its
        @ext.on_speakers_stop jobs run, so it stops at the source too, and "resume" can carry on. How many ran."""
        proc = self.host.procs.get(ext_id)
        if proc is None or proc.state != "running":
            return 0
        jobs = [j["name"] for j in self.jobs(ext_id) if j["trigger"]["kind"] == "on_speakers_stop"]
        for job in jobs:
            self._fire(ext_id, job, {"time": self.now().isoformat()})
        return len(jobs)

    def _fire(self, ext_id: str, job: str, data: dict[str, Any]) -> None:
        key = (ext_id, job)
        if key in self._in_flight:
            logger.warning(f"extension {ext_id}: {job} is still running; this run is skipped")
            return
        self._in_flight.add(key)

        async def run():
            try:
                if job.startswith("check:"):
                    if self.host.nudges is not None:
                        await self.host.nudges.check(ext_id, job.split(":")[1])
                    return
                r = await self.host.run_job(ext_id, job, data)
                if not r.get("ok"):
                    logger.warning(f"extension {ext_id}: {job} failed: {r.get('error')}")
            finally:
                self._in_flight.discard(key)
        asyncio.create_task(run(), name=f"{ext_id}.{job}")

    async def _loop(self) -> None:
        while True:
            try:
                soonest = self._tick()
            except Exception as e:                                      # noqa: BLE001  (never stop the clock)
                logger.exception(f"extension scheduler: {e}")
                soonest = time.time() + 5
            wait = max(0.2, min(soonest - time.time(), 30))
            try:
                await asyncio.wait_for(self._wake.wait(), wait)
            except TimeoutError:
                pass
            self._wake.clear()

    def _tick(self) -> float:
        now = self.now()
        ts = now.timestamp()
        soonest = ts + 30
        for ext_id, proc in list(self.host.procs.items()):
            if proc.state != "running" or proc.is_mcp_server or not proc.started_at:
                continue
            last = self._last(ext_id)
            for job in self.jobs(ext_id):
                if job["trigger"]["kind"] not in TIME_KINDS:
                    continue
                try:
                    due = self._due(ext_id, job, last.get(job["name"]), proc.started_at, now)
                except (ValueError, TypeError) as e:
                    self.host._note(ext_id, job["name"], f"cannot be scheduled: {e}")
                    continue
                if due is None:
                    continue
                if due <= ts:
                    self._fire(ext_id, job["name"], {"time": now.isoformat()})
                    self._ran(ext_id, job["name"], ts)
                    last[job["name"]] = ts
                    due = self._due(ext_id, job, ts, proc.started_at, now + timedelta(seconds=1))
                    if due is None:
                        continue
                self._next[(ext_id, job["name"])] = due
                soonest = min(soonest, due)
            self._ran_since_start[ext_id] = proc.started_at
            rows = self.services.timers(ext_id)
            keep = []
            for row in rows:
                at = datetime.fromisoformat(row["at"]).timestamp()
                if at <= ts:
                    self._fire(ext_id, row["job"], {"time": row["at"], "data": row.get("data")})
                else:
                    keep.append(row)
                    soonest = min(soonest, at)
            if len(keep) != len(rows):
                self.services.set_timers(ext_id, keep)
        return soonest

    # Home Assistant triggers

    def _wants(self) -> tuple[bool, tuple[str, ...]]:
        """Whether any running extension needs state changes, and which custom events. Speaking up
        needs the person entity's changes while any running extension has kinds, and the phone's
        buttons while a phone is set; announcements need their event always."""
        states, events = False, {self.announce_event} - {None}
        cfg = self.host.cfg
        for ext_id, proc in self.host.procs.items() if self._jobs else ():
            if proc.state != "running":
                continue
            if self.host.described.get(ext_id, {}).get("kinds"):
                states = states or bool(cfg.speak_up.home)
                if cfg.speak_up.phone:
                    events.add(PHONE_EVENT)
            for job in self.jobs(ext_id):
                kind = job["trigger"]["kind"]
                if kind in ("on_state", "on_arrive", "on_leave"):
                    states = True
                elif kind == "on_event":
                    events.add(job["trigger"]["event_type"])
        return states, tuple(sorted(events))

    async def _ha_loop(self) -> None:
        delay = 2.0
        while True:
            wants = self._wants()
            ha = self.host.cfg.providers.ha
            # No token yet (a new install before the wizard connects it): nothing to listen with, so it waits.
            if ha is None or not os.environ.get(ha.token_env) or not (wants[0] or wants[1]):
                await asyncio.sleep(3)
                continue
            url = ha.url.rstrip("/").replace("http", "ws", 1) + "/api/websocket"
            try:
                async with websockets.connect(url, max_size=2**22) as ws:
                    await ws.recv()
                    await ws.send(json.dumps({"type": "auth", "access_token": os.environ.get(ha.token_env, "")}))
                    if json.loads(await ws.recv()).get("type") != "auth_ok":
                        logger.error("Home Assistant events: the token was refused; trying again in a minute")
                        await asyncio.sleep(60)
                        continue
                    subs = dict(enumerate((["state_changed"] if wants[0] else []) + list(wants[1]), 1))
                    for msg_id, event_type in subs.items():
                        await ws.send(json.dumps({"id": msg_id, "type": "subscribe_events", "event_type": event_type}))
                    logger.info(f"Home Assistant events: listening for {'state changes, ' if wants[0] else ''}"
                                f"{', '.join(wants[1]) or 'no events'}")
                    delay = 2.0
                    while self._wants() == wants:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), 3)
                        except TimeoutError:
                            continue
                        msg = json.loads(raw)
                        if msg.get("type") == "result" and not msg.get("success"):
                            logger.warning(f"Home Assistant events: cannot subscribe to "
                                           f"{subs.get(msg.get('id'), 'an event')} (a non-admin token?): "
                                           f"{msg.get('error')}")
                        if msg.get("type") == "event":
                            self.dispatch(msg["event"])
            except (OSError, websockets.WebSocketException, ValueError) as e:
                logger.warning(f"Home Assistant events: {e}; reconnecting in {delay:.0f} s")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    def dispatch(self, event: dict[str, Any]) -> None:
        """One Home Assistant event to the triggers that want it."""
        etype, data = event.get("event_type"), event.get("data") or {}
        if etype == "state_changed":
            eid = data.get("entity_id")
            old, new = data.get("old_state") or {}, data.get("new_state") or {}
            if new.get("state") is not None:
                self._states[eid] = new["state"]
            person = self.host.cfg.speak_up.home
            arrived = eid == person and old.get("state") != "home" and new.get("state") == "home"
            left = eid == person and old.get("state") == "home" and new.get("state") not in (None, "home")
            if self.host.nudges is not None and (arrived or left):
                if arrived:
                    self.host.nudges.arrived()
                else:
                    asyncio.create_task(self.host.nudges.left())
            for ext_id, proc in self.host.procs.items():
                if proc.state != "running":
                    continue
                perms = self.host.sources[ext_id].manifest.permissions if self.host.sources[ext_id].manifest else None
                for job in self.jobs(ext_id):
                    trig = job["trigger"]
                    if trig["kind"] == "on_state":
                        entities = self._resolve(ext_id, trig["entity"])
                        entities = [entities] if isinstance(entities, str) else entities or []
                        if eid not in entities:
                            continue
                        if trig.get("to") is not None and new.get("state") != trig["to"]:
                            continue
                        if trig.get("from") is not None and old.get("state") != trig["from"]:
                            continue
                        payload = {"entity_id": eid, "old": old or None, "new": new or None}
                        if trig.get("for"):
                            asyncio.create_task(self._after_holding(ext_id, job["name"], eid, new.get("state"),
                                                                    trig["for"], payload))
                        else:
                            self._fire(ext_id, job["name"], payload)
                    elif (trig["kind"] == "on_arrive" and arrived) or (trig["kind"] == "on_leave" and left):
                        if perms and perms.presence:
                            self._fire(ext_id, job["name"], {"time": self.now().isoformat()})
                        else:
                            self.host._note(ext_id, job["name"], "on_arrive and on_leave need the presence permission")
        else:
            if etype == PHONE_EVENT and self.on_phone_action is not None:
                self.on_phone_action(data)
            if etype == self.announce_event and self.on_announce is not None:
                self.on_announce(data)
            for ext_id, proc in self.host.procs.items():
                if proc.state != "running":
                    continue
                for job in self.jobs(ext_id):
                    if job["trigger"]["kind"] == "on_event" and job["trigger"]["event_type"] == etype:
                        self._fire(ext_id, job["name"], {"type": etype, "data": data})

    async def _after_holding(self, ext_id: str, job: str, eid: str, state: str, seconds: float,
                             payload: dict[str, Any]) -> None:
        await asyncio.sleep(seconds)
        if self._states.get(eid) == state:
            self._fire(ext_id, job, payload)
