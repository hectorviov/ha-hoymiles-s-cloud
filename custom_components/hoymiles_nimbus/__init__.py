
"""The Hoymiles S-Cloud integration."""
from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform

from .hoymiles_client import HoymilesClient

DOMAIN = "hoymiles_nimbus"
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL_MIN = 5
MIN_SCAN_INTERVAL_MIN = 5
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.NUMBER, Platform.BINARY_SENSOR]

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Hoymiles S-Cloud from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    client = HoymilesClient(
        username=entry.data["username"],
        password=entry.data["password"],
        base_url=entry.data.get("base_url", "https://neapi.hoymiles.com/"),
    )

    minutes = entry.options.get(CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN))
    client.scan_interval_s = max(MIN_SCAN_INTERVAL_MIN, int(minutes)) * 60

    hass.data[DOMAIN][entry.entry_id] = client
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    _LOGGER.debug("Hoymiles Nimbus set up, refresh every %s s", client.scan_interval_s)
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True

async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the options (e.g. refresh interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    
    return unload_ok

