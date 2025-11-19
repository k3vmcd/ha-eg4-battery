"""Config flow for EG4 Battery integration."""
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfo
import voluptuous as vol
import logging
import asyncio

_LOGGER = logging.getLogger(__name__)
from .const import (
    DOMAIN,
    CONF_DEVICE_ADDRESS,
    CONF_DEVICE_NAME,
    CONF_BATTERY_CAPACITY_KWH,
    DEFAULT_BATTERY_CAPACITY_KWH,
    MIN_BATTERY_CAPACITY_KWH,
    MAX_BATTERY_CAPACITY_KWH,
    TEMP_UNIT_KEY,
    TEMP_UNIT_OPTIONS,
    SERVICE_UUID,
)

class Eg4BatteryConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EG4 Battery."""
    
    VERSION = 1
    
    def __init__(self):
        """Initialize the config flow."""
        self._discovered_devices = {}
    
    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        
        if user_input is not None:
            device_address = user_input[CONF_DEVICE_ADDRESS]
            # Use user's preferred name if provided, otherwise use discovered name
            ble_name = self._discovered_devices[device_address]["name"]
            user_name = user_input.get(CONF_DEVICE_NAME, ble_name)
            temp_unit = user_input.get(TEMP_UNIT_KEY, "C")
            battery_capacity = user_input.get(
                CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH
            )
            
            if not bluetooth.async_ble_device_from_address(self.hass, device_address):
                errors["base"] = "device_not_found"
            else:
                return self.async_create_entry(
                    title=user_name,  # Use user's preferred name as title
                    data={
                        CONF_DEVICE_ADDRESS: device_address,
                        CONF_DEVICE_NAME: user_name,  # User's preferred name
                        "ble_name": ble_name,  # Store actual BLE name separately
                        TEMP_UNIT_KEY: temp_unit,
                        CONF_BATTERY_CAPACITY_KWH: battery_capacity,
                    },
                )
        
        # Register a callback to discover devices
        self._discovered_devices = {}
        
        def discovery_callback(service_info: BluetoothServiceInfo, change: bluetooth.BluetoothChange):
            """Handle discovered devices."""
            if SERVICE_UUID in service_info.service_uuids or (
                service_info.name and (
                    "EG4" in service_info.name or
                    "Battery" in service_info.name
                )
            ):
                self._discovered_devices[service_info.address] = {
                    "name": service_info.name or "No name",
                    "address": service_info.address,
                }

        try:
            # Register callback and wait for devices
            cancel_callback = bluetooth.async_register_callback(
                self.hass,
                discovery_callback,
                {"domain": DOMAIN},
                bluetooth.BluetoothScanningMode.ACTIVE,
            )
            await asyncio.sleep(10)  # Wait for advertisements
            cancel_callback()
            
            device_options = {
                addr: f"{info['name']} ({addr})"
                for addr, info in self._discovered_devices.items()
            }
        except Exception as err:
            _LOGGER.error("BLE discovery failed: %s", err)
            errors["base"] = "ble_discovery_failed"
            device_options = {}
        
        if not device_options:
            errors["base"] = "no_eg4_devices_found"
        
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ADDRESS): vol.In(device_options),
                    vol.Optional(CONF_DEVICE_NAME): str,
                    vol.Optional(TEMP_UNIT_KEY, default="C"): vol.In(TEMP_UNIT_OPTIONS),
                    vol.Optional(
                        CONF_BATTERY_CAPACITY_KWH,
                        default=DEFAULT_BATTERY_CAPACITY_KWH,
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(
                            min=MIN_BATTERY_CAPACITY_KWH,
                            max=MAX_BATTERY_CAPACITY_KWH,
                        ),
                    ),
                }
            ),
            errors=errors,
        )
    
    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for EG4 Battery."""
    
    def __init__(self, entry: config_entries.ConfigEntry):
        """Initialize options flow."""
        self._entry = entry
    
    async def async_step_init(self, user_input=None):
        """Manage device options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        defaults = {
            CONF_DEVICE_NAME: self._entry.options.get(
                CONF_DEVICE_NAME,
                self._entry.data.get(CONF_DEVICE_NAME, ""),
            ),
            TEMP_UNIT_KEY: self._entry.options.get(
                TEMP_UNIT_KEY,
                self._entry.data.get(TEMP_UNIT_KEY, "C"),
            ),
            CONF_BATTERY_CAPACITY_KWH: self._entry.options.get(
                CONF_BATTERY_CAPACITY_KWH,
                self._entry.data.get(
                    CONF_BATTERY_CAPACITY_KWH,
                    DEFAULT_BATTERY_CAPACITY_KWH,
                ),
            ),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DEVICE_NAME,
                        default=defaults[CONF_DEVICE_NAME],
                    ): str,
                    vol.Optional(
                        TEMP_UNIT_KEY,
                        default=defaults[TEMP_UNIT_KEY],
                    ): vol.In(TEMP_UNIT_OPTIONS),
                    vol.Optional(
                        CONF_BATTERY_CAPACITY_KWH,
                        default=defaults[CONF_BATTERY_CAPACITY_KWH],
                    ): vol.All(
                        vol.Coerce(float),
                        vol.Range(
                            min=MIN_BATTERY_CAPACITY_KWH,
                            max=MAX_BATTERY_CAPACITY_KWH,
                        ),
                    ),
                }
            ),
        )