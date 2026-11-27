"""Winston's Home Assistant integration (custom_components/winston) talks to this: a second listener in
winstond, never the control API. See docs/ha-integration.md.

Home Assistant pairs with a code shown on Ways to talk and gets a token of its own (data/.env, TOKEN_ENV).
The token reaches only these routes:

- `POST /pair {code}` gives `{token, instance, name}`; a code lasts ten minutes and five wrong tries void it.
- `GET /stream` is server-sent events: `snapshot`, everything Home Assistant shows (his rooms' states, his
  engines, each publishing extension's entities and their values), whole, at the start and whenever it
  changes; and `event`, an announcement or an extension's nudge, as it happens.
- `POST /set {extension, key, entry, value, press}` runs the extension's on_change or on_press.
- `POST /converse {text, conversation_id, device_id, satellite_id, area}` is Home Assistant's conversation
  agent: answered as Assist answers (channels/assist.py), streamed back as lines of JSON, `{"text": piece}` as
  he says it, then `{"done": true, "text", "continue"}` or `{"error": what to say instead}`.

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
from urllib.parse import urlsplit

import httpx
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


NOT_INSTALLED = ("Home Assistant does not have Winston's integration yet. Select Download in Home Assistant, "
                 "download him there and restart Home Assistant, then try again.")


def address_towards(url: str) -> str | None:
    """This computer's IPv4 address on the route to that URL's host (nothing is sent), or None."""
    host = urlsplit(url).hostname
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 9))
            return s.getsockname()[0]
    except (OSError, TypeError):
        return None


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

    def __init__(self, cfg: WinstonConfig, feed: Feed, host, supervisor, answerer: "assist.Assist | None" = None):
        self.cfg, self.feed, self.host, self.sup, self.answerer = cfg, feed, host, supervisor, answerer
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
        self._tasks: set[asyncio.Task] = set()          # answers still running after Home Assistant hung up

    # what Home Assistant sees

    def snapshot(self) -> dict[str, Any]:
        cfg = self.cfg
        rooms = [{"name": r, "state": self.states.get(r.strip().casefold(), "idle")} for r in configured(cfg)]
        engines = [{"name": name, "title": TITLES.get(name, name.replace("_", " ").capitalize()), "up": m.up}
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

    async def _converse(self, request: Request):
        if not self._authorized(request):
            return JSONResponse({"error": "not paired"}, 401)
        try:
            b = await request.json()
            text = str(b["text"]).strip()
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"error": "send {text, conversation_id, device_id, satellite_id, area}"}, 400)
        if not text or self.answerer is None:
            return JSONResponse({"error": "nothing to answer" if not text else "not answering here"}, 400)
        opt = lambda k: str(b[k]) if b.get(k) else None                     # noqa: E731
        req = assist.Request(text, opt("conversation_id") or uuid.uuid4().hex, opt("device_id"),
                             opt("satellite_id"), opt("language"), opt("area"))
        q: asyncio.Queue = asyncio.Queue()

        async def run():
            try:
                said, more = await self.answerer.answer(req, on_text=q.put_nowait)
                q.put_nowait({"done": True, "text": said, "continue": more})
            except assist.Unanswered as e:
                q.put_nowait({"error": str(e)})
            except Exception:                                           # noqa: BLE001
                logger.exception("home assistant: answering failed")
                q.put_nowait({"error": assist.FAILED})
        # Not cancelled when Home Assistant hangs up: what he started doing in the home finishes either way.
        task = asyncio.create_task(run())
        self._tasks.add(task); task.add_done_callback(self._tasks.discard)

        async def lines():
            while True:
                item = await q.get()
                yield json.dumps({"text": item} if isinstance(item, str) else item) + "\n"
                if not isinstance(item, str):
                    return
        return StreamingResponse(lines(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    def app(self) -> Starlette:
        return Starlette(routes=[Route("/pair", self._pair, methods=["POST"]),
                                 Route("/stream", self._stream), Route("/set", self._set, methods=["POST"]),
                                 Route("/converse", self._converse, methods=["POST"])])

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

    async def add_to(self, ha: "httpx.AsyncClient", ha_url: str) -> str | None:
        """Adds him in Home Assistant from here, with the token Winston already has there (an administrator's):
        a fresh code, and Home Assistant's own Add integration flow given this computer's address, the port
        and the code. None once added, else what to tell the user."""
        if not self.listening:
            return self.error or "Switch on Show him in Home Assistant and save first."
        addr = await asyncio.to_thread(address_towards, ha_url)
        if not addr:
            return "His computer has no route to Home Assistant's address. Pair with a code instead."
        code = self.pairing.new()
        try:
            r = await ha.post("/api/config/config_entries/flow", json={"handler": "winston"})
            if r.status_code == 404:
                return NOT_INSTALLED
            if r.status_code in (401, 403):
                return ("Winston's account in Home Assistant is not an administrator's, so it cannot add him. "
                        "Pair with a code instead.")
            r.raise_for_status()
            flow = r.json()
            if flow.get("type") == "form":
                r = await ha.post(f"/api/config/config_entries/flow/{flow['flow_id']}",
                                  json={"host": addr, "port": self._port, "code": code})
                r.raise_for_status()
                flow = r.json()
        except (httpx.HTTPError, ValueError, KeyError) as e:
            logger.warning(f"home assistant: adding the integration failed: {e!r}")
            return f"Home Assistant did not finish adding him ({e}). Pair with a code instead."
        kind, why = flow.get("type"), flow.get("reason")
        if kind == "create_entry" or (kind == "abort" and why == "already_configured"):
            logger.info(f"home assistant: added the integration there, at {addr}:{self._port}")
            return None
        if kind == "form":                  # its errors, left open: closed so it is not left half done
            try:
                await ha.delete(f"/api/config/config_entries/flow/{flow['flow_id']}")
            except httpx.HTTPError:
                pass
            if (flow.get("errors") or {}).get("base") == "cannot_connect":
                return (f"Home Assistant could not reach his computer at {addr}, port {self._port}. Open the port "
                        f"to Home Assistant's address on his computer's firewall, and on the router between them.")
        logger.warning(f"home assistant: adding the integration ended with {flow}")
        return f"Home Assistant did not add him ({why or kind}). Pair with a code instead."

    def unpair(self):
        secrets.remove(TOKEN_ENV)
        self.last_seen = None
