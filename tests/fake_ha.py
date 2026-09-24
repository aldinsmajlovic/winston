"""A fake Home Assistant for tests that must not touch the real one. It speaks the subset Winston uses:
REST (/api/config, /api/states), the websocket API (auth; entity, area and device registry lists;
entity_registry/get_entries and /update; expose_entity, expose_entity/list, expose_new_entities/get
and /set; subscribe_events) and the MCP server's Streamable HTTP endpoint (initialize, tools/list), whose
tools follow the exposed domains as the real one's do.

Every write is recorded, so a test can check exactly what Winston sent (for example that it never sent
a "name"). Control endpoints for tests:

    GET  /fake/log             the writes so far, in order
    GET  /fake/entity/<id>     one entity's registry entry and exposure
    POST /fake/reset           back to the seed, log cleared
    POST /fake/admin?on=0      the token is not an admin: registry and exposure commands are refused
    POST /fake/delay?s=10      every answer waits s seconds (Home Assistant hanging)

Run it alone:  .venv/bin/python tests/fake_ha.py [--port 18123] [--token fake-ha-token] [--seed test|home]
Or in-process: `async with FakeHA(port) as ha: ...` (ha.url, ha.writes, ha.reset())."""
import argparse
import asyncio
import copy
import json

from aiohttp import WSMsgType, web

TOKEN = "fake-ha-token"
VERSION = "2026.9.3"

# A small generic home: Swedish names on the Home Assistant side, as a household would have them.
AREAS = [{"area_id": "living_room", "name": "Living room", "aliases": []},
         {"area_id": "kitchen", "name": "Kitchen", "aliases": []}]
