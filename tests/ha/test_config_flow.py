"""Exercise HA's actual flow manager, including legacy entries."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tfiac.api import TfiacParseError, TfiacTimeoutError

pytestmark = pytest.mark.asyncio


async def test_user_flow(hass, client):
    with patch("custom_components.tfiac.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            "tfiac", context={"source": SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": " 192.0.2.1 "}
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"] == {"host": "192.0.2.1"}
    assert result["result"].unique_id is None
    assert result["title"] == "Katie AC"


@pytest.mark.parametrize(
    "error,reason",
    [
        (TfiacTimeoutError(), "cannot_connect"),
        (TfiacParseError(), "cannot_connect"),
        (RuntimeError("unexpected"), "unknown"),
    ],
)
async def test_errors(hass, client, error, reason):
    client.async_update.side_effect = error
    result = await hass.config_entries.flow.async_init(
        "tfiac", context={"source": SOURCE_USER}, data={"host": "192.0.2.1"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": reason}


async def test_blank_host(hass, client):
    result = await hass.config_entries.flow.async_init(
        "tfiac", context={"source": SOURCE_USER}, data={"host": " "}
    )
    assert result["errors"] == {"host": "cannot_connect"}
    client.async_update.assert_not_awaited()


@pytest.mark.parametrize("options", [{}, {"host": "192.0.2.2"}])
async def test_duplicate(hass, entry, client, options):
    hass.config_entries.async_update_entry(entry, options=options)
    host = options.get("host", entry.data["host"])
    result = await hass.config_entries.flow.async_init(
        "tfiac", context={"source": SOURCE_USER}, data={"host": host}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    client.async_update.assert_not_awaited()


async def test_reconfigure_preserves_identity_and_options(hass, entry, client):
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, "extra": "preserved"},
        options={"host": "192.0.2.2", "friendly_name": "Bedroom"},
    )
    with patch.object(
        hass.config_entries, "async_reload", AsyncMock(return_value=True)
    ) as reload:
        result = await hass.config_entries.flow.async_init(
            "tfiac", context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
        )
        assert result["type"] == FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "192.0.2.3"}
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data == {"host": "192.0.2.3", "extra": "preserved"}
    assert entry.options == {"friendly_name": "Bedroom"}
    assert entry.unique_id == "legacy-id"
    assert len(hass.config_entries.async_entries("tfiac")) == 1
    reload.assert_awaited_once_with(entry.entry_id)


async def test_reconfigure_duplicate(hass, entry, client):
    other = MockConfigEntry(domain="tfiac", data={"host": "192.0.2.2"})
    other.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        "tfiac",
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        data={"host": "192.0.2.2"},
    )
    assert result["reason"] == "already_configured"
    assert entry.data["host"] == "192.0.2.1"


async def test_reconfigure_failure_keeps_entry(hass, entry, client):
    client.async_update.side_effect = TfiacTimeoutError()
    result = await hass.config_entries.flow.async_init(
        "tfiac",
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        data={"host": "192.0.2.2"},
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data["host"] == "192.0.2.1"
