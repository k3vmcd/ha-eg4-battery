"""EG4 Battery BLE integration for Home Assistant."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .coordinator import Eg4BatteryCoordinator
from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_DEVICE_ADDRESS,
    CONF_DEVICE_NAME,
    DEFAULT_BATTERY_CAPACITY_KWH,
    DOMAIN,
    TEMP_UNIT_KEY,
)

import logging

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EG4 Battery from a config entry."""
    device_address = entry.data[CONF_DEVICE_ADDRESS]
    device_name = entry.options.get(
        CONF_DEVICE_NAME,
        entry.data.get(CONF_DEVICE_NAME, device_address),
    )
    ble_name = entry.data.get("ble_name")  # Actual BLE name
    temp_unit = entry.options.get(
        TEMP_UNIT_KEY,
        entry.data.get(TEMP_UNIT_KEY, "C"),
    )
    battery_capacity = entry.options.get(
        CONF_BATTERY_CAPACITY_KWH,
        entry.data.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH),
    )

    coordinator = Eg4BatteryCoordinator(
        hass,
        device_address,
        device_name,
        temp_unit,
        ble_name,
        battery_capacity,
    )
    await coordinator.async_initialize_storage()
    await coordinator.async_config_entry_first_refresh()
    
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms(entry, ["sensor"])
    hass.data[DOMAIN].pop(entry.entry_id)
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the config entry."""
    await hass.config_entries.async_reload(entry.entry_id)