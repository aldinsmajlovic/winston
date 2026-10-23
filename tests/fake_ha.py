"""A fake Home Assistant for tests that must not touch the real one. It speaks the subset Winston uses:
REST (/api/config, /api/states, /api/services and calls to them, /api/history), the websocket API (auth; entity, area and device registry lists;
entity_registry/get_entries and /update; expose_entity, expose_entity/list, expose_new_entities/get
and /set; subscribe_events) and the MCP server's Streamable HTTP endpoint (initialize, tools/list), whose
tools follow the exposed domains as the real one's do. And signing in as Home Assistant's login does it:
/auth/authorize signs its one user in at once and sends the browser back with a code (refusing a
redirect_uri on another address than the client_id, as Home Assistant does), /auth/token and /auth/revoke,
the websocket's auth/current_user, auth/refresh_tokens, auth/delete_refresh_token and
auth/long_lived_access_token (a name once), and the MCP server's config flow for an administrator, which
switches it on (`mcp_on`, off with --no-mcp). `admin` makes its user an administrator or not.

Every write is recorded, so a test can check exactly what Winston sent (for example that it never sent
a "name"). GET /fake/log returns the writes so far, in order.

Run it alone:  .venv/bin/python tests/fake_ha.py [--port 18123] [--token fake-ha-token] [--seed test|home]
Or in-process: `async with FakeHA(port) as ha: ...` (ha.url, ha.writes, ha.reset(), await ha.change(...),
await ha.fire(...))."""
import argparse
import asyncio
import copy
import json
from datetime import datetime
from urllib.parse import urlencode, urlsplit

from aiohttp import WSMsgType, web

TOKEN = "fake-ha-token"
VERSION = "2026.9.3"

# A small generic home: Swedish names on the Home Assistant side, as a household would have them.
AREAS = [{"area_id": "living_room", "name": "Living room", "aliases": []},
         {"area_id": "kitchen", "name": "Kitchen", "aliases": []}]
# Makers, models and firmware are made up; addresses and serials are there to check they are never passed on.
DEVICES = [
    {"id": "dev_ceiling", "name": "TAKLAMPA E27", "name_by_user": None, "area_id": "living_room",
     "manufacturer": "Glimt Lighting", "model": "Ceiling E27", "model_id": "GL-E27", "sw_version": "1.4.2",
     "hw_version": "2", "serial_number": "GL-SERIAL-0001", "connections": [["mac", "02:00:00:00:00:01"]],
     "identifiers": [["fake", "ceiling"]], "config_entries": ["entry_glimt"], "entry_type": None},
    {"id": "dev_shelf", "name": "HYLLA E27", "name_by_user": "Hyllan", "area_id": "living_room",
     "manufacturer": "Glimt Lighting", "model": "Shelf E27", "sw_version": "1.3.0"},
    {"id": "dev_kitchen", "name": "Kitchen spots", "name_by_user": None, "area_id": "kitchen"},
    {"id": "dev_phone", "name": "Phone", "name_by_user": None, "area_id": None},
    {"id": "dev_plex", "name": "Plex client", "name_by_user": None, "area_id": None,
     "manufacturer": "Plexus Media", "sw_version": "9.1", "entry_type": "service"},
]
# entity_id, friendly name, state, attributes, registry (None: not in the registry), exposed
ENTITIES = [
    ("light.taklampa", "Taklampa", "on", {"brightness": 128},
     {"device_id": "dev_ceiling", "name": None, "aliases": ["taket"]}, True),
    ("light.hylla", "Hyllan", "unavailable", {}, {"device_id": "dev_shelf", "name": None, "aliases": []}, True),
    ("light.kok", "Köksspotlights", "off", {}, {"device_id": "dev_kitchen", "name": "Köksspotlights",
                                                "aliases": [], "area_id": "kitchen"}, False),
    ("sensor.taklampa_signal", "Taklampa signal", "-62", {"unit_of_measurement": "dBm"},
     {"device_id": "dev_ceiling", "name": None, "aliases": [], "entity_category": "diagnostic"}, False),
    ("sensor.phone_battery", "Phone battery", "81", {"unit_of_measurement": "%"},
     {"device_id": "dev_phone", "name": None, "aliases": []}, False),
    ("media_player.plex_1", "Plex client", "unavailable", {}, {"device_id": "dev_plex", "name": None, "aliases": []}, True),
    ("switch.adaptive_lighting", "Adaptive lighting", "on", {}, {"device_id": None, "name": None, "aliases": []}, False),
    ("button.wake_pc", "Wake PC", "2026-09-24T07:12:00+00:00", {}, {"device_id": None, "name": None, "aliases": []}, False),
    ("person.someone", "Someone", "home", {}, {"device_id": None, "name": None, "aliases": []}, True),
    ("weather.forecast_home", "Forecast", "sunny", {"temperature": 14}, {"device_id": None, "name": None, "aliases": []}, True),
    ("todo.shopping_list", "Shopping list", "2", {}, {"device_id": None, "name": None, "aliases": []}, True),
    ("sun.sun", "Sun", "above_horizon", {}, None, False),       # a state with no registry entry
]

