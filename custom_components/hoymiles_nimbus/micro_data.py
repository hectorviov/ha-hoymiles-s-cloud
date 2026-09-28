"""Parser for the S-Cloud microinverter day series (pvm-data/api/0/micro/data/count_by_day).

The endpoint is called with ``pb_ver: 1`` and answers with a protobuf message:

    1: repeated string   time slots ("HH:MM", ~5 min apart, up to the last upload)
    2: repeated message  one series per (quota, microinverter)
         1: string          quota name, e.g. "MI_NET_V"
         2: packed double   one value per time slot
         3: varint          microinverter id
    3: string            date ("YYYY-MM-DD")

No .proto file is published, so this is a small hand-written wire-format reader.
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

QUOTA_POWER = "MI_POWER"
QUOTA_GRID_VOLTAGE = "MI_NET_V"
QUOTA_GRID_FREQUENCY = "MI_NET_RATE"
QUOTA_TEMPERATURE = "MI_TEMPERATURE"

ALL_QUOTAS = [QUOTA_POWER, QUOTA_GRID_VOLTAGE, QUOTA_GRID_FREQUENCY, QUOTA_TEMPERATURE]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class MicroDaySeries:
    date: str | None = None
    times: list[str] = field(default_factory=list)
    # {micro_id: {quota: [values...]}}
    series: dict[int, dict[str, list[float]]] = field(default_factory=dict)


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _iter_fields(buf: bytes):
    """Yield (field_number, wire_type, value) for each field in a message."""
    pos = 0
    while pos < len(buf):
        key, pos = _read_varint(buf, pos)
        field_no, wire_type = key >> 3, key & 7
        if wire_type == 0:
            value, pos = _read_varint(buf, pos)
        elif wire_type == 1:
            value = buf[pos:pos + 8]
            pos += 8
        elif wire_type == 2:
            length, pos = _read_varint(buf, pos)
            value = buf[pos:pos + length]
            pos += length
        elif wire_type == 5:
            value = buf[pos:pos + 4]
            pos += 4
        else:
            raise ValueError(f"unsupported wire type {wire_type}")
        yield field_no, wire_type, value


def parse_count_by_day(content: bytes, requested_micro_ids: list[int] | None = None) -> MicroDaySeries:
    result = MicroDaySeries()
    for field_no, wire_type, value in _iter_fields(content):
        if field_no == 1 and wire_type == 2:
            result.times.append(value.decode("utf-8", "replace"))
        elif field_no == 3 and wire_type == 2:
            text = value.decode("utf-8", "replace")
            if _DATE_RE.match(text):
                result.date = text
        elif field_no == 2 and wire_type == 2:
            quota = None
            values: list[float] = []
            micro_id = None
            for f, wt, v in _iter_fields(value):
                if f == 1 and wt == 2:
                    quota = v.decode("utf-8", "replace")
                elif f == 2 and wt == 2:
                    # Values are float32 upcast to double (269.799987...), so round them.
                    values = [round(x[0], 2) for x in struct.iter_unpack("<d", v[: len(v) - len(v) % 8])]
                elif f == 2 and wt == 1:  # unpacked double
                    values.append(round(struct.unpack("<d", v)[0], 2))
                elif f == 3 and wt == 0:
                    micro_id = v
            if quota is None:
                continue
            if micro_id is None and requested_micro_ids and len(requested_micro_ids) == 1:
                micro_id = requested_micro_ids[0]
            if micro_id is None:
                continue
            result.series.setdefault(micro_id, {})[quota] = values
    return result


MODULE_POWER = "MODULE_POWER"
MODULE_VOLTAGE = "MODULE_V"
MODULE_CURRENT = "MODULE_I"
MODULE_QUOTAS = [MODULE_POWER, MODULE_VOLTAGE, MODULE_CURRENT]

# A slot older than this (or from another day) means the DTU stopped
# reporting (night, outage): values are then treated as "not producing".
STALE_AFTER_MINUTES = 40


@dataclass
class ModuleDaySeries:
    date: str | None = None
    times: list[str] = field(default_factory=list)
    # {(micro_id, port): {quota: [values...]}}
    series: dict[tuple[int, int], dict[str, list[float]]] = field(default_factory=dict)
    # Time axis of each quota (requests are separate, so they may differ)
    quota_times: dict[str, list[str]] = field(default_factory=dict)


def parse_module_count_by_day(content: bytes, requested_date: str | None = None) -> ModuleDaySeries:
    """Parse pvm-data/api/0/module/data/count_by_day (pb_ver 1).

    Same layout as the microinverter call, plus field 4 = port in each series.
    """
    result = ModuleDaySeries(date=requested_date)
    for field_no, wire_type, value in _iter_fields(content):
        if field_no == 1 and wire_type == 2:
            result.times.append(value.decode("utf-8", "replace"))
        elif field_no == 3 and wire_type == 2:
            # In this response field 3 is the chart type, not a date; keep the
            # requested date unless the field really looks like one.
            text = value.decode("utf-8", "replace")
            if _DATE_RE.match(text):
                result.date = text
        elif field_no == 2 and wire_type == 2:
            quota, values, micro_id, port = None, [], None, None
            for f, wt, v in _iter_fields(value):
                if f == 1 and wt == 2:
                    quota = v.decode("utf-8", "replace")
                elif f == 2 and wt == 2:
                    values = [round(x[0], 2) for x in struct.iter_unpack("<d", v[: len(v) - len(v) % 8])]
                elif f == 3 and wt == 0:
                    micro_id = v
                elif f == 4 and wt == 0:
                    port = v
            if quota is None or micro_id is None or port is None:
                continue
            result.series.setdefault((micro_id, port), {})[quota] = values
            result.quota_times.setdefault(quota, result.times)
    return result


def is_stale(date: str | None, time: str | None, now) -> bool:
    """True when the last slot is not from today or is too old."""
    if now is None:
        return False
    if not date or not time:
        return True
    if date != now.strftime("%Y-%m-%d"):
        return True
    try:
        hh, mm = (int(x) for x in time.split(":"))
    except ValueError:
        return True
    age = (now.hour * 60 + now.minute) - (hh * 60 + mm)
    return age > STALE_AFTER_MINUTES


def latest_module_values(day: ModuleDaySeries, micro_id: int, port: int, now=None) -> dict:
    """Latest power/voltage/current of one panel.

    Each quota is aligned on its own time axis (they come from separate
    requests). Current is calculated as P / V when the API has none.
    """
    data = day.series.get((micro_id, port), {})
    empty = {"power": 0.0, "voltage": None, "current": None, "time": None, "current_calculated": False}
    if not data:
        return empty

    def times_for(quota):
        return day.quota_times.get(quota) or day.times

    ref_times = times_for(MODULE_POWER)
    if not ref_times:
        return empty
    time = ref_times[-1]
    if is_stale(day.date, time, now):
        return {**empty, "time": time}

    def at(quota):
        vals = data.get(quota) or []
        times = times_for(quota)
        try:
            idx = times.index(time)
        except ValueError:
            return None
        return vals[idx] if idx < len(vals) else None

    power = at(MODULE_POWER) or 0.0
    voltage = at(MODULE_VOLTAGE)
    current = at(MODULE_CURRENT)
    calculated = False
    if current is None and voltage:
        current = round(power / voltage, 2)
        calculated = True
    return {"power": power, "voltage": voltage, "current": current, "time": time,
            "current_calculated": calculated}


def latest_values(day: MicroDaySeries, micro_id: int, now=None) -> dict:
    """Return the most recent slot for one microinverter.

    When power, voltage and frequency are all 0 the inverter is asleep
    (night / no sun); values are then reported as None instead of 0.
    """
    data = day.series.get(micro_id, {})
    if not day.times or not data:
        return {}

    idx = len(day.times) - 1

    def at(quota):
        vals = data.get(quota) or []
        return vals[idx] if idx < len(vals) else None

    power = at(QUOTA_POWER)
    voltage = at(QUOTA_GRID_VOLTAGE)
    frequency = at(QUOTA_GRID_FREQUENCY)
    temperature = at(QUOTA_TEMPERATURE)
    asleep = (not voltage and not frequency and not power) or is_stale(day.date, day.times[idx], now)

    return {
        "time": day.times[idx],
        "power": 0.0 if asleep else power,
        "grid_voltage": None if asleep else voltage,
        "grid_frequency": None if asleep else frequency,
        "temperature": None if asleep else temperature,
        "asleep": asleep,
    }


def day_stats(day: MicroDaySeries, micro_id: int) -> dict:
    """Daily aggregates for one microinverter.

    ``dropouts`` counts time slots where the inverter was awake (grid voltage
    present) and producing earlier and later in the day, but reported 0 W.
    That pattern usually means a grid-protection trip (e.g. overvoltage).
    """
    data = day.series.get(micro_id, {})
    power = data.get(QUOTA_POWER) or []
    voltage = data.get(QUOTA_GRID_VOLTAGE) or []
    awake_v = [v for v in voltage if v]

    producing = [i for i, p in enumerate(power) if p and p > 0]
    dropouts = 0
    if producing:
        first, last = producing[0], producing[-1]
        for i in range(first, last + 1):
            if (power[i] or 0) <= 0 and i < len(voltage) and voltage[i]:
                dropouts += 1

    return {
        "grid_voltage_max": max(awake_v) if awake_v else None,
        "grid_voltage_min": min(awake_v) if awake_v else None,
        "dropouts": dropouts,
    }
