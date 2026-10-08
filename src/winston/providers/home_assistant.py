"""Home Assistant provider: keeps HA's Assist exposure equal to the allowlist in config
(providers.home_assistant.expose), adds the names people say as aliases, and turns off HA's
auto-expose. Least privilege (plan §6 rule 5): the model sees exactly what is listed, nothing else.

Two steps. `plan()` is pure: from Winston's config before and after a save and a snapshot of Home
Assistant it works out what to expose, what to un-expose and which aliases to add or remove.
`HomeAssistant.apply()` then writes exactly that. Winston writes aliases only: it never renames an
entity in Home Assistant, whose names are the household's own. An alias is removed only when an
earlier config listed it and the new one does not, so aliases a person added in Home Assistant stay.
An entity id Home Assistant does not know is skipped and reported, never an error for the rest."""
import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

import aiohttp
from pydantic import BaseModel

from winston.config.schema import ExposedEntity, HomeAssistantConfig, WinstonConfig
from winston.core.common import key

TIMEOUT = 5             # seconds for one read; a save waits at most APPLY_TIMEOUT for the whole apply
APPLY_TIMEOUT = 5
TOOLS_TTL = 60          # seconds the MCP tool list is cached


class HAError(RuntimeError):
    def __init__(self, command: str, error: dict | None):
        self.code = (error or {}).get("code", "")
        super().__init__(f"{command}: {(error or {}).get('message') or self.code or 'failed'}")


# ---------------------------------------------------------------- what Home Assistant has now


@dataclass
class Snapshot:
    """What Home Assistant has, as far as the token may read it. registry is None when the registry
    commands were refused (not an admin token); exposed and expose_new are None the same way."""
    states: dict[str, dict]
    registry: dict[str, dict] | None = None      # entity_id -> registry entry (with aliases)
    exposed: set[str] | None = None              # exposed to Assist ("conversation")
    expose_new: bool | None = None               # HA's "expose new entities" for Assist
    areas: dict[str, str] = field(default_factory=dict)     # area_id -> name
    devices: dict[str, dict] = field(default_factory=dict)  # device_id -> entry
    version: str | None = None
    mcp_server: bool | None = None
    errors: list[str] = field(default_factory=list)

    def known(self, eid: str) -> bool:
        return eid in self.states or (self.registry is not None and eid in self.registry)

    def display_name(self, eid: str) -> str | None:
        """The name Home Assistant shows and Assist matches: its friendly name."""
        s = self.states.get(eid)
        if s and (n := s.get("attributes", {}).get("friendly_name")):
            return n
        r = (self.registry or {}).get(eid) or {}
        return r.get("name") or r.get("original_name")

    def aliases(self, eid: str) -> list[str]:
        r = (self.registry or {}).get(eid) or {}
        return [a for a in r.get("aliases") or [] if isinstance(a, str)]

    def area(self, eid: str) -> tuple[str | None, str | None]:
        """(area_id, name): the entity's own area, else its device's, as Home Assistant resolves it."""
        r = (self.registry or {}).get(eid) or {}
        aid = r.get("area_id") or (self.devices.get(r.get("device_id") or "") or {}).get("area_id")
        return aid, self.areas.get(aid) if aid else None

    def device(self, eid: str) -> str | None:
        r = (self.registry or {}).get(eid) or {}
        d = self.devices.get(r.get("device_id") or "")
        return (d.get("name_by_user") or d.get("name")) if d else None


def state_text(s: dict | None) -> str | None:
    """A state as a person reads it: "on, 50%", "81 %", "24 Sep 07:12"."""
    if not s:
        return None
    state, attrs = str(s.get("state")), s.get("attributes", {})
    if len(state) >= 10 and state[:4].isdigit() and state[4] == "-":   # buttons and timestamp sensors: ISO time
        try:
            return datetime.fromisoformat(state).astimezone().strftime("%-d %b %H:%M")
        except ValueError:
            pass
    if state == "on" and isinstance(b := attrs.get("brightness"), (int, float)):
        return f"on, {round(b / 255 * 100)}%"
    unit = attrs.get("unit_of_measurement")
    return f"{state} {unit}" if unit else state


