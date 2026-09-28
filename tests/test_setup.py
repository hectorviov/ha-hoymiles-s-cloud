"""Smoke test: the whole integration sets up with a mocked cloud."""
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.test_panels import client_for


async def test_entry_sets_up_all_platforms(hass):
    client = client_for("2026-01-01")
    client.count_station_real_data.return_value = {"data": {"real_power": 1, "today_eq": 1000, "total_eq": 5000, "capacitor": 4.96}}
    client.select_device_of_tree.return_value = []
    client.parse_dtu_info.return_value = []
    entry = MockConfigEntry(domain="hoymiles_nimbus", data={"username": "u", "password": "p"})
    entry.add_to_hass(hass)
    with patch("custom_components.hoymiles_nimbus.HoymilesClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    states = hass.states.async_all("sensor")
    names = {s.name for s in states}
    assert "Test Plant Panel 1164A0000001-1 Power" in names
    assert any(n.endswith("Grid Voltage") for n in names)
    assert len(states) >= 4 + 6 + 12
