"""Support for EG4 Battery BLE sensors."""
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfTemperature,
    UnitOfElectricPotential,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import Eg4BatteryCoordinator

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EG4 battery sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    sensors = [
        Eg4BatterySensor(coordinator, "total_voltage", "Total Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
        Eg4BatterySensor(coordinator, "current", "Current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT),
        Eg4BatterySensor(coordinator, "battery_pct", "Battery", PERCENTAGE, SensorDeviceClass.BATTERY),
        Eg4BatterySensor(coordinator, "state", "State", None, None),
        Eg4BatterySensor(coordinator, "pcb_temp", "PCB Temperature", None, SensorDeviceClass.TEMPERATURE),
        Eg4BatterySensor(coordinator, "cell_temp_1", "Cell Temperature 1", None, SensorDeviceClass.TEMPERATURE),
        Eg4BatterySensor(coordinator, "cell_temp_2", "Cell Temperature 2", None, SensorDeviceClass.TEMPERATURE),
        Eg4BatterySensor(
            coordinator,
            "stored_energy_kwh",
            "Stored Energy",
            UnitOfEnergy.KILO_WATT_HOUR,
            SensorDeviceClass.ENERGY,
            SensorStateClass.MEASUREMENT,
            None,
            keep_available_when_stale=True,
        ),
        Eg4BatterySensor(
            coordinator,
            "charge_energy_total_kwh",
            "Energy Charged",
            UnitOfEnergy.KILO_WATT_HOUR,
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
            keep_available_when_stale=True,
        ),
        Eg4BatterySensor(
            coordinator,
            "discharge_energy_total_kwh",
            "Energy Discharged",
            UnitOfEnergy.KILO_WATT_HOUR,
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
            keep_available_when_stale=True,
        ),
        Eg4BatterySensor(
            coordinator,
            "charge_power_kw",
            "Charge Power",
            UnitOfPower.KILO_WATT,
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
            keep_available_when_stale=True,
        ),
        Eg4BatterySensor(
            coordinator,
            "discharge_power_kw",
            "Discharge Power",
            UnitOfPower.KILO_WATT,
            SensorDeviceClass.POWER,
            SensorStateClass.MEASUREMENT,
            keep_available_when_stale=True,
        ),
    ]

    # Add cell voltage sensors
    for i in range(4):
        sensors.append(
            Eg4BatterySensor(
                coordinator,
                f"cell_{i+1}_voltage",
                f"Cell {i+1} Voltage",
                UnitOfElectricPotential.VOLT,
                SensorDeviceClass.VOLTAGE,
                SensorStateClass.MEASUREMENT,
            )
        )

    # Add min/max/diff voltage sensors
    sensors.extend([
        Eg4BatterySensor(coordinator, "cell_voltage_min", "Cell Voltage Min", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        Eg4BatterySensor(coordinator, "cell_voltage_max", "Cell Voltage Max", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
        Eg4BatterySensor(coordinator, "cell_voltage_diff", "Cell Voltage Difference", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT),
    ])

    async_add_entities(sensors)

class Eg4BatterySensor(CoordinatorEntity, SensorEntity):
    """Representation of an EG4 Battery sensor."""

    def __init__(
        self,
        coordinator: Eg4BatteryCoordinator,
        key: str,
        name: str,
        unit: str | None,
        device_class: str | None,
        state_class: SensorStateClass | None = None,
        keep_available_when_stale: bool = False,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_unique_id = f"{coordinator.device_address}_{key}"
        self._attr_state_class = state_class
        self._keep_available_when_stale = keep_available_when_stale
        if (
            self._attr_state_class is None
            and device_class is not None
            and device_class != SensorDeviceClass.ENERGY
        ):
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this sensor."""
        mac = getattr(self.coordinator, "device_address", None)
        user_name = getattr(self.coordinator, "device_name", None)
        ble_name = getattr(self.coordinator, "ble_name", None)
        return DeviceInfo(
            identifiers={(DOMAIN, mac)},
            name=user_name,  # Use user's preferred name
            manufacturer="EG4",
            model=f"LiFePO4 Battery, BLE Name: {ble_name}",
            connections={("bluetooth", mac)},
        )

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement."""
        if self._attr_device_class == SensorDeviceClass.TEMPERATURE:
            return self.coordinator.data.get("temp_unit", "C")
        return self._attr_native_unit_of_measurement

    @property
    def native_value(self):
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._key)

    @property
    def available(self) -> bool:
        """Return availability based on coordinator data.
        
        Since the coordinator returns cached data on connection failures
        (instead of raising UpdateFailed), standard CoordinatorEntity
        availability logic works correctly. We only need special handling
        to keep energy sensors available when their value is temporarily None.
        """
        # Standard availability check - works because coordinator doesn't fail when it has cached data
        if not super().available:
            return False
            
        # Additional check: energy sensors stay available even if their current value is None
        # This prevents dashboard gaps when values are temporarily missing from updates
        if self._keep_available_when_stale and self.coordinator.data is not None:
            return True
            
        # For other sensors, require a non-None value
        return self.coordinator.data is not None and self.coordinator.data.get(self._key) is not None