# ---------------------------------------------------------------- the plan (pure)


def wanted_aliases(ent: ExposedEntity | None, ha_name: str | None) -> list[str]:
    """The aliases config asks for: its name (said by people, so an alias, never HA's name) and its
    aliases, without repeats and without Home Assistant's own name, which Assist matches anyway."""
    if ent is None:
        return []
    out, seen = [], {key(ha_name)} if ha_name else set()
    for a in [ent.name, *ent.aliases]:
        if a and a.strip() and key(a) not in seen:
            seen.add(key(a))
            out.append(a.strip())
    return out


class ExposurePlan(BaseModel):
    expose: list[str] = []                          # to expose to Assist (not exposed now)
    unexpose: list[str] = []                        # exposed now, not on the list or in a listed domain
    aliases_add: dict[str, list[str]] = {}
    aliases_remove: dict[str, list[str]] = {}       # only aliases an earlier config listed
    skipped: list[str] = []                         # listed ids Home Assistant does not know
    no_registry: list[str] = []                     # known but not in the entity registry: no aliases possible
    auto_expose_off: bool = False                   # HA's "expose new entities" is on and will be turned off
    targets: list[str] = []                         # everything that should be exposed after the apply

    @property
    def empty(self) -> bool:
        return not (self.expose or self.unexpose or self.aliases_add or self.aliases_remove or self.auto_expose_off)


def plan(new: HomeAssistantConfig, old: HomeAssistantConfig | None, snap: Snapshot) -> ExposurePlan:
    p = ExposurePlan()
    listed = [eid for eid in new.expose if snap.known(eid)]
    p.skipped = [eid for eid in new.expose if not snap.known(eid)]
    domains = set(new.expose_domains)
    p.targets = sorted(set(listed) | {eid for eid in snap.states if eid.split(".")[0] in domains})
    if snap.exposed is not None:
        p.expose = [eid for eid in p.targets if eid not in snap.exposed]
        p.unexpose = sorted(eid for eid in snap.exposed if eid not in p.targets)
    p.auto_expose_off = snap.expose_new is True
    if snap.registry is None:
        return p
    for eid in dict.fromkeys([*new.expose, *(old.expose if old else {})]):
        if eid in p.skipped or not snap.known(eid):
            continue
        ha_name = snap.display_name(eid)
        want = wanted_aliases(new.expose.get(eid), ha_name)
        had = wanted_aliases(old.expose.get(eid), ha_name) if old else []
        have = {key(a) for a in snap.aliases(eid)}
        add = [a for a in want if key(a) not in have]
        keep = {key(a) for a in want}
        remove = [a for a in snap.aliases(eid) if key(a) in {key(h) for h in had} and key(a) not in keep]
        if (add or remove) and eid not in snap.registry:
            if add:
                p.no_registry.append(eid)
            continue
        if add:
            p.aliases_add[eid] = add
        if remove:
            p.aliases_remove[eid] = remove
    return p


class HAApply(BaseModel):
    """What a save (or winston ha expose) did in Home Assistant."""
    applied: bool                                   # every planned write went through
    exposed: list[str] = []
    unexposed: list[str] = []
    aliases_added: dict[str, list[str]] = {}
    aliases_removed: dict[str, list[str]] = {}
    skipped: list[str] = []                         # listed ids Home Assistant does not know
    no_registry: list[str] = []                     # could not take aliases (not in the entity registry)
    auto_expose_turned_off: bool = False
    error: str | None = None


# ---------------------------------------------------------------- talking to Home Assistant


