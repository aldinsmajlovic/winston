"""One Winston: the stream from winstond's listener, what it shows, and setting an extension's entity."""
import asyncio
import json
import logging
from typing import Any

import aiohttp
from yarl import URL

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_TOKEN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send

_LOGGER = logging.getLogger(__name__)
DOMAIN = "winston"
STATES = ["idle", "listening", "thinking", "speaking"]
READ_SECS = 30          # winstond sends something every 2 s; silence this long is a dead connection
BACKOFF = (1, 2, 5, 10, 30)


def base_url(host: str, port: int) -> URL:
    return URL.build(scheme="http", host=host, port=port)


class Hub:
    """Keeps the stream open, turns each snapshot into the entities to show (`items`, by unique id), adds
    and removes them, and tells them to redraw. Unique ids join this Winston's instance id and the parts
    with "#", which no extension id or key contains."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass, self.entry = hass, entry
        self.url = base_url(entry.data[CONF_HOST], entry.data[CONF_PORT])
        self.instance: str = entry.unique_id or ""
        self.items: dict[str, dict[str, Any]] = {}
        self.available = False
        self.adders: dict[str, Any] = {}             # platform -> its add_entities
        self.made: dict[str, str] = {}               # unique id -> platform, for what has been added
        self.signal = f"{DOMAIN}_{entry.entry_id}"
        self.event_signal = f"{self.signal}_event"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.entry.data[CONF_TOKEN]}"}

    async def run(self) -> None:
        session = async_get_clientsession(self.hass)
        tries = 0
        while True:
            try:
                async with session.get(self.url / "stream", headers=self._headers,
                                       timeout=aiohttp.ClientTimeout(total=None, sock_connect=10,
                                                                     sock_read=READ_SECS)) as r:
                    if r.status == 401:
                        _LOGGER.warning("Winston no longer knows this Home Assistant; pair it again")
                        self._down()
                        self.entry.async_start_reauth(self.hass)
                        return
                    r.raise_for_status()
                    tries = 0
                    kind = None
                    async for raw in r.content:
                        line = raw.decode().rstrip("\r\n")
                        if line.startswith("event: "):
                            kind = line[7:]
                        elif line.startswith("data: "):
                            data = json.loads(line[6:])
                            if kind == "snapshot":
                                self._snapshot(data)
                            elif kind == "event":
                                async_dispatcher_send(self.hass, self.event_signal, data)
            except (aiohttp.ClientError, TimeoutError, ValueError) as e:
                if self.available or tries == 0:
                    _LOGGER.info("Winston at %s is out of reach: %s", self.url, e or type(e).__name__)
            self._down()
            await asyncio.sleep(BACKOFF[min(tries, len(BACKOFF) - 1)])
            tries += 1

    @callback
    def _down(self) -> None:
        if self.available:
            self.available = False
            async_dispatcher_send(self.hass, self.signal)

    @callback
    def _snapshot(self, data: dict[str, Any]) -> None:
        inst = self.instance
        him = DeviceInfo(identifiers={(DOMAIN, inst)}, name=data["name"], manufacturer="Winston",
                         model="Voice assistant", sw_version=data.get("version"))
        items: dict[str, dict[str, Any]] = {}
        for room in data.get("rooms", []):
            items[f"{inst}#room#{room['name'].casefold()}"] = {
                "platform": "sensor", "device": him, "name": f"{room['name']} state", "value": room["state"],
                "device_class": "enum", "options": STATES, "icon": "mdi:account-voice"}
        for eng in data.get("engines", []):
            items[f"{inst}#engine#{eng['name']}"] = {
                "platform": "binary_sensor", "device": him, "name": eng["title"], "value": eng["up"],
                "device_class": "connectivity", "diagnostic": True}
        items[f"{inst}#announcement"] = {"platform": "event", "device": him, "name": "Announcement",
                                         "event_types": ["announcement"], "icon": "mdi:bullhorn"}
        devices = {(DOMAIN, inst)}
        devs = dr.async_get(self.hass)
        his = devs.async_get_or_create(config_entry_id=self.entry.entry_id, **him)     # made first: extensions hang off it
        for ext in data.get("extensions", []):
            ident = (DOMAIN, f"{inst}#{ext['id']}")
            devices.add(ident)
            dev = DeviceInfo(identifiers={ident}, name=ext["name"], manufacturer="Winston", model="Extension",
                             via_device_id=his.id)
            items[f"{inst}#{ext['id']}#nudge"] = {"platform": "event", "device": dev, "name": "Nudge",
                                                  "ext": ext["id"], "event_types": ["nudge"],
                                                  "icon": "mdi:message-badge"}
            for e in ext.get("entities", []):
                uid = f"{inst}#{ext['id']}#{e['key']}" + (f"#{e['entry']}" if e.get("entry") else "")
                items[uid] = {**e, "platform": e["kind"], "ext": ext["id"], "device": dev,
                              "event_types": e.get("options") or ["fired"]}
        self.items, self.available = items, True

        for platform, add in self.adders.items():
            new = [uid for uid, i in items.items() if i["platform"] == platform and uid not in self.made]
            for uid in new:
                self.made[uid] = platform
            if new:
                from .entity import make         # here: entity.py imports this module
                add([make(self, uid) for uid in new])
        ents = er.async_get(self.hass)
        for uid in [u for u in self.made if u not in items]:
            platform = self.made.pop(uid)
            if eid := ents.async_get_entity_id(platform, DOMAIN, uid):
                ents.async_remove(eid)
        for d in dr.async_entries_for_config_entry(devs, self.entry.entry_id):
            if not d.identifiers & devices:
                devs.async_remove_device(d.id)
        async_dispatcher_send(self.hass, self.signal)

    async def set(self, ext: str, key: str, entry: str | None, value: Any = None, press: bool = False) -> None:
        """An extension's switch, number, select or text set, or its button pressed; its handler's error is
        raised for Home Assistant to show."""
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(self.url / "set", headers=self._headers,
                                    json={"extension": ext, "key": key, "entry": entry, "value": value, "press": press},
                                    timeout=aiohttp.ClientTimeout(total=40)) as r:
                body = await r.json(content_type=None) if r.content_length != 0 else {}
                if r.status == 401:
                    self.entry.async_start_reauth(self.hass)
                    raise HomeAssistantError("Winston no longer knows this Home Assistant; pair it again")
        except (aiohttp.ClientError, TimeoutError) as e:
            raise HomeAssistantError(f"Winston is out of reach: {e}") from e
        if not (body or {}).get("ok"):
            raise HomeAssistantError(f"Winston: {(body or {}).get('error') or r.status}")
