"""EG4 Battery BLE integration for Home Assistant."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .coordinator import Eg4BatteryCoordinator
from .const import DOMAIN

import logging

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EG4 Battery from a config entry."""
    device_address = entry.data["device_address"]
    device_name = entry.data.get("device_name", device_address)  # User's preferred name
    ble_name = entry.data.get("ble_name")  # Actual BLE name
    temp_unit = entry.data.get("temp_unit", "C")
    
    coordinator = Eg4BatteryCoordinator(hass, device_address, device_name, temp_unit, ble_name)
    await coordinator.async_config_entry_first_refresh()
    
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    hass.data[DOMAIN].pop(entry.entry_id)
    return True