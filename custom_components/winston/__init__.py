"""Winston: his brain as a conversation agent, his state and his extensions' entities, from the listener
winstond opens for Home Assistant (src/winston/api/ha_link.py in the Winston repository). See
docs/ha-integration.md there."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .hub import DOMAIN, Hub

PLATFORMS = [Platform.CONVERSATION, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT, Platform.SWITCH,
             Platform.BUTTON, Platform.NUMBER, Platform.SELECT, Platform.TEXT]

type WinstonConfigEntry = ConfigEntry[Hub]


async def async_setup_entry(hass: HomeAssistant, entry: WinstonConfigEntry) -> bool:
    entry.runtime_data = hub = Hub(hass, entry)
    # His device before the first snapshot names it fully: the conversation entity hangs off it from the start.
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, hub.instance)},
                                           name=entry.title, manufacturer="Winston", model="Voice assistant")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(hass, hub.run(), "winston stream")     # cancelled on unload
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WinstonConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
