"""Winston's Home Assistant integration (custom_components/winston) talks to this: a second listener in
winstond, never the control API. See docs/ha-integration.md.

Home Assistant pairs with a code shown on Ways to talk and gets a token of its own (data/.env, TOKEN_ENV).
The token reaches only these routes:

- `POST /pair {code}` gives `{token, instance, name}`; a code lasts ten minutes and five wrong tries void it.
- `GET /stream` is server-sent events: `snapshot`, everything Home Assistant shows (his rooms' states, his
  engines, each publishing extension's entities and their values), whole, at the start and whenever it
  changes; and `event`, an announcement or an extension's nudge, as it happens.
- `POST /set {extension, key, entry, value, press}` runs the extension's on_change or on_press.

Discovery is zeroconf, `_winston._tcp.local.`, with the instance id in its properties, so Home Assistant
knows a Winston it has already added."""
import asyncio
import errno
import hmac
import json
import secrets as pysecrets
import socket
import time
import uuid
from contextlib import contextmanager
from typing import Any

import uvicorn
from loguru import logger
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

import winston
from winston.api import secrets
from winston.channels import assist
from winston.config.loader import data_dir
from winston.config.schema import WinstonConfig
from winston.core.live import Feed, LiveEvent
from winston.core.rooms import configured

TOKEN_ENV = "WINSTON_HA_TOKEN"
SERVICE = "_winston._tcp.local."
ZEROCONF = AsyncZeroconf        # swapped in tests
CODE_SECS = 600
CODE_TRIES = 5
TITLES = {"llm": "Brain", "stt": "Hearing", "tts": "Speaking", "voice": "Talk page", "satellite": "Room boxes",
          "discord": "Discord"}        # as the web UI names them (web/src/lib/presets.ts, Ways to talk)
TICK_SECS = 2                   # engines have no events of their own: the snapshot is rebuilt this often anyway


def instance() -> str:
    """This Winston's id, made once: Home Assistant's unique ids start with it, so they survive restarts."""
    f = data_dir() / "instance"
    if f.is_file() and (v := f.read_text().strip()):
        return v
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(v := uuid.uuid4().hex)
    return v


class Pairing:
    """The code on Ways to talk: one at a time, CODE_SECS long, void after CODE_TRIES wrong tries."""

    def __init__(self):
        self.code: str | None = None
        self.until = 0.0
        self.tries = 0

    def new(self) -> str:
        self.code, self.until, self.tries = f"{pysecrets.randbelow(10 ** 6):06d}", time.time() + CODE_SECS, 0
        return self.code

    def take(self, code: str) -> bool:
        """Right and in time: true, and the code is used up. Wrong: counted."""
        if self.code is None or time.time() > self.until:
            self.code = None
            return False
        if hmac.compare_digest(self.code, code.strip()):
            self.code = None
            return True
        self.tries += 1
        if self.tries >= CODE_TRIES:
            self.code = None
        return False


class _Server(uvicorn.Server):
    """winstond's own server handles the signals; this one only stops when told."""

    @contextmanager
    def capture_signals(self):
        yield


