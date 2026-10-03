"""Fixtures using the real Home Assistant custom-component test harness."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tfiac.api import TfiacClient, parse_status


@pytest.fixture(autouse=True)
def custom_integration(enable_custom_integrations):
    """Allow loading this custom component in HA tests."""


@pytest.fixture(autouse=True)
def units(hass):
    hass.config.units = US_CUSTOMARY_SYSTEM


@pytest.fixture
def ha_state():
    return parse_status(
        (Path(__file__).parents[1] / "fixtures" / "status.xml").read_bytes()
    )


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain="tfiac",
        title="Katie AC",
        data={"host": "192.0.2.1"},
        unique_id="legacy-id",
        version=1,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def client(ha_state):
    client = TfiacClient("192.0.2.1")
    client.async_update = AsyncMock(return_value=ha_state)
    client.async_apply_changes = AsyncMock(return_value=ha_state)
    with (
        patch("custom_components.tfiac.TfiacClient", return_value=client),
        patch("custom_components.tfiac.config_flow.TfiacClient", return_value=client),
    ):
        yield client