class _Socket:
    """Home Assistant's websocket API: one authenticated connection, one call at a time."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str):
        self.session, self.url, self.token, self.n = session, url, token, 0

    async def __aenter__(self):
        self.ws = await self.session.ws_connect(f"{self.url}/api/websocket", timeout=aiohttp.ClientWSTimeout(ws_close=TIMEOUT))
        if (await self.ws.receive_json(timeout=TIMEOUT))["type"] != "auth_required":
            raise RuntimeError("not a Home Assistant websocket")
        await self.ws.send_json({"type": "auth", "access_token": self.token})
        if (await self.ws.receive_json(timeout=TIMEOUT))["type"] != "auth_ok":
            raise RuntimeError("Token rejected")
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()

    async def call(self, type: str, **kw):
        self.n += 1
        await self.ws.send_json({"id": self.n, "type": type, **kw})
        while True:
            r = await self.ws.receive_json(timeout=TIMEOUT)
            if r.get("id") == self.n:
                if not r.get("success", True):
                    raise HAError(type, r.get("error"))
                return r.get("result")


# Plain words for the tools Home Assistant's MCP server offers, by the name after "domain__".
TOOL_LABELS = {
    "HassTurnOn": "Turn things on and off", "HassTurnOff": "Turn things on and off",
    "HassLightSet": "Set brightness and colour",
    "GetLiveContext": "Look at the home", "HassGetState": "Look at the home",
    "GetDateTime": "Know the date and time",
    "get_items": "Read, add and tick off the to-do list", "HassListAddItem": "Read, add and tick off the to-do list",
    "HassListCompleteItem": "Read, add and tick off the to-do list",
    "HassListRemoveItem": "Read, add and tick off the to-do list",
    "HassShoppingListAddItem": "Add to the shopping list",
    "HassBroadcast": "Broadcast on a satellite",
    "HassStartTimer": "Set and manage timers", "HassCancelTimer": "Set and manage timers",
    "HassCancelAllTimers": "Set and manage timers", "HassIncreaseTimer": "Set and manage timers",
    "HassDecreaseTimer": "Set and manage timers", "HassPauseTimer": "Set and manage timers",
    "HassUnpauseTimer": "Set and manage timers", "HassTimerStatus": "Set and manage timers",
    "HassClimateSetTemperature": "Set the temperature", "HassClimateGetTemperature": "Read the temperature",
    "HassGetWeather": "Check the weather",
    "HassSetPosition": "Open and close things partway",
    "HassMediaPause": "Play and pause media", "HassMediaUnpause": "Play and pause media",
    "HassMediaNext": "Play and pause media", "HassMediaPrevious": "Play and pause media",
    "HassSetVolume": "Set the volume", "HassMediaSearchAndPlay": "Find and play media",
    "HassVacuumStart": "Start and dock the vacuum", "HassVacuumReturnToBase": "Start and dock the vacuum",
    "HassFanSetSpeed": "Set a fan's speed", "HassLawnMowerStartMowing": "Start and dock the mower",
    "HassLawnMowerDock": "Start and dock the mower",
}
# Where a tool comes from, by its prefix: Assist itself, or the domain whose entities bring it.
TOOL_SOURCES = {"intent": "Assist", "homeassistant": "Assist", "llm": "Assist", "light": "Lights",
                "todo": "To-do", "assist_satellite": "Assist satellite", "climate": "Climate",
                "media_player": "Media", "cover": "Covers", "vacuum": "Vacuum", "fan": "Fans",
                "weather": "Weather", "timer": "Timers", "shopping_list": "Shopping list"}


def tool_label(name: str) -> str:
    bare = name.split("__", 1)[-1]
    if bare in TOOL_LABELS:
        return TOOL_LABELS[bare]
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", bare.removeprefix("Hass")).replace("_", " ").strip()
    return words[:1].upper() + words[1:].lower()


class HATool(BaseModel):
    label: str              # plain words; tools that do one job share a label and a row
    names: list[str]        # the MCP tool names, "domain__Name"
    domain: str             # the prefix: the domain the tool comes from ("intent" is Assist itself)
    source: str             # that domain in words: Assist, Lights, To-do...
    description: str        # Home Assistant's own description of the first tool


_tools_cache: dict[tuple[str, str], tuple[float, list[HATool]]] = {}


class HomeAssistant:
    """Home Assistant as Winston reaches it: REST reads, the websocket API and the MCP server's tools, with
    the token from the variable config names. `url` is an address to try instead of the saved one (a
    draft on the settings page); it is tried with Home Assistant switched off too."""

    def __init__(self, cfg: WinstonConfig, url: str | None = None):
        ha, saved = cfg.providers.ha, cfg.providers.home_assistant
        if ha is None and not url:
            raise RuntimeError("providers.home_assistant is not configured or is switched off")
        env = saved.token_env if saved else "HA_TOKEN"
        self.cfg, self.ha = cfg, ha
        self.url, self.token = (url or ha.url).rstrip("/"), os.environ.get(env, "")
        if not self.token:
            raise RuntimeError(f"{env} is not set")

    @property
    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def _session(self, timeout: float = TIMEOUT * 3) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout, sock_connect=min(timeout, TIMEOUT)))

    async def _get(self, s: aiohttp.ClientSession, path: str):
        r = await s.get(f"{self.url}{path}", headers=self._headers)
        if r.status == 401:
            raise RuntimeError("Token rejected")
        r.raise_for_status()
        return await r.json()

    async def get(self, path: str, timeout: float = TIMEOUT * 3):
        """One REST read as JSON: "/api/states", "/api/states/<entity_id>", "/api/services". A refused token
        raises RuntimeError("Token rejected"), anything else aiohttp's error."""
        async with self._session(timeout) as s:
            return await self._get(s, path)

    async def ping(self) -> dict:
        j = await self.get("/api/config")
        return {"version": j.get("version"), "location": j.get("location_name"),
                "mcp_server": "mcp_server" in j.get("components", [])}

    async def snapshot(self, ids: list[str] | None = None) -> Snapshot:
        """Read Home Assistant: states, exposure, areas, devices and the registry entries of ids (None: of
        every entity with a state, for the picker; with ids, what is exposed is read too). Registry and exposure
        reads need an admin token; refused, they stay None and the snapshot says why."""
        async with self._session() as s:
            conf = await self._get(s, "/api/config")
            snap = Snapshot(states={e["entity_id"]: e for e in await self._get(s, "/api/states")},
                            version=conf.get("version"), mcp_server="mcp_server" in conf.get("components", []))
            async with _Socket(s, self.url, self.token) as ws:
                try:
                    ex = await ws.call("homeassistant/expose_entity/list") or {}
                    snap.exposed = {eid for eid, v in ex.get("exposed_entities", {}).items() if v.get("conversation")}
                    got = await ws.call("homeassistant/expose_new_entities/get", assistant="conversation") or {}
                    snap.expose_new = got.get("expose_new")
                except HAError as e:
                    snap.errors.append(str(e))
                try:
                    entries = await ws.call("config/entity_registry/get_entries",
                                            entity_ids=list(snap.states) if ids is None else
                                            list(dict.fromkeys([*ids, *sorted(snap.exposed or ())]))) or {}
                    snap.registry = {k: v for k, v in entries.items() if v}
                    snap.areas = {a["area_id"]: a["name"] for a in await ws.call("config/area_registry/list") or []}
                    snap.devices = {d["id"]: d for d in await ws.call("config/device_registry/list") or []}
                except HAError as e:
                    snap.registry = None
                    snap.errors.append(str(e))
        return snap

    async def plan(self, old: HomeAssistantConfig | None = None) -> tuple[ExposurePlan, Snapshot]:
        """What an apply would do, with old the config before a save (None: remove no aliases)."""
        snap = await self.snapshot(list(dict.fromkeys([*self.ha.expose, *(old.expose if old else {})])))
        return plan(self.ha, old, snap), snap

    async def apply(self, old: HomeAssistantConfig | None = None, out: HAApply | None = None) -> HAApply:
        """Make Home Assistant match the config: plan, then write the plan. Never raises for Home Assistant
        being away or refusing: the result says what went through and why the rest did not. out collects
        progress as it goes, so a caller that times out still knows what was written."""
        out = out if out is not None else HAApply(applied=False)
        try:
            p, snap = await self.plan(old)
        except Exception as e:                                          # noqa: BLE001
            out.error = f"Home Assistant not reachable: {e}" if not str(e).startswith("Token") else str(e)
            return out
        out.skipped, out.no_registry = p.skipped, p.no_registry
        if snap.exposed is None or snap.registry is None:
            out.error = "; ".join(snap.errors) or "Home Assistant refused to list exposure or the registry"
            return out
        try:
            async with self._session() as s, _Socket(s, self.url, self.token) as ws:
                if p.auto_expose_off:
                    await ws.call("homeassistant/expose_new_entities/set", assistant="conversation", expose_new=False)
                    out.auto_expose_turned_off = True
                if p.unexpose:
                    await ws.call("homeassistant/expose_entity", assistants=["conversation"],
                                  entity_ids=p.unexpose, should_expose=False)
                    out.unexposed = p.unexpose
                if p.expose:
                    await ws.call("homeassistant/expose_entity", assistants=["conversation"],
                                  entity_ids=p.expose, should_expose=True)
                    out.exposed = p.expose
                if out.exposed or out.unexposed:        # the MCP server's tools follow the exposed domains
                    _tools_cache.pop((self.url, self.ha.mcp_path), None)
                for eid in dict.fromkeys([*p.aliases_add, *p.aliases_remove]):
                    add, rm = p.aliases_add.get(eid, []), {key(a) for a in p.aliases_remove.get(eid, [])}
                    current = snap.registry[eid].get("aliases") or []
                    kept = [a for a in current if not (isinstance(a, str) and key(a) in rm)]
                    # aliases only: "name" is never sent, so Home Assistant's own name stays
                    await ws.call("config/entity_registry/update", entity_id=eid, aliases=kept + add)
                    if add:
                        out.aliases_added[eid] = add
                    if rm:
                        out.aliases_removed[eid] = p.aliases_remove[eid]
            out.applied = True
        except Exception as e:                                          # noqa: BLE001
            out.error = str(e) or type(e).__name__
        return out

    async def tools(self) -> list[HATool]:
        """The tools Home Assistant's MCP server offers now, one row per job. Cached for a minute."""
        cached = (self.url, self.ha.mcp_path)
        if (hit := _tools_cache.get(cached)) and time.monotonic() - hit[0] < TOOLS_TTL:
            return hit[1]
        import mcp
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        async def fetch():
            async with create_mcp_http_client(headers=self._headers) as hc, \
                    streamable_http_client(self.url + self.ha.mcp_path, http_client=hc) as (r, w), \
                    mcp.ClientSession(r, w) as cs:
                await cs.initialize()
                return (await cs.list_tools()).tools

        listed = await asyncio.wait_for(fetch(), TIMEOUT * 2)
        rows: dict[str, HATool] = {}
        for t in listed:
            domain = t.name.split("__", 1)[0] if "__" in t.name else ""
            label = tool_label(t.name)
            if label in rows:
                rows[label].names.append(t.name)
            else:
                rows[label] = HATool(label=label, names=[t.name], domain=domain,
                                     source=TOOL_SOURCES.get(domain, domain.replace("_", " ").capitalize() or "Assist"),
                                     description=(t.description or "").split("\n")[0][:200])
        out = list(rows.values())
        _tools_cache[cached] = (time.monotonic(), out)
        return out


