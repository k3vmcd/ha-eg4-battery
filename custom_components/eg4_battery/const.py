"""Constants for the EG4 Battery integration."""
DOMAIN = "eg4_battery"
CONF_DEVICE_ADDRESS = "device_address"
CONF_DEVICE_NAME = "device_name"
SERVICE_UUID = "00001000-0000-1000-8000-00805f9b34fb"
WRITE_CHARACTERISTIC_UUID = "00001001-0000-1000-8000-00805f9b34fb"
NOTIFY_CHARACTERISTIC_UUID = "00001002-0000-1000-8000-00805f9b34fb"

# Modbus register mappings
REGISTER_TOTAL_VOLTAGE = 0    # Total voltage (÷100)
REGISTER_CURRENT = 1          # Current (÷100, signed)
REGISTER_CELL_VOLTAGE = 2     # Cell voltages start (÷1000)
REGISTER_TEMPERATURE = 19     # Temperature values start (raw °C)
REGISTER_STATUS = 24         # Status bits
REGISTER_SOC = 25           # State of charge (%)

# Modbus register/byte offsets (from wombat-main)
TEMP_OFFSET = 42  # Fixed offset in response data
TEMP_PCB = TEMP_OFFSET  # PCB temp at base offset
TEMP_CELL1 = TEMP_OFFSET + 2  # Cell 1 temp 2 bytes later
TEMP_CELL2 = TEMP_OFFSET + 4  # Cell 2 temp 4 bytes later

# Status bit masks
STATUS_CHARGING = 0x0001      # Charging bit
STATUS_DISCHARGING = 0x0002   # Discharging bit
STATUS_BALANCING = 0x0004     # Balancing bit
STATUS_PROTECTION = 0xFFF8    # All protection bits