class HALink:
    """The listener, switched by channels.ha_integration, and what it serves."""

    def __init__(self, cfg: WinstonConfig, feed: Feed, host, supervisor):
        self.cfg, self.feed, self.host, self.sup = cfg, feed, host, supervisor
        self.pairing = Pairing()
        self.states: dict[str, str] = {}                # room key -> idle, listening, thinking or speaking
        self.last_seen: float | None = None
        self.error: str | None = None
        self._server: _Server | None = None
        self._task: asyncio.Task | None = None
        self._port: int | None = None
        self._zc = self._service = None
        self._subs: set[asyncio.Queue] = set()
        self._watch: asyncio.Task | None = None

    # what Home Assistant sees

    def snapshot(self) -> dict[str, Any]:
        cfg = self.cfg
        rooms = [{"name": r, "state": self.states.get(r.strip().casefold(), "idle")} for r in configured(cfg)]
        engines = [{"name": name, "title": TITLES.get(name, name.replace("_", " ").capitalize()), "up": m.state == "healthy"}
                   for name, m in self.sup.items.items()]
        exts = []
        for ext_id, d in sorted(self.host.described.items()):
            if not d.get("entities") or not self.host.enabled(ext_id) or not self.host.config(ext_id).publish:
                continue
            saved = self.host.config(ext_id).settings
            props = d.get("settings", {}).get("schema", {}).get("properties", {})
            entities = []
            for e in d["entities"]:
                per = (e.get("per") or {}).get("from")
                entries = list((saved.get(per) if per in saved else props.get(per, {}).get("default")) or {}) \
                    if per else [None]
                for entry in entries:
                    v = self.host.entities.get((ext_id, e["key"], entry)) or {}
                    entities.append({**{k: e.get(k) for k in ("key", "kind", "unit", "device_class", "state_class",
                                                              "icon", "options", "min", "max", "step", "takes")},
                                     "entry": entry, "name": e["name"].replace("{entry}", entry) if entry else e["name"],
                                     "value": v.get("value"), "attributes": v.get("attributes") or {},
                                     "at": v.get("at")})
            exts.append({"id": ext_id, "name": d.get("manifest", {}).get("name", ext_id), "entities": entities})
        return {"instance": instance(), "name": cfg.assistant.name, "version": winston.__version__,
                "rooms": rooms, "engines": engines, "extensions": exts}

    def _send(self, kind: str, data: dict[str, Any]):
        for q in list(self._subs):
            try:
                q.put_nowait((kind, data))
            except asyncio.QueueFull:
                logger.warning("home assistant: dropped an event for a stuck stream")

    def changed(self):
        """Something it shows may have changed; each stream sends a snapshot if it did."""
        self._send("check", {})

    def announced(self, text: str):
        self._send("event", {"type": "announcement", "text": text})

    def nudged(self, ev: dict[str, Any]):
        """An extension's nudge was delivered (silent ones too): its event entity fires."""
        if ev.get("ext"):
            self._send("event", {"type": "nudge", "extension": ev["ext"], "kind": ev.get("kind"),
                                 "level": ev.get("level"), "where": ev.get("name"), "text": ev.get("text")})

    async def _watch_feed(self):
        """Rooms' states from the live feed."""
        q = self.feed.subscribe()
        try:
            while True:
                ev: LiveEvent = await q.get()
                if ev.type == "state" and ev.room and ev.state:
                    k = ev.room.strip().casefold()
                    if self.states.get(k) != ev.state:
                        self.states[k] = ev.state
                        self.changed()
        finally:
            self.feed.unsubscribe(q)

    # the routes

    def _authorized(self, request: Request) -> bool:
        want = secrets.value(TOKEN_ENV)
        got = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        ok = bool(want) and hmac.compare_digest(want.encode(), got.encode())
        if ok:
            self.last_seen = time.time()
        return ok

    async def _pair(self, request: Request):
        try:
            code = str((await request.json()).get("code", ""))
        except (ValueError, AttributeError):
            return JSONResponse({"error": "send {\"code\": ...}"}, 400)
        if not self.pairing.take(code):
            logger.warning(f"home assistant: a wrong or expired pairing code from {request.client.host if request.client else '?'}")
            return JSONResponse({"error": "That code is wrong or expired. Make a new one on Ways to talk."}, 403)
        token = pysecrets.token_urlsafe(32)
        secrets.write(TOKEN_ENV, token)
        self.last_seen = time.time()
        logger.info("home assistant: paired")
        return JSONResponse({"token": token, "instance": instance(), "name": self.cfg.assistant.name})

    async def _stream(self, request: Request):
        if not self._authorized(request):
            return JSONResponse({"error": "not paired"}, 401)
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.add(q)

        async def events():
            last = None
            try:
                while True:
                    snap = json.dumps(self.snapshot())
                    if snap != last:
                        last = snap
                        yield f"event: snapshot\ndata: {snap}\n\n"
                    try:
                        kind, data = await asyncio.wait_for(q.get(), TICK_SECS)
                    except TimeoutError:
                        if not secrets.value(TOKEN_ENV):        # unpaired on Ways to talk: the stream ends
                            return
                        yield ": still here\n\n"
                        continue
                    if kind == "event":
                        yield f"event: event\ndata: {json.dumps(data)}\n\n"
                    self.last_seen = time.time()
            finally:
                self._subs.discard(q)
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    async def _set(self, request: Request):
        if not self._authorized(request):
            return JSONResponse({"error": "not paired"}, 401)
        try:
            b = await request.json()
            ext_id, key = str(b["extension"]), str(b["key"])
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"error": "send {extension, key, entry, value, press}"}, 400)
        d = self.host.described.get(ext_id) or {}
        spec = next((e for e in d.get("entities", []) if e["key"] == key), None)
        if spec is None or not self.host.enabled(ext_id) or not self.host.config(ext_id).publish:
            return JSONResponse({"ok": False, "error": f"no entity {key!r} in {ext_id}"}, 404)
        proc = self.host.procs.get(ext_id)
        if proc is None or proc.state != "running":
            return JSONResponse({"ok": False, "error": f"{ext_id} is not running"}, 409)
        press, entry, value = bool(b.get("press")), b.get("entry"), b.get("value")
        logger.info(f"home assistant: {ext_id}.{key}{f'[{entry}]' if entry else ''} "
                    f"{'pressed' if press else f'set to {value!r}'}")
        try:
            r = await proc.call("entity", timeout=35, key=key, entry=entry, value=value, press=press)
        except Exception as e:                                          # noqa: BLE001
            r = {"ok": False, "error": str(e)}
        if r.get("ok"):
            if not press and spec.get("kind") != "button":      # the value shows once the handler returns
                self.host.set_entity(ext_id, key, entry, value, {})
            self.host.handled(ext_id)
        return JSONResponse(r, 200 if r.get("ok") else 500)

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/pair", self._pair, methods=["POST"]),
                                 Route("/stream", self._stream), Route("/set", self._set, methods=["POST"])])

    # switching it

    @property
    def listening(self) -> bool:
        return self._server is not None

    async def apply(self, cfg: WinstonConfig) -> None:
        """Listen, move or stop to match channels.ha_integration. A port that cannot be opened is kept in error,
        never raised, as Assist does."""
        self.cfg, h = cfg, cfg.channels.ha_integration
        want = h.port if h.enabled else None
        if self._server and self._port != want:
            await self.stop()
        self.error = None
        if want and not self._server:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", want))
            except OSError as e:
                sock.close()
                self.error = (f"Port {want} is in use by another program." if e.errno == errno.EADDRINUSE
                              else f"Port {want} could not be opened: {e.strerror or e}.")
                logger.warning(f"home assistant: {self.error}")
                return
            self._server = _Server(uvicorn.Config(self.app(), log_level="warning", lifespan="off",
                                                  timeout_graceful_shutdown=1))
            self._task = asyncio.create_task(self._server.serve(sockets=[sock]))
            self._port = want
            self._watch = asyncio.create_task(self._watch_feed())
            await self._advertise(want)
        self.changed()

    async def _advertise(self, port: int):
        addr = assist.local_address()
        if not addr:
            logger.warning(f"home assistant: no route to the mDNS group, so not advertised; add Winston by host and "
                           f"port {port}")
            return
        self._service = AsyncServiceInfo(SERVICE, f"{self.cfg.assistant.name}.{SERVICE}",
                                         addresses=[socket.inet_aton(addr)], port=port,
                                         properties={"instance": instance(), "version": winston.__version__})
        self._zc = ZEROCONF()
        await self._zc.async_register_service(self._service, allow_name_change=True)
        logger.info(f"home assistant: listening on {port} and advertised from {addr}")

    async def stop(self):
        if self._zc:
            await self._zc.async_unregister_service(self._service)
            await self._zc.async_close()
        if self._watch:
            self._watch.cancel()
        if self._server:
            self._server.should_exit = True
            self._server.force_exit = True          # open streams never end on their own
            try:
                await asyncio.wait_for(self._task, 5)
            except TimeoutError:
                self._task.cancel()
            logger.info("home assistant: stopped listening")
        self._server = self._task = self._zc = self._service = self._watch = None
        self._port = None

    def unpair(self):
        secrets.remove(TOKEN_ENV)
        self.last_seen = None