def ha_changed(old: WinstonConfig, new: WinstonConfig) -> bool:
    """Does a save change what Home Assistant should expose: the list, the domains or the switch."""
    a, b = old.providers.home_assistant, new.providers.home_assistant
    pick = (lambda h: None if h is None else h.model_dump(include={"expose", "expose_domains", "enabled"}))
    return pick(a) != pick(b)


async def apply_saved(old: WinstonConfig, new: WinstonConfig, timeout: float = APPLY_TIMEOUT) -> HAApply | None:
    """The save hook: apply the new list when Home Assistant is on, within timeout. Never raises."""
    if new.providers.ha is None:
        return None
    try:
        ha = HomeAssistant(new)
    except RuntimeError as e:
        return HAApply(applied=False, error=str(e))
    out = HAApply(applied=False)
    try:
        return await asyncio.wait_for(ha.apply(old.providers.home_assistant, out), timeout)
    except asyncio.TimeoutError:
        out.applied, out.error = False, f"Home Assistant did not answer within {timeout:g} s"
        return out


# ---------------------------------------------------------------- what the page shows


class HAEntity(BaseModel):
    entity_id: str
    domain: str
    in_ha: bool                         # Home Assistant knows the id
    exposed: bool | None                # exposed to Assist; None when the token may not read exposure
    friendly_name: str | None           # Home Assistant's own name (never changed by Winston)
    aliases: list[str]                  # aliases in Home Assistant now
    area: str | None                    # the entity's area, else its device's
    area_id: str | None
    device: str | None
    state: str | None                   # raw state
    state_text: str | None              # "on, 50%", "81 %"
    listed: bool                        # on Winston's list (providers.home_assistant.expose)
    applied: bool | None = None         # listed only: exposed, with every alias config asks for
    missing_aliases: list[str] = []     # listed only: aliases config asks for that Home Assistant lacks