# A home shaped like a real one, for docs screenshots (--seed home): a room light group with three
# lamps, a phone battery and a button that wakes the PC. Every name here is made up.
HOME_AREAS = [{"area_id": "main_room", "name": "Main room", "aliases": []},
              {"area_id": "hallway", "name": "Hallway", "aliases": []}]
HOME_DEVICES = [
    {"id": "dev_tak", "name": "TAKLAMPA E27 WS", "name_by_user": "Taklampa", "area_id": "main_room"},
    {"id": "dev_golv", "name": "GOLVLAMPA E27 CWS", "name_by_user": "Golvlampa", "area_id": "main_room"},
    {"id": "dev_hylla", "name": "HYLLA KAJPLATS E27 WS G95 clear 806lm", "name_by_user": None,
     "area_id": "main_room"},
    {"id": "dev_phone", "name": "Telefon", "name_by_user": None, "area_id": None},
    {"id": "dev_pc", "name": "Dator", "name_by_user": None, "area_id": "main_room"},
    {"id": "dev_door", "name": "Ytterdörr", "name_by_user": None, "area_id": "hallway"},
]
_no = {"device_id": None, "name": None, "aliases": []}
HOME_ENTITIES = [
    ("light.main_room", "Main room lights", "on",
     {"brightness": 128, "entity_id": ["light.taklampa", "light.golvlampa", "light.hylla_kajplats"]},
     {**_no, "area_id": "main_room", "aliases": ["vardagsrummet"]}, True),
    ("light.taklampa", "Taklampa", "on", {"brightness": 128}, {**_no, "device_id": "dev_tak"}, True),
    ("sensor.taklampa_signal", "Taklampa signal", "-62", {"unit_of_measurement": "dBm"},
     {**_no, "device_id": "dev_tak", "entity_category": "diagnostic"}, False),
    ("light.golvlampa", "Golvlampa", "on", {"brightness": 128, "color_temp_kelvin": 2700},
     {**_no, "device_id": "dev_golv"}, True),
    ("light.hylla_kajplats", "HYLLA KAJPLATS E27 WS G95 clear 806lm", "unavailable", {},
     {**_no, "device_id": "dev_hylla"}, True),
    ("sensor.phone_battery", "Telefon batteri", "81", {"unit_of_measurement": "%"},
     {**_no, "device_id": "dev_phone"}, True),
    ("button.wake_pc", "PC", "2026-09-24T07:12:00+00:00", {}, {**_no, "device_id": "dev_pc"}, True),
    ("binary_sensor.ytterdorr", "Ytterdörr", "off", {"device_class": "door"}, {**_no, "device_id": "dev_door"}, False),
    ("sensor.ficus_moisture", "Ficus moisture", "22", {"unit_of_measurement": "%", "device_class": "moisture"}, _no, False),
    ("person.someone", "Someone", "home", {}, _no, True),
    ("weather.forecast_home", "Forecast", "cloudy", {"temperature": 12}, _no, True),
    ("todo.shopping_list", "Shopping list", "3", {}, _no, True),
    ("calendar.home", "Home", "off", {}, _no, False),
    ("update.home_assistant_core", "Home Assistant Core Update", "off", {}, _no, False),
    ("sun.sun", "Sun", "above_horizon", {}, None, False),
]
SEEDS = {"test": (AREAS, DEVICES, ENTITIES), "home": (HOME_AREAS, HOME_DEVICES, HOME_ENTITIES)}

# MCP tools by the domain that brings them; Assist's own are always there.
TOOLS_ALWAYS = [("homeassistant__GetLiveContext", "Provides real-time information about the CURRENT state"),
                ("intent__HassTurnOn", "Turns on/opens/presses a device or entity."),
                ("intent__HassTurnOff", "Turns off/closes a device or entity."),
                ("llm__GetDateTime", "Provides the current date and time.")]
