# custom_components/hoymiles_cloud/sensor.py

import logging
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import UnitOfPower, UnitOfEnergy, UnitOfElectricPotential, UnitOfElectricCurrent

from .hoymiles_client import HoymilesClient
from .device_registry import create_station_device_info, create_module_device_info
from .micro import async_setup_micro_sensors

DOMAIN = "hoymiles_nimbus"

_LOGGER = logging.getLogger(__name__)

# How often HA asks the entities for a value. The API itself is only called
# once per configured refresh interval; everything in between is served from cache.
from datetime import timedelta
SCAN_INTERVAL = timedelta(seconds=60)


async def async_setup_entry(hass, config_entry, async_add_entities):
    client = hass.data[DOMAIN][config_entry.entry_id]

    # Ensure the client is authenticated
    await hass.async_add_executor_job(client.login)

    _LOGGER.warning("Fetching device data from Hoymiles S-Cloud...")
    stations = await hass.async_add_executor_job(client.select_by_page, "station")

    _LOGGER.warning("Found %d station(s) in Hoymiles account", len(stations))

    entities = []
    
    for station in stations:
        station_name = station.get('name', 'Unknown')
        sid = station.get("id")
        device_info = create_station_device_info(sid, station_name)
        name = device_info["name"]

        entities.append(HoymilesStationPowerSensor(client, name, sid, device_info))
        entities.append(HoymilesStationEnergySensor(client, name, sid, device_info))
        entities.append(HoymilesStationTotalEnergySensor(client, name, sid, device_info))
        entities.append(HoymilesStationRatioSensor(client, name, sid, device_info))

    _LOGGER.warning("Created %d sensors for Hoymiles devices", len(entities))
    async_add_entities(entities)

    # Per-microinverter (grid voltage, frequency, temperature, AC power) and
    # per-panel (DC power, voltage, current) sensors, from the count_by_day calls
    try:
        await async_setup_micro_sensors(hass, client, config_entry, async_add_entities)
    except Exception as err:  # noqa: BLE001 - never break the station/panel sensors
        _LOGGER.error("Could not set up microinverter sensors: %s", err)

class HoymilesStationPowerSensor(SensorEntity):
    def __init__(self, client, name, sid, device_info):
        self._client = client
        self._sid = sid
        self._attr_name = f"{name} Current Power"
        self._attr_native_unit_of_measurement = UnitOfPower.WATT
        self._attr_unique_id = f"hoymiles_nimbus_{sid}_power"
        self._attr_device_class = "power"
        self._attr_device_info = device_info
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._state = None

    @property
    def native_value(self):
        return self._state

    async def async_update(self):
        # _LOGGER.debug(f"Fetching current power for station {self._sid}")
        
        # Fetch the current power data from the Hoymiles S-Cloud
        data = await self.hass.async_add_executor_job(self._client.count_station_real_data, self._sid)
        # _LOGGER.debug(f"Received power data for station {self._sid}: {data}")
        val = data.get("data", {}).get("real_power", 0)
        if val is None:
            _LOGGER.warning(f"Received None value for power data for station {self._sid}")
            self._state = 0
        else:
            # _LOGGER.debug(f" Current power for station {self._sid}: {val}")
            self._state = float(val)
        
class HoymilesStationEnergySensor(SensorEntity):
    def __init__(self, client, name, sid, device_info):
        self._client = client
        self._sid = sid
        self._attr_name = f"{name} Daily Energy"
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_unique_id = f"hoymiles_nimbus_{sid}_energy"
        self._attr_device_class = "energy"
        self._attr_state_class = "total_increasing"
        self._attr_icon = "mdi:solar-power"
        self._attr_device_info = device_info
        self._state = None

    @property
    def native_value(self):
        return self._state

    async def async_update(self):
        data = await self.hass.async_add_executor_job(self._client.count_station_real_data, self._sid)
        # _LOGGER.debug(f"Received energy data for station {self._sid}: {data}")
        val = data.get("data", {}).get("today_eq", 0)
        if val is None:
            _LOGGER.warning(f"Received None value for energy data for station {self._sid}")
            self._state = 0
        else:
            # _LOGGER.debug(f" Daily energy for station {self._sid}: {val}")
            # Convert to kWh
            # Assuming the value is in Wh, convert to kWh
            self._state = float(val) / 1000

class HoymilesStationTotalEnergySensor(SensorEntity):
    """Cumulative total energy sensor from lifetime production."""
    
    def __init__(self, client, name, sid, device_info):
        self._client = client
        self._sid = sid
        self._attr_name = f"{name} Total Energy"
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_unique_id = f"hoymiles_nimbus_{sid}_total_energy"
        self._attr_device_class = "energy"
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_icon = "mdi:solar-power"
        self._attr_device_info = device_info
        self._state = None

    @property
    def native_value(self):
        return self._state

    async def async_update(self):
        """Update the total energy from the API."""
        data = await self.hass.async_add_executor_job(self._client.count_station_real_data, self._sid)
        val = data.get("data", {}).get("total_eq", 0)
        
        if val is None:
            _LOGGER.warning(f"Received None value for total energy data for station {self._sid}")
            self._state = 0
        else:
            # Convert to kWh (assuming the value is in Wh like today_eq)
            self._state = float(val) / 1000

class HoymilesStationRatioSensor(SensorEntity):
    def __init__(self, client, name, sid, device_info):
        self._client = client
        self._sid = sid
        self._attr_name = f"{name} Performance Ratio"
        self._attr_native_unit_of_measurement = "%"
        self._attr_unique_id = f"hoymiles_nimbus_{sid}_performance_ratio"
        self._attr_device_class = "performance_ratio"
        self._attr_state_class = "measurement"
        self._attr_icon = "mdi:percent"
        self._attr_device_info = device_info
        self._state = None

    @property
    def native_value(self):
        return self._state

    async def async_update(self):
        data = await self.hass.async_add_executor_job(self._client.count_station_real_data, self._sid)
        capacity = data.get("data", {}).get("capacitor", 0)
        current_power = data.get("data", {}).get("real_power", 0)

        if capacity is None:
            _LOGGER.warning(f"Received None value for capacity data for station {self._sid}")
            self._state = 0
            return
        if current_power is None:
            _LOGGER.warning(f"Received None value for current power data for station {self._sid}")
            self._state = 0
            return
        if capacity == 0:
            self._state = 0
        else:
            # Make sure we're dealing with floats
            capacity = float(capacity)
            current_power = float(current_power)
            capacity_kw = capacity * 1000  # Convert kW to W
            ratio = (current_power / capacity_kw) * 100
            self._state = round(ratio, 2)