class HAExposure(BaseModel):
    ok: bool                            # Home Assistant answered
    detail: str = ""                    # why not, or what could not be read
    version: str | None = None
    mcp_server: bool | None = None      # the mcp_server integration is loaded
    registry: bool = False              # the registry could be read (admin token): areas and aliases are real
    auto_expose: bool | None = None     # HA's "expose new entities" for Assist (Winston turns it off)
    in_sync: bool = False               # an apply now would change nothing
    entities: list[HAEntity] = []       # the list, in config order
    domains: dict[str, list[str]] = {}  # expose_domains: what each brings in
    drift: list[HAEntity] = []          # exposed in Home Assistant but neither listed nor in a listed domain
    pending: ExposurePlan | None = None # what Save (or an apply) would still do; aliases to remove not included


class HAArea(BaseModel):
    area_id: str | None                 # None: no room
    name: str | None
    entities: list[HAEntity]


class HARegistry(BaseModel):
    ok: bool
    detail: str = ""
    areas_known: bool = False           # False: the token may not read the registry; one group, no aliases
    groups: list[HAArea] = []


def _entity(snap: Snapshot, eid: str, listed: bool) -> HAEntity:
    s = snap.states.get(eid)
    aid, area = snap.area(eid)
    return HAEntity(entity_id=eid, domain=eid.split(".")[0], in_ha=snap.known(eid),
                    exposed=None if snap.exposed is None else eid in snap.exposed,
                    friendly_name=snap.display_name(eid), aliases=snap.aliases(eid), area=area, area_id=aid,
                    device=snap.device(eid), state=s.get("state") if s else None, state_text=state_text(s),
                    listed=listed)


