from unittest.mock import patch, MagicMock
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from custom_components.hoymiles_nimbus.hoymiles_client import HoymilesClient
from custom_components.hoymiles_nimbus.micro import HoymilesMicroCoordinator


def test_station_cache_respects_interval():
    c = HoymilesClient("u","p","https://x/")
    c.token = "t"; c.scan_interval_s = 600
    with patch.object(c, "_post_request", return_value={"data":{"real_power":1}}) as m:
        c.count_station_real_data(1); c.count_station_real_data(1)
        assert m.call_count == 1
        c.scan_interval_s = 0
        c.count_station_real_data(1)
        assert m.call_count == 2

async def test_micro_interval(hass):
    client = MagicMock(); client.scan_interval_s = 900
    coord = HoymilesMicroCoordinator(hass, client, None)
    assert coord.update_interval.total_seconds() == 900

async def test_options_flow(hass):
    entry = MockConfigEntry(domain="hoymiles_nimbus", data={"username":"u","password":"p","base_url":"https://neapi.hoymiles.com/"})
    entry.add_to_hass(hass)
    with patch("custom_components.hoymiles_nimbus.async_setup_entry", return_value=True):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "form"
        result = await hass.config_entries.options.async_configure(result["flow_id"],
            {"username":"u","password":"p","base_url":"https://neapi.hoymiles.com/","scan_interval":10})
    assert result["type"] == "create_entry", result
    assert entry.options == {"scan_interval": 10}
    assert entry.data["password"] == "p"