TOOLS_BY_DOMAIN = {"light": [("light__HassLightSet", "Sets the brightness percentage or color of a light")],
                   "todo": [("todo__get_items", "Query a to-do list"), ("todo__HassListAddItem", "Add item to a todo list"),
                            ("todo__HassListCompleteItem", "Complete item on a todo list")],
                   "media_player": [("media_player__HassMediaPause", "Pauses a media player")]}

INTENT_ARGS = {"type": "object", "properties": {"name": {"type": "string"}, "area": {"type": "string"},
                                             "domain": {"type": "array", "items": {"type": "string"}}}}
WRITES = {"config/entity_registry/update", "homeassistant/expose_entity", "homeassistant/expose_new_entities/set"}
UPDATE_KEYS = {"aliases", "name", "area_id", "icon", "new_entity_id", "disabled_by", "hidden_by"}


def _between(events: list[dict], body: dict) -> list[dict]:
    """The events overlapping get_events' range, as Home Assistant answers; a whole day is local midnight."""
    lo, hi = (datetime.fromisoformat(body[k]) for k in ("start_date_time", "end_date_time"))

    def at(v: str) -> datetime:
        return datetime.fromisoformat(v) if "T" in v else datetime.fromisoformat(v).replace(tzinfo=lo.tzinfo)
    return [e for e in events if at(e["start"]) < hi and at(e["end"]) > lo]


