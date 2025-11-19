"""Coordinator for EG4 Battery BLE data."""
from datetime import datetime, timedelta
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from bleak import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from .const import (
    DEFAULT_BATTERY_CAPACITY_KWH,
    DOMAIN,
    SERVICE_UUID,
    WRITE_CHARACTERISTIC_UUID,
    NOTIFY_CHARACTERISTIC_UUID,
    REGISTER_TOTAL_VOLTAGE,
    REGISTER_CURRENT,
    REGISTER_CELL_VOLTAGE,
    REGISTER_TEMPERATURE,
    REGISTER_STATUS,
    REGISTER_SOC,
    STATUS_CHARGING,
    STATUS_DISCHARGING,
    STATUS_PROTECTION,
    TEMP_OFFSET,
    TEMP_PCB,
    TEMP_CELL1,
    TEMP_CELL2,
)
import logging
import asyncio
from typing import Any

_LOGGER = logging.getLogger(__name__)

class Eg4BatteryCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch EG4 battery BLE data."""
    
    def __init__(
        self,
        hass: HomeAssistant,
        device_address: str,
        device_name: str,
        temp_unit: str = "C",
        ble_name: str | None = None,
        battery_capacity_kwh: float | None = None,
    ):
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"EG4 Battery {device_name}",
            update_interval=timedelta(seconds=15),
        )
        self.device_address = device_address
        self.device_name = device_name  # User's preferred name
        self.ble_name = ble_name  # Actual BLE name
        self.temp_unit = temp_unit.upper()  # "C" or "F"
        try:
            capacity = float(battery_capacity_kwh)
        except (TypeError, ValueError):
            capacity = DEFAULT_BATTERY_CAPACITY_KWH
        self.battery_capacity_kwh = max(capacity, 0.1)
        self.data = {}
        self._notification_event = asyncio.Event()
        self._latest_data = None
        self._response_buffer = bytearray()
        self._expected_response_length = None
        self._energy_store: Store | None = None
        self._energy_stats: dict[str, Any] = {
            "stored_energy_kwh": None,
            "charged_total_kwh": 0.0,
            "discharged_total_kwh": 0.0,
            "last_ts": None,
        }
        self._last_energy_persist: datetime | None = None
    
    def set_temp_unit(self, unit: str):
        """Set the temperature unit ('C' or 'F')."""
        self.temp_unit = unit.upper()

    async def async_initialize_storage(self) -> None:
        """Prepare persistent storage for energy statistics."""
        if self._energy_store is not None:
            return
        safe_address = self.device_address.replace(":", "").lower()
        self._energy_store = Store(
            self.hass,
            1,
            f"{DOMAIN}_{safe_address}_energy",
        )
        stored = await self._energy_store.async_load()
        if stored:
            self._energy_stats.update(stored)
        for key, default in (
            ("stored_energy_kwh", None),
            ("charged_total_kwh", 0.0),
            ("discharged_total_kwh", 0.0),
            ("last_ts", None),
        ):
            self._energy_stats.setdefault(key, default)
        self._last_energy_persist = None

    def notification_handler(self, sender: int, data: bytearray) -> None:
        """Handle incoming BLE notifications and assemble complete Modbus frames."""
        if not data:
            return

        _LOGGER.debug("Received notification chunk (%d bytes): %s", len(data), data.hex())
        self._response_buffer.extend(data)
        self._process_response_buffer()

    def _process_response_buffer(self) -> None:
        """Process buffered notification data until a complete frame is available."""
        while True:
            if self._expected_response_length is None:
                if len(self._response_buffer) < 3:
                    return
                if self._response_buffer[0] != 0x01 or self._response_buffer[1] != 0x03:
                    _LOGGER.warning(
                        "Dropping notification due to unexpected header: %s",
                        self._response_buffer.hex(),
                    )
                    self._response_buffer.clear()
                    return
                self._expected_response_length = self._response_buffer[2] + 5
                _LOGGER.debug(
                    "Expecting %d-byte response from notification stream",
                    self._expected_response_length,
                )

            if len(self._response_buffer) < self._expected_response_length:
                return

            frame = bytes(self._response_buffer[: self._expected_response_length])
            self._response_buffer = self._response_buffer[self._expected_response_length :]
            self._expected_response_length = None
            self._handle_full_frame(frame)
            if not self._response_buffer:
                return

    def _handle_full_frame(self, frame: bytes) -> None:
        """Validate and publish a complete Modbus frame."""
        # Verify CRC
        if len(frame) < 5:
            _LOGGER.warning("Discarding short frame (%d bytes)", len(frame))
            return

        received_crc = (frame[-1] << 8) | frame[-2]
        calculated_crc = self._calculate_crc16(frame[:-2])
        if received_crc != calculated_crc:
            _LOGGER.warning("CRC check failed for frame: %s", frame.hex())
            return

        self._latest_data = frame
        self._notification_event.set()

    async def _async_fetch_services(self, client):
        """Return GATT services for the connected client with HA compatibility."""
        if hasattr(client, "get_services"):
            try:
                services = await client.get_services()
                if services is not None:
                    return services
            except AttributeError:
                # Older HA clients may not implement get_services
                pass
        services = getattr(client, "services", None)
        if services is None:
            # Give bleak a brief moment to populate services cache
            await asyncio.sleep(0.5)
            services = getattr(client, "services", None)
        return services or []

    async def _find_characteristics(self, client):
        """Find required characteristics with retry."""
        _LOGGER.debug("Discovering services and characteristics")
        write_char = None
        notify_char = None
        try:
            services = await self._async_fetch_services(client)
            for service in services:
                _LOGGER.debug("Found service: %s", service.uuid)
                if service.uuid.lower() == SERVICE_UUID.lower():
                    for char in service.characteristics:
                        _LOGGER.debug("Found characteristic: %s [%s]", 
                                    char.uuid, char.properties)
                        char_uuid = char.uuid.lower()
                        if char_uuid == WRITE_CHARACTERISTIC_UUID.lower():
                            write_char = char
                        elif char_uuid == NOTIFY_CHARACTERISTIC_UUID.lower():
                            notify_char = char
                    if write_char and notify_char:
                        return write_char, notify_char
            
            _LOGGER.error("Required characteristics not found in service %s", SERVICE_UUID)
        except Exception as err:
            _LOGGER.error("Error discovering characteristics: %s", err)
        return None, None

    async def _enable_notifications(self, client, write_char, notify_char):
        """Enable notifications following manufacturer app sequence."""
        try:
            _LOGGER.debug("Enabling notifications for %s", notify_char.uuid)
            await client.start_notify(notify_char, self.notification_handler)
            await asyncio.sleep(0.5)
            
            # Request 40 registers (0x28) instead of 12 to get all data
            command = bytearray([0x01, 0x03, 0x00, 0x00, 0x00, 0x28])
            crc = self._calculate_crc16(command)
            command.extend([crc & 0xFF, (crc >> 8) & 0xFF])
            
            _LOGGER.debug("Sending Modbus RTU command: %s", command.hex())
            await client.write_gatt_char(write_char, command)
            return True
        except Exception as err:
            _LOGGER.error("Failed to setup notifications: %s", err)
            return False

    async def _async_update_energy_statistics(self, data: dict[str, Any]) -> None:
        """Update derived energy metrics for Energy Dashboard support."""
        if self._energy_store is None:
            await self.async_initialize_storage()

        now = dt_util.utcnow()
        force_persist = False

        soc = data.get("battery_pct")
        if soc is not None:
            stored_energy = round((soc / 100.0) * self.battery_capacity_kwh, 3)
            data["stored_energy_kwh"] = stored_energy
            self._energy_stats["stored_energy_kwh"] = stored_energy
        else:
            data["stored_energy_kwh"] = None

        voltage = data.get("total_voltage")
        current = data.get("current")
        if voltage is None or current is None:
            data["charge_power_kw"] = None
            data["discharge_power_kw"] = None
            data["charge_energy_total_kwh"] = round(
                self._energy_stats.get("charged_total_kwh", 0.0), 3
            )
            data["discharge_energy_total_kwh"] = round(
                self._energy_stats.get("discharged_total_kwh", 0.0), 3
            )
            self._energy_stats["last_ts"] = now.isoformat()
            await self._async_maybe_persist_energy(now, False)
            return

        power_kw = (voltage * current) / 1000.0
        data["charge_power_kw"] = round(max(power_kw, 0.0), 3)
        data["discharge_power_kw"] = round(max(-power_kw, 0.0), 3)

        last_ts = self._energy_stats.get("last_ts")
        last_dt = dt_util.parse_datetime(last_ts) if last_ts else None
        delta_hours = 0.0
        if last_dt is not None:
            delta = (now - last_dt).total_seconds() / 3600.0
            if delta > 0:
                delta_hours = delta

        energy_delta = 0.0
        if delta_hours > 0 and abs(power_kw) > 0.0001:
            energy_delta = power_kw * delta_hours
            if energy_delta > 0:
                self._energy_stats["charged_total_kwh"] += energy_delta
            else:
                self._energy_stats["discharged_total_kwh"] += abs(energy_delta)
            if abs(energy_delta) >= 0.03:
                force_persist = True

        data["charge_energy_total_kwh"] = round(
            self._energy_stats.get("charged_total_kwh", 0.0), 3
        )
        data["discharge_energy_total_kwh"] = round(
            self._energy_stats.get("discharged_total_kwh", 0.0), 3
        )
        self._energy_stats["last_ts"] = now.isoformat()
        await self._async_maybe_persist_energy(now, force_persist)

    async def _async_maybe_persist_energy(self, now: datetime, force: bool) -> None:
        """Persist energy stats periodically to survive restarts."""
        if self._energy_store is None:
            return
        if not force and self._last_energy_persist is not None:
            delta = (now - self._last_energy_persist).total_seconds()
            if delta < 60:
                return
        try:
            await self._energy_store.async_save(self._energy_stats)
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.warning("Failed to persist energy statistics: %s", err, exc_info=True)
            return
        self._last_energy_persist = now

    def _calculate_crc16(self, data: bytes) -> int:
        """Calculate Modbus RTU CRC16."""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

    async def _async_update_data(self):
        """Fetch data from the EG4 battery via BLE."""
        client = None
        last_error = None
        
        for attempt in range(3):
            try:
                _LOGGER.debug("Connection attempt %d/3", attempt + 1)

                ble_device = bluetooth.async_ble_device_from_address(
                    self.hass,
                    self.device_address,
                    connectable=True,
                )
                if not ble_device:
                    last_error = "Bluetooth device not available"
                    _LOGGER.debug(
                        "BLE device %s not currently available; retrying",
                        self.device_address,
                    )
                    await asyncio.sleep((attempt + 1) * 2)
                    continue

                client = await establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    ble_device.name or self.device_name or self.device_address,
                    max_attempts=3,
                )

                if not client.is_connected:
                    raise BleakError("Failed to establish connection")

                # Clear any previous state
                self._notification_event.clear()
                self._latest_data = None
                self._response_buffer.clear()
                self._expected_response_length = None

                # Allow connection to stabilize
                await asyncio.sleep(1)
                
                # Find characteristics
                write_char, notify_char = await self._find_characteristics(client)
                if not write_char or not notify_char:
                    services = await self._async_fetch_services(client)
                    _LOGGER.error("Could not find characteristics - Found services: %s",
                                [s.uuid for s in services])
                    raise BleakError("Required characteristics not found")
                
                # Enable notifications and wait for data
                if await self._enable_notifications(client, write_char, notify_char):
                    try:
                        async with asyncio.timeout(5.0):
                            _LOGGER.debug("Waiting for notification response...")
                            await self._notification_event.wait()
                            if self._latest_data:
                                parsed = parse_battery_data(
                                    self._latest_data,
                                    self.device_name,
                                    self.device_address,
                                    self.temp_unit
                                )
                                try:
                                    await self._async_update_energy_statistics(parsed)
                                except Exception as err:  # pragma: no cover - defensive
                                    _LOGGER.warning(
                                        "Energy statistics update failed: %s", err,
                                        exc_info=True,
                                    )
                                self.data = parsed
                                return self.data
                            _LOGGER.error("No data received after notification")
                    except asyncio.TimeoutError:
                        _LOGGER.debug("Notification timeout, attempting retry")
                        last_error = "Notification timeout"
                        continue
                
            except Exception as err:
                _LOGGER.warning("Error on attempt %d: %s", attempt + 1, err)
                last_error = str(err)
                await asyncio.sleep((attempt + 1) * 2)
                
            finally:
                if client:
                    try:
                        if client.is_connected:
                            await client.disconnect()
                    except Exception as err:
                        _LOGGER.warning("Error disconnecting: %s", err)
                    client = None
        
        # If we have cached data, log the error but return cached values to keep entities available
        # This is critical for BLE devices which frequently have connection issues
        if self.data:
            _LOGGER.warning(
                "Failed to update after 3 attempts: %s. Using cached data to keep entities available.",
                last_error
            )
            # Update energy statistics even with stale data to maintain totals
            try:
                await self._async_update_energy_statistics(self.data)
            except Exception as err:
                _LOGGER.debug("Could not update energy stats with cached data: %s", err)
            return self.data
        
        # Only raise UpdateFailed if we have no data at all (first connection)
        raise UpdateFailed(f"Failed after 3 attempts: {last_error}")

def parse_battery_data(response: bytes, device_name: str = None, device_address: str = None, temp_unit: str = "C") -> dict:
    """Parse raw BLE data into a dictionary."""
    _LOGGER.debug("Parsing data packet: %s", response.hex())
    if len(response) < 5:
        _LOGGER.error("Invalid response length: %d bytes", len(response))
        return {}

    data = {}
    try:
        if len(response) >= 85:
            # Add device identification
            data["device_info"] = {
                "identifiers": {("eg4_battery", device_address)},
                "name": f"EG4 Battery {device_name}",
                "manufacturer": "EG4",
                "model": "LiFePO4 Battery",
                "via_device": ("eg4_battery", device_address),
                "connections": {("bluetooth", device_address)},
                "sw_version": None,
                "hw_version": None,
                "mac_address": device_address,
                "ble_name": device_name,
            }
            
            # Parse registers (skip 3-byte header)
            registers = []
            for i in range(0, 80, 2):
                registers.append((response[i+3] << 8) | response[i+4])
            
            # Total voltage (÷100)
            data["total_voltage"] = registers[REGISTER_TOTAL_VOLTAGE] / 100.0
            
            # Current (÷10, signed)
            current = registers[REGISTER_CURRENT]
            if current & 0x8000:  # Check sign bit
                current = -((~current + 1) & 0xFFFF)  # Two's complement conversion
            data["current"] = current / 10.0
            
            # Cell voltages (÷1000)
            for i in range(4):
                data[f"cell_{i+1}_voltage"] = registers[REGISTER_CELL_VOLTAGE + i] / 1000.0
            
            # Calculate min/max/diff
            cell_voltages = [data[f"cell_{i+1}_voltage"] for i in range(4)]
            data["cell_voltage_min"] = min(cell_voltages)
            data["cell_voltage_max"] = max(cell_voltages)
            data["cell_voltage_diff"] = round(data["cell_voltage_max"] - data["cell_voltage_min"], 3)
            
            # Temperature parsing from registers, ignore out-of-range values
            def safe_temp(val):
                if val is None or val > 200 or val < -40:
                    return None
                return val
            pcb_temp_c = safe_temp(registers[REGISTER_TEMPERATURE])
            cell_temp_1_c = safe_temp(registers[REGISTER_TEMPERATURE + 1])
            cell_temp_2_c = safe_temp(registers[REGISTER_TEMPERATURE + 2])
            if temp_unit.upper() == "F":
                data["pcb_temp"] = round(pcb_temp_c * 9/5 + 32, 1) if pcb_temp_c is not None else None
                data["cell_temp_1"] = round(cell_temp_1_c * 9/5 + 32, 1) if cell_temp_1_c is not None else None
                data["cell_temp_2"] = round(cell_temp_2_c * 9/5 + 32, 1) if cell_temp_2_c is not None else None
                data["temp_unit"] = "°F"
            else:
                data["pcb_temp"] = round(pcb_temp_c, 1) if pcb_temp_c is not None else None
                data["cell_temp_1"] = round(cell_temp_1_c, 1) if cell_temp_1_c is not None else None
                data["cell_temp_2"] = round(cell_temp_2_c, 1) if cell_temp_2_c is not None else None
                data["temp_unit"] = "°C"

            # Battery percentage (SoC) from marker 0x0898
            soc = None
            for i in range(len(response) - 6):
                if response[i] == 0x08 and response[i+1] == 0x98:
                    soc = (response[i+4] << 8) | response[i+5]  # Use the second value after marker
                    break
            if soc is not None:
                if soc > 100:
                    soc = 100
                data["battery_pct"] = soc
            else:
                data["battery_pct"] = None
            
            # State detection
            status = registers[REGISTER_STATUS]
            protection_reasons = []
            is_charging = bool(status & STATUS_CHARGING)
            is_discharging = bool(status & STATUS_DISCHARGING)
            # Only check protection if not charging/discharging
            if not (is_charging or is_discharging):
                if status & 0x0008:
                    protection_reasons.append("COV")
                if status & 0x0010:
                    protection_reasons.append("CUV")
                if status & 0x0020:
                    protection_reasons.append("POV")
                if status & 0x0040:
                    protection_reasons.append("PUV")
                if status & 0x0080:
                    protection_reasons.append("CHG_OT")
                if status & 0x0100:
                    protection_reasons.append("CHG_UT")
                if status & 0x0200:
                    protection_reasons.append("DSG_OT")
                if status & 0x0400:
                    protection_reasons.append("DSG_UT")
                if status & 0x0800:
                    protection_reasons.append("CHG_OC")
                if status & 0x1000:
                    protection_reasons.append("DSG_OC")
                if status & 0x2000:
                    protection_reasons.append("SCD")
                if status & 0x4000:
                    protection_reasons.append("AFE")
            # If SoC is 100 and any protect bits, treat as protect
            if soc == 100 and protection_reasons:
                data["state"] = "protect"
                data["protect_reason"] = ",".join(protection_reasons)
            elif is_charging or data["current"] > 0.1:
                data["state"] = "charging"
            elif is_discharging or data["current"] < -0.1:
                data["state"] = "discharging"
            elif protection_reasons:
                data["state"] = "protect"
                data["protect_reason"] = ",".join(protection_reasons)
            else:
                data["state"] = "idle"
            
            _LOGGER.debug(f"Parsed values: voltage={data['total_voltage']}V, current={data['current']}A, soc={soc}, state={data['state']}, pcb_temp={data['pcb_temp']}, cell_temp_1={data['cell_temp_1']}, cell_temp_2={data['cell_temp_2']}")
            return data
            
    except Exception as err:
        _LOGGER.error("Error parsing battery data: %s", err)
    return {}