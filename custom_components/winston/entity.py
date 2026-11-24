"""Winston's entities: one class per kind, each drawing what the hub's latest snapshot says."""
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.event import EventEntity
from homeassistant.components.number import NumberEntity
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.text import TextEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.util import dt as dt_util

from .hub import Hub


def setup_platform(platform: str):
    """A platform's async_setup_entry: it hands the hub its add_entities, and the next snapshot adds to it."""
    async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
        entry.runtime_data.adders[platform] = async_add_entities
    return async_setup_entry


class WinstonEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: Hub, uid: str):
        self.hub = hub
        i = hub.items[uid]
        self._attr_unique_id = uid
        self._attr_name = i["name"]
        self._attr_device_info = i["device"]
        if i.get("icon"):
            self._attr_icon = i["icon"]
        if i.get("diagnostic"):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def item(self) -> dict[str, Any]:
        return self.hub.items.get(self.unique_id or "", {})

    @property
    def available(self) -> bool:
        return self.hub.available and bool(self.item)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.item.get("attributes") or None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, self.hub.signal, self._redraw))

    @callback
    def _redraw(self) -> None:
        self.async_write_ha_state()

    async def _set(self, **kw: Any) -> None:
        i = self.item
        await self.hub.set(i["ext"], i["key"], i.get("entry"), **kw)


class WinstonSensor(WinstonEntity, SensorEntity):
    def __init__(self, hub: Hub, uid: str):
        super().__init__(hub, uid)
        i = hub.items[uid]
        self._attr_native_unit_of_measurement = i.get("unit")
        self._attr_device_class = i.get("device_class") or ("enum" if i.get("options") else None)
        self._attr_state_class = i.get("state_class")
        if i.get("options"):
            self._attr_options = i["options"]

    @property
    def native_value(self):
        v = self.item.get("value")
        if isinstance(v, str) and self.device_class == "timestamp":
            return dt_util.parse_datetime(v)
        return v


class WinstonBinarySensor(WinstonEntity, BinarySensorEntity):
    def __init__(self, hub: Hub, uid: str):
        super().__init__(hub, uid)
        self._attr_device_class = hub.items[uid].get("device_class")

    @property
    def is_on(self) -> bool | None:
        v = self.item.get("value")
        return None if v is None else bool(v)


class WinstonSwitch(WinstonEntity, SwitchEntity):
    @property
    def is_on(self) -> bool | None:
        v = self.item.get("value")
        return None if v is None else bool(v)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(value=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(value=False)


class WinstonButton(WinstonEntity, ButtonEntity):
    async def async_press(self) -> None:
        await self._set(press=True)


class WinstonNumber(WinstonEntity, NumberEntity):
    def __init__(self, hub: Hub, uid: str):
        super().__init__(hub, uid)
        i = hub.items[uid]
        self._attr_native_unit_of_measurement = i.get("unit")
        for attr, k in (("_attr_native_min_value", "min"), ("_attr_native_max_value", "max"),
                        ("_attr_native_step", "step")):
            if i.get(k) is not None:
                setattr(self, attr, i[k])

    @property
    def native_value(self) -> float | None:
        return self.item.get("value")

    async def async_set_native_value(self, value: float) -> None:
        await self._set(value=value)


class WinstonSelect(WinstonEntity, SelectEntity):
    def __init__(self, hub: Hub, uid: str):
        super().__init__(hub, uid)
        self._attr_options = hub.items[uid].get("options") or []

    @property
    def current_option(self) -> str | None:
        return self.item.get("value")

    async def async_select_option(self, option: str) -> None:
        await self._set(value=option)


class WinstonText(WinstonEntity, TextEntity):
    @property
    def native_value(self) -> str | None:
        v = self.item.get("value")
        return None if v is None else str(v)

    async def async_set_value(self, value: str) -> None:
        await self._set(value=value)


class WinstonEvent(WinstonEntity, EventEntity):
    """His announcements, an extension's nudges (from the stream's events), or an extension's own event
    entity (fired when its value is set again, with the value as the event type when it is one of its
    options)."""

    def __init__(self, hub: Hub, uid: str):
        super().__init__(hub, uid)
        self._attr_event_types = hub.items[uid]["event_types"]
        self._at = hub.items[uid].get("at")          # what was set before Home Assistant saw it is not fired

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, self.hub.event_signal, self._happened))

    @callback
    def _happened(self, data: dict[str, Any]) -> None:
        kind, i = data.get("type"), self.item
        if kind == "announcement" and i.get("event_types") == ["announcement"] and not i.get("ext"):
            self._trigger_event("announcement", {"text": data.get("text")})
        elif kind == "nudge" and i.get("ext") == data.get("extension") and "key" not in i:
            self._trigger_event("nudge", {k: data.get(k) for k in ("kind", "level", "where", "text")})
        else:
            return
        self.async_write_ha_state()

    @callback
    def _redraw(self) -> None:
        i = self.item
        if "key" in i and i.get("at") and i.get("at") != self._at:
            self._at = i["at"]
            v = i.get("value")
            self._trigger_event(v if v in self.event_types else self.event_types[0],
                                {"value": v, **(i.get("attributes") or {})})
        self.async_write_ha_state()


KINDS = {"sensor": WinstonSensor, "binary_sensor": WinstonBinarySensor, "switch": WinstonSwitch,
         "button": WinstonButton, "number": WinstonNumber, "select": WinstonSelect, "text": WinstonText,
         "event": WinstonEvent}


def make(hub: Hub, uid: str) -> WinstonEntity:
    return KINDS[hub.items[uid]["platform"]](hub, uid)