def exposure_view(ha: HomeAssistantConfig, snap: Snapshot) -> HAExposure:
    p = plan(ha, None, snap)
    out = HAExposure(ok=True, detail="; ".join(snap.errors), version=snap.version, mcp_server=snap.mcp_server,
                     registry=snap.registry is not None, auto_expose=snap.expose_new, pending=p,
                     in_sync=p.empty and snap.exposed is not None and snap.registry is not None)
    for eid, ent in ha.expose.items():
        e = _entity(snap, eid, True)
        if e.in_ha and snap.registry is not None:
            e.missing_aliases = p.aliases_add.get(eid, [])
        if e.in_ha and e.exposed is not None and snap.registry is not None:
            e.applied = e.exposed and not e.missing_aliases and eid not in p.no_registry
        out.entities.append(e)
    domains = set(ha.expose_domains)
    out.domains = {d: sorted(eid for eid in snap.states if eid.split(".")[0] == d) for d in ha.expose_domains}
    out.drift = [_entity(snap, eid, False) for eid in sorted(snap.exposed or ())
                 if eid not in ha.expose and eid.split(".")[0] not in domains]
    return out


def registry_view(ha: HomeAssistantConfig, snap: Snapshot) -> HARegistry:
    groups: dict[str | None, HAArea] = {}
    for eid in sorted(snap.states):
        e = _entity(snap, eid, eid in ha.expose)
        g = groups.setdefault(e.area_id, HAArea(area_id=e.area_id, name=e.area, entities=[]))
        g.entities.append(e)
    ordered = sorted(groups.values(), key=lambda g: (g.area_id is None, (g.name or "").casefold()))
    return HARegistry(ok=True, detail="; ".join(snap.errors), areas_known=snap.registry is not None, groups=ordered)
