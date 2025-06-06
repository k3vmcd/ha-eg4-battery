"""Coordinator for EG4 Battery BLE data."""
from datetime import timedelta
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.exceptions import HomeAssistantError
from bleak import BleakClient, BleakError
from .const import (
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
import struct

_LOGGER = logging.getLogger(__name__)

class Eg4BatteryCoordinator(DataUpdateCoordinator):
    """Coordinator to fetch EG4 battery BLE data."""
    
    def __init__(self, hass: HomeAssistant, device_address: str, device_name: str, temp_unit: str = "C", ble_name: str = None):
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
        self.data = {}
        self._notification_event = asyncio.Event()
        self._latest_data = None
    
    def set_temp_unit(self, unit: str):
        """Set the temperature unit ('C' or 'F')."""
        self.temp_unit = unit.upper()

    def notification_handler(self, sender: int, data: bytearray) -> None:
        """Handle incoming notifications."""
        _LOGGER.debug("Received notification (%d bytes): %s", len(data), data.hex())
        # For Modbus RTU responses:
        # Byte 0: Slave address (0x01)
        # Byte 1: Function code (0x03)
        # Byte 2: Number of data bytes to follow
        # Bytes 3-N: Data bytes
        # Last 2 bytes: CRC16
        if len(data) >= 3 and data[0] == 0x01 and data[1] == 0x03:
            expected_length = data[2] + 5  # Header (3) + Data (N) + CRC (2)
            if len(data) == expected_length:
                # Verify CRC
                received_crc = (data[-1] << 8) | data[-2]
                calculated_crc = self._calculate_crc16(data[:-2])
                if received_crc == calculated_crc:
                    self._latest_data = data
                    self._notification_event.set()
                else:
                    _LOGGER.error("CRC check failed")
            else:
                _LOGGER.error("Invalid response length: got %d, expected %d", 
                            len(data), expected_length)

    async def _find_characteristics(self, client):
        """Find required characteristics with retry."""
        _LOGGER.debug("Discovering services and characteristics")
        try:
            # Wait for service discovery
            await asyncio.sleep(1)
            services = client.services
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
                client = BleakClient(self.device_address, timeout=20)
                
                # Clear any previous state
                self._notification_event.clear()
                self._latest_data = None
                
                # Connect with increased timeout
                _LOGGER.debug("Connecting to device...")
                await asyncio.wait_for(client.connect(), timeout=10.0)
                await asyncio.sleep(1)  # Allow connection to stabilize
                
                if not client.is_connected:
                    raise BleakError("Failed to establish connection")
                
                # Find characteristics
                write_char, notify_char = await self._find_characteristics(client)
                if not write_char or not notify_char:
                    _LOGGER.error("Could not find characteristics - Found services: %s",
                                [s.uuid for s in client.services])
                    raise BleakError("Required characteristics not found")
                
                # Enable notifications and wait for data
                if await self._enable_notifications(client, write_char, notify_char):
                    try:
                        async with asyncio.timeout(5.0):
                            _LOGGER.debug("Waiting for notification response...")
                            await self._notification_event.wait()
                            if self._latest_data:
                                self.data = parse_battery_data(
                                    self._latest_data,
                                    self.device_name,
                                    self.device_address,
                                    self.temp_unit
                                )
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
        
        raise HomeAssistantError(f"Failed after 3 attempts: {last_error}")

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