"""Per-microinverter sensors: grid voltage, grid frequency, temperature, AC power."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricPotential,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from homeassistant.const import UnitOfElectricCurrent

from .device_registry import create_module_device_info
from .micro_data import day_stats, latest_module_values, latest_values

_LOGGER = logging.getLogger(__name__)

# The DTU uploads roughly every 5-15 minutes, so polling faster gains nothing.
MICRO_UPDATE_INTERVAL = timedelta(minutes=5)


@dataclass
class MicroInfo:
    station_id: int
    station_name: str
    micro_id: int
    sn: str
    model: str | None
    ports: list = None  # [{"port": 1, "x": 0, "y": 4}, ...]


class HoymilesMicroCoordinator(DataUpdateCoordinator):
    """Fetches the day series for every microinverter of every station."""

    def __init__(self, hass, client, config_entry=None):
        seconds = getattr(client, "scan_interval_s", None)
        interval = timedelta(seconds=seconds) if isinstance(seconds, (int, float)) else MICRO_UPDATE_INTERVAL
        common = dict(name="Hoymiles Nimbus microinverters", update_interval=interval)
        try:
            super().__init__(hass, _LOGGER, config_entry=config_entry, **common)
        except TypeError:  # Home Assistant < 2024.8 has no config_entry argument
            super().__init__(hass, _LOGGER, **common)
        self._client = client
        self.micros: list[MicroInfo] = []

    def _discover(self) -> list[MicroInfo]:
        micros: list[MicroInfo] = []
        for station in self._client.select_by_page("station") or []:
            sid = station.get("id")
            data = self._client.select_by_station(sid) or {}
            for m in data.get("list", []):
                mid = m.get("id")
                try:
                    layout = (self._client.micro_find(mid, sid) or {}).get("layout_list") or []
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Could not read panel layout for %s: %s", m.get("sn"), err)
                    layout = []
                ports = sorted(
                    ({"port": p.get("port"), "x": p.get("x"), "y": p.get("y")} for p in layout if p.get("port")),
                    key=lambda p: p["port"],
                )
                micros.append(
                    MicroInfo(
                        station_id=sid,
                        station_name=station.get("name", "Unknown"),
                        micro_id=mid,
                        sn=m.get("sn"),
                        model=m.get("model_no") or m.get("model") or m.get("init_hard_no"),
                        ports=ports,
                    )
                )
        return micros

    def _fetch(self) -> dict[int, dict[str, Any]]:
        if not self.micros:
            self.micros = self._discover()

        now = dt_util.now()
        date = now.strftime("%Y-%m-%d")
        by_station: dict[int, list[int]] = {}
        for m in self.micros:
            by_station.setdefault(m.station_id, []).append(m.micro_id)

        result: dict[int, dict[str, Any]] = {}
        for sid, ids in by_station.items():
            for mid in ids:
                # One request per inverter, exactly like the S-Cloud web UI does.
                day = self._client.micro_count_by_day(sid, date, [mid])
                if not day.date:
                    day.date = date
                values = latest_values(day, mid, now)
                values.update(day_stats(day, mid))
                values["date"] = day.date or date
                values["modules"] = {}
                micro = next(m for m in self.micros if m.micro_id == mid)
                if micro.ports:
                    try:
                        mday = self._client.module_count_by_day(sid, date, mid, [p["port"] for p in micro.ports])
                        for p in micro.ports:
                            values["modules"][p["port"]] = latest_module_values(mday, mid, p["port"], now)
                    except Exception as err:  # noqa: BLE001 - keep inverter data if panels fail
                        _LOGGER.warning("Panel data for %s failed: %s", micro.sn, err)
                result[mid] = values
        return result

    async def _async_update_data(self):
        try:
            return await self.hass.async_add_executor_job(self._fetch)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Error fetching microinverter data: {err}") from err


@dataclass(frozen=True, kw_only=True)
class MicroSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], Any]


MICRO_SENSORS: tuple[MicroSensorDescription, ...] = (
    MicroSensorDescription(
        key="grid_voltage",
        name="Grid Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.get("grid_voltage"),
    ),
    MicroSensorDescription(
        key="grid_frequency",
        name="Grid Frequency",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: d.get("grid_frequency"),
    ),
    MicroSensorDescription(
        key="temperature",
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.get("temperature"),
    ),
    MicroSensorDescription(
        key="ac_power",
        name="AC Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.get("power"),
    ),
    MicroSensorDescription(
        key="grid_voltage_max_today",
        name="Grid Voltage Max Today",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        suggested_display_precision=1,
        value_fn=lambda d: d.get("grid_voltage_max"),
    ),
    MicroSensorDescription(
        key="dropouts_today",
        name="Production Dropouts Today",
        icon="mdi:flash-alert",
        native_unit_of_measurement="intervals",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("dropouts"),
    ),
)


def create_micro_device_info(micro: MicroInfo) -> dict:
    return {
        "identifiers": {(f"hoymiles_micro_{micro.micro_id}",)},
        "name": f"Microinverter {micro.sn}",
        "manufacturer": "Hoymiles",
        "model": micro.model or "Microinverter",
        "serial_number": micro.sn,
        "via_device": (f"hoymiles_station_{micro.station_id}",),
    }


class HoymilesMicroSensor(CoordinatorEntity, SensorEntity):
    entity_description: MicroSensorDescription

    def __init__(self, coordinator: HoymilesMicroCoordinator, micro: MicroInfo,
                 description: MicroSensorDescription):
        super().__init__(coordinator)
        self.entity_description = description
        self._micro = micro
        self._attr_name = f"Microinverter {micro.sn} {description.name}"
        self._attr_unique_id = f"hoymiles_nimbus_micro_{micro.micro_id}_{description.key}"
        self._attr_device_info = create_micro_device_info(micro)

    @property
    def _data(self) -> dict:
        return (self.coordinator.data or {}).get(self._micro.micro_id, {})

    @property
    def native_value(self):
        return self.entity_description.value_fn(self._data)

    @property
    def extra_state_attributes(self):
        d = self._data
        attrs = {"serial_number": self._micro.sn}
        if d.get("time"):
            attrs["data_time"] = f"{d.get('date')} {d.get('time')}"
        if self.entity_description.key == "grid_voltage":
            attrs["min_today"] = d.get("grid_voltage_min")
            attrs["max_today"] = d.get("grid_voltage_max")
        return attrs


PANEL_SENSORS = {
    "power": ("Power", UnitOfPower.WATT, SensorDeviceClass.POWER, 1),
    "voltage": ("Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, 1),
    "current": ("Current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, 2),
}


class HoymilesPanelSensor(CoordinatorEntity, SensorEntity):
    """DC power / voltage / current of one panel (one microinverter port).

    Keeps the unique_id of the original Nimbus panel sensors, so existing
    entities, history and dashboards carry over.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:solar-panel"

    def __init__(self, coordinator, micro: MicroInfo, port: dict, key: str):
        super().__init__(coordinator)
        label, unit, device_class, precision = PANEL_SENSORS[key]
        self._micro = micro
        self._port = port
        self._key = key
        module_id = f"{micro.sn}-{port['port']}"
        self._attr_name = f"{micro.station_name} Panel {module_id} {label}"
        self._attr_unique_id = f"hoymiles_nimbus_module_{module_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_suggested_display_precision = precision
        self._attr_device_info = create_module_device_info(module_id, f"hoymiles_station_{micro.station_id}")

    @property
    def _values(self) -> dict:
        micro = (self.coordinator.data or {}).get(self._micro.micro_id, {})
        return micro.get("modules", {}).get(self._port["port"], {})

    @property
    def native_value(self):
        return self._values.get(self._key)

    @property
    def extra_state_attributes(self):
        attrs = {
            "microinverter": self._micro.sn,
            "port": self._port["port"],
            "position_x": self._port.get("x"),
            "position_y": self._port.get("y"),
        }
        if self._values.get("time"):
            attrs["data_time"] = self._values["time"]
        if self._key == "current" and self._values.get("current_calculated"):
            attrs["calculated"] = "power / voltage"
        return attrs


async def async_setup_micro_sensors(hass, client, config_entry, async_add_entities):
    coordinator = HoymilesMicroCoordinator(hass, client, config_entry)
    await coordinator.async_config_entry_first_refresh()
    entities = [
        HoymilesMicroSensor(coordinator, micro, desc)
        for micro in coordinator.micros
        for desc in MICRO_SENSORS
    ]
    entities += [
        HoymilesPanelSensor(coordinator, micro, port, key)
        for micro in coordinator.micros
        for port in (micro.ports or [])
        for key in PANEL_SENSORS
    ]
    _LOGGER.info("Created %d microinverter sensors", len(entities))
    async_add_entities(entities)