DEVICES = [
    {"id": "dev_ceiling", "name": "TAKLAMPA E27", "name_by_user": None, "area_id": "living_room"},
    {"id": "dev_shelf", "name": "HYLLA E27", "name_by_user": "Hyllan", "area_id": "living_room"},
    {"id": "dev_kitchen", "name": "Kitchen spots", "name_by_user": None, "area_id": "kitchen"},
    {"id": "dev_phone", "name": "Phone", "name_by_user": None, "area_id": None},
    {"id": "dev_plex", "name": "Plex client", "name_by_user": None, "area_id": None},
]
# entity_id, friendly name, state, attributes, registry (None: not in the registry), exposed
ENTITIES = [
    ("light.taklampa", "Taklampa", "on", {"brightness": 128},
     {"device_id": "dev_ceiling", "name": None, "aliases": ["taket"]}, True),
    ("light.hylla", "Hyllan", "unavailable", {}, {"device_id": "dev_shelf", "name": None, "aliases": []}, True),
    ("light.kok", "Köksspotlights", "off", {}, {"device_id": "dev_kitchen", "name": "Köksspotlights",
                                                "aliases": [], "area_id": "kitchen"}, False),
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
    ("light.golvlampa", "Golvlampa", "on", {"brightness": 128, "color_temp_kelvin": 2700},
     {**_no, "device_id": "dev_golv"}, True),
    ("light.hylla_kajplats", "HYLLA KAJPLATS E27 WS G95 clear 806lm", "unavailable", {},
     {**_no, "device_id": "dev_hylla"}, True),
    ("sensor.phone_battery", "Telefon batteri", "81", {"unit_of_measurement": "%"},
     {**_no, "device_id": "dev_phone"}, True),
    ("button.wake_pc", "PC", "2026-09-24T07:12:00+00:00", {}, {**_no, "device_id": "dev_pc"}, True),
    ("binary_sensor.ytterdorr", "Ytterdörr", "off", {"device_class": "door"}, {**_no, "device_id": "dev_door"}, False),
    ("person.someone", "Someone", "home", {}, _no, True),
    ("weather.forecast_home", "Forecast", "cloudy", {"temperature": 12}, _no, True),
    ("todo.shopping_list", "Shopping list", "3", {}, _no, True),
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

ADMIN_ONLY = {"config/entity_registry/list", "config/entity_registry/get_entries", "config/entity_registry/update",
              "config/area_registry/list", "config/device_registry/list", "homeassistant/expose_entity",
              "homeassistant/expose_entity/list", "homeassistant/expose_new_entities/get",
              "homeassistant/expose_new_entities/set"}
WRITES = {"config/entity_registry/update", "homeassistant/expose_entity", "homeassistant/expose_new_entities/set"}
UPDATE_KEYS = {"aliases", "name", "area_id", "icon", "new_entity_id", "disabled_by", "hidden_by"}


class FakeHA:
    def __init__(self, port: int = 18123, token: str = TOKEN, seed: str = "test"):
        self.port, self.token = port, token
        self.areas, self.devices, self.entities = SEEDS[seed]
        self.reset()
        self.app = web.Application()
        self.app.add_routes([
            web.get("/api/", self.api_root), web.get("/api/config", self.get_config), web.get("/api/states", self.get_states),
            web.get("/api/states/{eid}", self.get_state), web.get("/api/websocket", self.websocket),
            web.post("/api/mcp", self.mcp), web.get("/api/mcp", self.mcp_get), web.delete("/api/mcp", self.mcp_delete),
            web.get("/fake/log", self.fake_log), web.get("/fake/entity/{eid}", self.fake_entity),
            web.post("/fake/reset", self.fake_reset), web.post("/fake/admin", self.fake_admin),
            web.post("/fake/delay", self.fake_delay),
        ])
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
        self.admin, self.delay = True, 0.0
        self.writes: list[dict] = []

    # ------------------------------------------------------------ REST

    def _authed(self, request: web.Request) -> bool:
        return request.headers.get("Authorization") == f"Bearer {self.token}"

    async def _gate(self, request: web.Request):
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self._authed(request):
            raise web.HTTPUnauthorized()

    async def api_root(self, request):
        await self._gate(request)
        return web.json_response({"message": "API running."})

    async def get_config(self, request):
        await self._gate(request)
        return web.json_response({"version": VERSION, "location_name": "Fake home", "country": "SE",
                                  "currency": "SEK", "time_zone": "Europe/Stockholm", "language": "en",
                                  "unit_system": {"temperature": "°C", "length": "km"},
                                  "components": ["http", "websocket_api", "conversation", "mcp_server", "light"]})

    async def get_states(self, request):
        await self._gate(request)
        return web.json_response(list(self.states.values()))

    async def get_state(self, request):
        await self._gate(request)
        s = self.states.get(request.match_info["eid"])
        return web.json_response(s) if s else web.json_response({"message": "Entity not found."}, status=404)

    # ------------------------------------------------------------ websocket

    def _entry(self, eid: str) -> dict:
        e = copy.deepcopy(self.registry[eid])
        e["options"] = {"conversation": {"should_expose": eid in self.exposed}}
        return e

    def _command(self, msg: dict):
        t = msg.get("type")
        if t in ADMIN_ONLY and not self.admin:
            raise PermissionError
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
        raise KeyError(t)

    async def websocket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required", "ha_version": VERSION})
        first = await ws.receive_json()
        if first.get("type") != "auth" or first.get("access_token") != self.token:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token or password"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": VERSION})
        async for m in ws:
            if m.type != WSMsgType.TEXT:
                break
            msg = json.loads(m.data)
            if self.delay:
                await asyncio.sleep(self.delay)
            out = {"id": msg.get("id"), "type": "result"}
            try:
                out |= {"success": True, "result": self._command(msg)}
            except PermissionError:
                out |= {"success": False, "error": {"code": "unauthorized", "message": "Unauthorized"}}
            except LookupError as e:
                out |= {"success": False, "error": {"code": "not_found", "message": str(e).strip("'")}}
            except KeyError as e:
                out |= {"success": False, "error": {"code": "unknown_command", "message": f"Unknown command {e}"}}
            await ws.send_json(out)
        return ws

    # ------------------------------------------------------------ MCP (Streamable HTTP, JSON answers)

    def _tools(self) -> list[dict]:
        domains = {eid.split(".")[0] for eid in self.exposed}
        tools = TOOLS_ALWAYS + [t for d, ts in TOOLS_BY_DOMAIN.items() if d in domains for t in ts]
        return [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": {}}} for n, d in tools]

    async def mcp(self, request):
        await self._gate(request)
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

    async def fake_entity(self, request):
        eid = request.match_info["eid"]
        return web.json_response({"registry": self._entry(eid) if eid in self.registry else None,
                                  "state": self.states.get(eid), "exposed": eid in self.exposed})

    async def fake_reset(self, request):
        self.reset()
        return web.json_response({"ok": True})

    async def fake_admin(self, request):
        self.admin = request.query.get("on", "1") not in ("0", "false")
        return web.json_response({"admin": self.admin})

    async def fake_delay(self, request):
        self.delay = float(request.query.get("s", "0"))
        return web.json_response({"delay": self.delay})

    # ------------------------------------------------------------ running

    async def __aenter__(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", self.port).start()
        return self

    async def __aexit__(self, *exc):
        await self.runner.cleanup()


async def _main(port: int, token: str, seed: str):
    async with FakeHA(port, token, seed) as ha:
        print(f"fake Home Assistant on {ha.url}", flush=True)
        await asyncio.Event().wait()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=18123)
    ap.add_argument("--token", default=TOKEN)
    ap.add_argument("--seed", choices=sorted(SEEDS), default="test",
                    help="test: the fixture the checks use; home: a made-up home for docs screenshots")
    a = ap.parse_args()
    asyncio.run(_main(a.port, a.token, a.seed))