class FakeHA:
    def __init__(self, port: int = 18123, token: str = TOKEN, seed: str = "test", mcp_on: bool = True):
        self.port, self.token = port, token
        self.areas, self.devices, self.entities = SEEDS[seed]
        self.user = {"id": "user_1", "name": "Someone", "is_owner": True, "is_admin": True}
        self.mcp_on = mcp_on
        self.reset()
        self.app = web.Application()
        self.app.add_routes([
            web.get("/api/", self.api_root), web.get("/api/config", self.get_config), web.get("/api/states", self.get_states),
            web.get("/api/states/{eid}", self.get_state), web.get("/api/websocket", self.websocket),
            web.get("/api/services", self.get_services), web.post("/api/services/{domain}/{service}", self.call_service),
            web.get("/api/history/period/{since}", self.get_history),
            web.post("/api/mcp", self.mcp), web.get("/api/mcp", self.mcp_get), web.delete("/api/mcp", self.mcp_delete),
            web.get("/fake/log", self.fake_log), web.get("/manifest.json", self.manifest),
            web.get("/auth/authorize", self.authorize), web.post("/auth/token", self.auth_token),
            web.post("/auth/revoke", self.revoke), web.post("/api/config/config_entries/flow", self.flow_start),
            web.post("/api/config/config_entries/flow/{flow_id}", self.flow_step),
        ])
        self.subscribers: dict[web.WebSocketResponse, dict[int, str]] = {}
        self.runner: web.AppRunner | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def reset(self):
        self.states = {eid: {"entity_id": eid, "state": st, "attributes": {"friendly_name": fn, **attrs},
                             "last_changed": "2026-09-24T07:00:00+00:00"} for eid, fn, st, attrs, _, _ in self.entities}
        self.registry = {eid: {"entity_id": eid, "id": f"reg_{i}", "area_id": None, "platform": "fake",
                               "has_entity_name": False, "original_name": fn, **copy.deepcopy(reg)}
                         for i, (eid, fn, _, _, reg, _) in enumerate(self.entities) if reg is not None}
        self.exposed = {eid for eid, *_, ex in self.entities if ex}
        self.expose_new = True
        self.writes: list[dict] = []
        self.events: dict[str, list[dict]] = {}       # calendar.get_events answers, by calendar
        self.tokens = {self.token}                    # every access token it takes
        self.codes: dict[str, str] = {}               # sign-in code -> client_id
        self.refresh: dict[str, dict] = {}            # refresh token id -> {token, access, client_name, type}
        self.n = 0

    # ------------------------------------------------------------ REST

    async def _gate(self, request: web.Request):
        if request.headers.get("Authorization", "").removeprefix("Bearer ") not in self.tokens:
            raise web.HTTPUnauthorized()

    # ------------------------------------------------------------ signing in

    def _new(self, kind: str) -> str:
        self.n += 1
        return f"fake-{kind}-{self.n}"

    async def manifest(self, request):
        return web.json_response({"name": "Home Assistant", "short_name": "Home Assistant"})

    async def authorize(self, request):
        """Its login, signed in at once: back to redirect_uri with a code and the state."""
        q = request.query
        client, back = urlsplit(q.get("client_id", "")), urlsplit(q.get("redirect_uri", ""))
        if q.get("response_type") != "code" or not client.netloc or (client.scheme, client.netloc) != (back.scheme, back.netloc):
            return web.Response(status=400, text="Invalid redirect URI")
        code = self._new("code")
        self.codes[code] = q["client_id"]
        self.writes.append({"type": "auth/authorize", "client_id": q["client_id"], "redirect_uri": q["redirect_uri"]})
        raise web.HTTPFound(f"{q['redirect_uri']}?{urlencode({'code': code, 'state': q.get('state', '')})}")

    async def auth_token(self, request):
        form = await request.post()
        if form.get("grant_type") != "authorization_code" or self.codes.pop(form.get("code", ""), None) != form.get("client_id"):
            return web.json_response({"error": "invalid_request", "error_description": "Invalid code"}, status=400)
        access, token = self._new("session"), self._new("refresh")
        self.tokens.add(access)
        self.refresh[self._new("rid")] = {"token": token, "access": access, "client_name": None, "type": "normal",
                                          "client_id": form["client_id"]}
        return web.json_response({"access_token": access, "token_type": "Bearer", "refresh_token": token,
                                  "expires_in": 1800, "ha_auth_provider": "homeassistant"})

    async def revoke(self, request):
        form = await request.post()
        for rid, r in list(self.refresh.items()):
            if r["token"] == form.get("token"):
                self._drop(rid)
                self.writes.append({"type": "auth/revoke", "client_id": r.get("client_id")})
        return web.Response(status=200)

    def _drop(self, rid: str):
        self.tokens.discard(self.refresh.pop(rid)["access"])

    async def flow_start(self, request):
        await self._gate(request)
        if not self.user["is_admin"]:
            raise web.HTTPUnauthorized()
        body = await request.json()
        if body.get("handler") != "mcp_server":
            return web.json_response({"message": "Invalid handler specified"}, status=404)
        if self.mcp_on:
            return web.json_response({"type": "abort", "flow_id": "flow_1", "reason": "single_instance_allowed"})
        return web.json_response({"type": "form", "flow_id": "flow_1", "handler": "mcp_server", "step_id": "user",
                                  "data_schema": [{"name": "llm_hass_api", "default": ["assist"]}]})

    async def flow_step(self, request):
        await self._gate(request)
        body = await request.json()
        if request.match_info["flow_id"] != "flow_1" or not body.get("llm_hass_api"):
            return web.json_response({"message": "Invalid flow"}, status=400)
        self.mcp_on = True
        self.writes.append({"type": "config_entries/mcp_server", **body})
        return web.json_response({"type": "create_entry", "flow_id": "flow_1", "title": "Assist"})

    async def api_root(self, request):
        await self._gate(request)
        return web.json_response({"message": "API running."})

    async def get_config(self, request):
        await self._gate(request)
        return web.json_response({"version": VERSION, "location_name": "Fake home", "country": "SE",
                                  "currency": "SEK", "time_zone": "Europe/Stockholm", "language": "en",
                                  "unit_system": {"temperature": "°C", "length": "km"},
                                  "components": ["http", "websocket_api", "conversation", "light"]
                                  + (["mcp_server"] if self.mcp_on else [])})

    async def get_states(self, request):
        await self._gate(request)
        return web.json_response(list(self.states.values()))

    async def get_state(self, request):
        await self._gate(request)
        s = self.states.get(request.match_info["eid"])
        return web.json_response(s) if s else web.json_response({"message": "Entity not found."}, status=404)

    # Services: a few that change things, and the response-only ones Home Assistant has for reading.
    SERVICES = {"light": {"turn_on": {}, "turn_off": {}}, "switch": {"turn_on": {}, "turn_off": {}},  # noqa: RUF012  (a test may shadow it)
                "weather": {"get_forecasts": {"response": {"optional": False}}},
                "calendar": {"get_events": {"response": {"optional": False}}},
                "todo": {"get_items": {"response": {"optional": False}}, "add_item": {}},
                "notify": {"mobile_app_test_phone": {}, "persistent_notification": {}}}

    async def get_services(self, request):
        await self._gate(request)
        return web.json_response([{"domain": d, "services": s} for d, s in self.SERVICES.items()])

    async def call_service(self, request):
        await self._gate(request)
        domain, service = request.match_info["domain"], request.match_info["service"]
        if service not in self.SERVICES.get(domain, {}):
            return web.json_response({"message": f"Service {domain}.{service} not found."}, status=400)
        body = await request.json() if request.can_read_body else {}
        self.writes.append({"type": f"service/{domain}.{service}", **body})
        ids = body.get("entity_id") or []
        for eid in [ids] if isinstance(ids, str) else ids:
            if eid in self.states and service in ("turn_on", "turn_off"):
                self.states[eid]["state"] = service.removeprefix("turn_")
        if "return_response" in request.query:
            response = {eid: {"forecast": [{"condition": "sunny", "temperature": 14}]} if domain == "weather"
                        else {"events": _between(self.events.get(eid, []), body)} if domain == "calendar" else {"items": []}
                        for eid in ([ids] if isinstance(ids, str) else ids)}
            return web.json_response({"changed_states": [], "service_response": response})
        return web.json_response([self.states[e] for e in ([ids] if isinstance(ids, str) else ids) if e in self.states])

    async def get_history(self, request):
        await self._gate(request)
        eid = request.query.get("filter_entity_id", "")
        s = self.states.get(eid)
        return web.json_response([[s]] if s else [])

    # ------------------------------------------------------------ websocket

    def _entry(self, eid: str) -> dict:
        e = copy.deepcopy(self.registry[eid])
        e["options"] = {"conversation": {"should_expose": eid in self.exposed}}
        return e

    def _command(self, msg: dict):
        t = msg.get("type")
        if t in WRITES:
            self.writes.append({k: v for k, v in msg.items() if k != "id"})
        if t == "config/entity_registry/list":
            return [{k: v for k, v in self._entry(eid).items() if k != "aliases"} for eid in self.registry]
        if t == "config/entity_registry/get_entries":
            return {eid: self._entry(eid) if eid in self.registry else None for eid in msg["entity_ids"]}
        if t == "config/area_registry/list":
            return copy.deepcopy(self.areas)
        if t == "config/device_registry/list":
            return copy.deepcopy(self.devices)
        if t == "homeassistant/expose_entity/list":
            return {"exposed_entities": {eid: {"conversation": True} for eid in sorted(self.exposed)}}
        if t == "homeassistant/expose_entity":
            unknown = [e for e in msg["entity_ids"] if e not in self.states and e not in self.registry]
            if unknown:
                raise LookupError(f"can't expose unknown entity {unknown[0]}")
            if "conversation" in msg["assistants"]:
                (self.exposed.update if msg["should_expose"] else self.exposed.difference_update)(msg["entity_ids"])
            return None
        if t == "homeassistant/expose_new_entities/get":
            return {"expose_new": self.expose_new}
        if t == "homeassistant/expose_new_entities/set":
            self.expose_new = bool(msg["expose_new"])
            return None
        if t == "config/entity_registry/update":
            eid = msg["entity_id"]
            if eid not in self.registry:
                raise LookupError("Entity not found")
            for k in UPDATE_KEYS & msg.keys():
                self.registry[eid][k] = msg[k]
            if "name" in msg:
                self.states[eid]["attributes"]["friendly_name"] = msg["name"] or self.registry[eid]["original_name"]
            return {"entity_entry": self._entry(eid)}
        if t == "subscribe_events":
            return None
        if t == "get_config":
            return {"version": VERSION}
        if t == "auth/current_user":
            return dict(self.user)
        if t == "auth/refresh_tokens":
            return [{"id": rid, "client_name": r["client_name"], "type": r["type"], "client_id": r.get("client_id")}
                    for rid, r in self.refresh.items()]
        if t == "auth/delete_refresh_token":
            if msg["refresh_token_id"] not in self.refresh:
                raise LookupError("Refresh token not found")
            self.writes.append({"type": t, "client_name": self.refresh[msg["refresh_token_id"]]["client_name"]})
            self._drop(msg["refresh_token_id"])
            return {}
        if t == "auth/long_lived_access_token":
            if any(r["client_name"] == msg["client_name"] for r in self.refresh.values()):
                raise LookupError(f"{msg['client_name']} already exists")
            access = self._new("llat")
            self.tokens.add(access)
            self.refresh[self._new("rid")] = {"token": self._new("refresh"), "access": access,
                                              "client_name": msg["client_name"], "type": "long_lived_access_token"}
            self.writes.append({"type": t, "client_name": msg["client_name"], "lifespan": msg["lifespan"]})
            return access
        raise KeyError(t)

    async def websocket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required", "ha_version": VERSION})
        first = await ws.receive_json()
        if first.get("type") != "auth" or first.get("access_token") not in self.tokens:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token or password"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": VERSION})
        self.subscribers[ws] = {}
        try:
            await self._serve(ws)
        finally:
            self.subscribers.pop(ws, None)
        return ws

    async def _serve(self, ws):
        async for m in ws:
            if m.type != WSMsgType.TEXT:
                break
            msg = json.loads(m.data)
            if msg.get("type") == "subscribe_events":
                self.subscribers[ws][msg["id"]] = msg.get("event_type") or "*"
            out = {"id": msg.get("id"), "type": "result"}
            try:
                out |= {"success": True, "result": self._command(msg)}
            except LookupError as e:
                out |= {"success": False, "error": {"code": "not_found", "message": str(e).strip("'")}}
            except KeyError as e:
                out |= {"success": False, "error": {"code": "unknown_command", "message": f"Unknown command {e}"}}
            await ws.send_json(out)

    async def fire(self, event_type: str, data: dict):
        """Sends an event to every subscription that asked for it (or for all events)."""
        for ws, subs in list(self.subscribers.items()):
            for sub_id, wanted in subs.items():
                if wanted in (event_type, "*"):
                    await ws.send_json({"id": sub_id, "type": "event",
                                        "event": {"event_type": event_type, "data": data, "origin": "LOCAL",
                                                  "time_fired": "2026-09-24T07:00:00+00:00"}})

    async def change(self, entity_id: str, state: str):
        """Changes a state and sends state_changed, as Home Assistant does."""
        old = copy.deepcopy(self.states.get(entity_id))
        new = {**(old or {"entity_id": entity_id, "attributes": {}}), "state": state,
               "last_changed": "2026-09-24T07:05:00+00:00"}
        self.states[entity_id] = new
        await self.fire("state_changed", {"entity_id": entity_id, "old_state": old, "new_state": new})

    # ------------------------------------------------------------ MCP (Streamable HTTP, JSON answers)

    def _tools(self) -> list[dict]:
        domains = {eid.split(".")[0] for eid in self.exposed}
        tools = TOOLS_ALWAYS + [t for d, ts in TOOLS_BY_DOMAIN.items() if d in domains for t in ts]
        return [{"name": n, "description": d, "inputSchema": INTENT_ARGS if "__Hass" in n else
                 {"type": "object", "properties": {}}} for n, d in tools]

    async def mcp(self, request):
        await self._gate(request)
        if not self.mcp_on:
            raise web.HTTPNotFound()
        msg = await request.json()
        if "id" not in msg:                     # a notification
            return web.Response(status=202)
        method, params = msg.get("method"), msg.get("params") or {}
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "home-assistant", "version": "fake"}}
        elif method == "tools/list":
            result = {"tools": self._tools()}
        elif method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            known = name in {t["name"] for t in self._tools()}
            if known:
                self.writes.append({"type": f"mcp/{name}", **args})
            result = {"content": [{"type": "text", "text": f"Done: {name}" if known else f"Error: no tool {name}"}],
                      "isError": not known}
        elif method == "ping":
            result = {}
        else:
            return web.json_response({"jsonrpc": "2.0", "id": msg["id"],
                                      "error": {"code": -32601, "message": f"no method {method}"}})
        return web.json_response({"jsonrpc": "2.0", "id": msg["id"], "result": result},
                                 headers={"mcp-session-id": "fake-session"})

    async def mcp_get(self, request):
        return web.Response(status=405)

    async def mcp_delete(self, request):
        return web.Response(status=200)

    # ------------------------------------------------------------ control

    async def fake_log(self, request):
        return web.json_response(self.writes)

    # ------------------------------------------------------------ running

    async def __aenter__(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        return self

    async def __aexit__(self, *exc):
        await self.runner.cleanup()


async def _main(port: int, token: str, seed: str, mcp_on: bool):
    async with FakeHA(port, token, seed, mcp_on) as ha:
        print(f"fake Home Assistant on {ha.url}", flush=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=18123)
    ap.add_argument("--token", default=TOKEN)
    ap.add_argument("--seed", choices=sorted(SEEDS), default="test",
                    help="test: the fixture the checks use; home: a made-up home for docs screenshots")
    ap.add_argument("--no-mcp", action="store_true", help="the MCP server not set up, for signing in to switch on")
    a = ap.parse_args()
    asyncio.run(_main(a.port, a.token, a.seed, not a.no_mcp))
