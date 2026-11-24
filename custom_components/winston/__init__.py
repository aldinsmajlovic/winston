"""Winston: his state and his extensions' entities, from the listener winstond opens for Home Assistant
(src/winston/api/ha_link.py in the Winston repository). See docs/ha-integration.md there."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .hub import Hub

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT, Platform.SWITCH, Platform.BUTTON,
             Platform.NUMBER, Platform.SELECT, Platform.TEXT]

type WinstonConfigEntry = ConfigEntry[Hub]


async def async_setup_entry(hass: HomeAssistant, entry: WinstonConfigEntry) -> bool:
    entry.runtime_data = hub = Hub(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(hass, hub.run(), "winston stream")     # cancelled on unload
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WinstonConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
