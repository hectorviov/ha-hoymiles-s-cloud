"""Test setup: let Home Assistant load the custom integration from custom_components/."""
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
