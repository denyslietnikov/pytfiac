"""Actual HA switch services and options with a synthetic XML device."""

import asyncio
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.tfiac.api import TfiacClient, TfiacTimeoutError
from custom_components.tfiac.diagnostics import async_get_config_entry_diagnostics
from custom_components.tfiac.models import CommandProfile

pytestmark = pytest.mark.asyncio
FIELDS = (
    ("display", "Opt_display", "Opt_display"),
    ("beep", "BeepEnable", "BeepEnable"),
)


class OptionalDevice:
    """A test contract, deliberately not a real firmware implementation."""

    def __init__(self):
        self.root = ET.fromstring(
            (Path(__file__).parents[1] / "fixtures" / "status.xml").read_bytes()
        )
        self.requests = []
        self.hosts = []
        self.apply_writes = True
        self.reject_write = False
        self.fail_read = False
        self.fail_after_write = False
        for _, tag, _ in FIELDS:
            self.set(tag, "off")

    def set(self, tag, value):
        status = self.root.find("statusUpdateMsg")
        node = status.find(tag)
        if node is None:
            node = ET.SubElement(status, tag)
        node.text = value

    def client(self, host, **kwargs):
        self.hosts.append(host)
        client = TfiacClient(host, **kwargs)
        client._send = self.send
        return client

    async def send(self, message):
        request = ET.fromstring(message)
        self.requests.append(request)
        await asyncio.sleep(0)
        if request.get("msgid") == "SyncStatusReq":
            if self.fail_read:
                raise TfiacTimeoutError("Synthetic read failure")
            return ET.tostring(self.root)
        assert request.get("msgid") == "SetMessage"
        if self.reject_write:
            return b'<msg msgid="UnknownCmd"><UnknownCmd /></msg>'
        if self.apply_writes:
            for field in request.find("SetMessage"):
                # Livingroom ignores these old writer spellings, rather than
                # mapping every read alias into a writable command.
                if field.tag not in ("Opt_eco", "Opt_beep"):
                    self.set(field.tag, field.text)
        if self.fail_after_write:
            self.fail_read = True
        return b'<msg msgid="SetMessage"><SetMessage /></msg>'

    @property
    def message_ids(self):
        return [request.get("msgid") for request in self.requests]


@pytest.fixture
def device():
    device = OptionalDevice()
    with patch("custom_components.tfiac.TfiacClient", side_effect=device.client):
        yield device


def switches(hass, entry):
    return [
        item
        for item in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if item.domain == "switch"
    ]


async def setup(hass, entry, device, *, profile=True, enable=False):
    if profile:
        hass.config_entries.async_update_entry(
            entry,
            options={**entry.options, "command_profile": "legacy_experimental"},
        )
    if enable:
        for field, _, _ in FIELDS:
            er.async_get(hass).async_get_or_create(
                "switch",
                "tfiac",
                f"{entry.entry_id}_{field}",
                config_entry=entry,
                suggested_object_id=f"ac_{field}",
                disabled_by=None,
            )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device.requests.clear()
    return {
        field: er.async_get(hass).async_get_entity_id(
            "switch", "tfiac", f"{entry.entry_id}_{field}"
        )
        for field, _, _ in FIELDS
    }


async def test_default_profile_creates_no_switches(hass, entry, device):
    await setup(hass, entry, device, profile=False)
    assert switches(hass, entry) == []
    assert entry.runtime_data.client.command_profile == CommandProfile.DISABLED


async def test_opted_in_entities_disabled_by_default_and_share_device(
    hass, entry, device
):
    await setup(hass, entry, device)
    registered = switches(hass, entry)
    assert len(registered) == 2
    assert {item.unique_id for item in registered} == {
        f"{entry.entry_id}_{field}" for field, _, _ in FIELDS
    }
    assert {item.original_name for item in registered} == {
        "Display",
        "Beep",
    }
    assert all(
        item.disabled_by == er.RegistryEntryDisabler.INTEGRATION for item in registered
    )
    assert all(hass.states.get(item.entity_id) is None for item in registered)
    assert (
        len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1
    )
    assert len(entry.runtime_data._listeners) == 1
    assert device.requests == []


@pytest.mark.parametrize("field,status_tag,command_tag", FIELDS)
@pytest.mark.parametrize("enabled", [False, True])
async def test_switch_services_exact_command_and_no_mode_rules(
    hass, entry, device, field, status_tag, command_tag, enabled
):
    for other, tag, _ in FIELDS:
        if other != field:
            device.set(tag, "on")
    device.set(status_tag, "off" if enabled else "on")
    device.set("Opt_sleepMode", "customSleepProfile:private")
    device.set("TurnOn", "off")
    entities = await setup(hass, entry, device, enable=True)
    # Physical remote changes the fan/temp since the cached coordinator snapshot.
    device.set("WindSpeed", "High")
    device.set("SetTemp", "79")
    await hass.services.async_call(
        "switch",
        "turn_on" if enabled else "turn_off",
        {"entity_id": entities[field]},
        blocking=True,
    )
    assert hass.states.get(entities[field]).state == ("on" if enabled else "off")
    assert device.message_ids == ["SyncStatusReq", "SetMessage", "SyncStatusReq"]
    payload = {node.tag: node.text for node in device.requests[1].find("SetMessage")}
    assert payload == {
        "TurnOn": "off",
        "BaseMode": "selfFeel",
        "SetTemp": "79.0",
        "WindSpeed": "High",
        "Opt_sleepMode": "customSleepProfile:private",
        command_tag: "on" if enabled else "off",
    }
    assert entry.runtime_data.data.sleep == "customSleepProfile:private"
    for other, _, _ in FIELDS:
        if other != field:
            assert getattr(entry.runtime_data.data, other) is True
    assert len(entry.runtime_data._listeners) == 3


async def test_missing_invalid_conflicting_flags_do_not_create_controls(
    hass, entry, device
):
    status = device.root.find("statusUpdateMsg")
    status.remove(status.find("Opt_super"))
    device.set("Opt_display", "unknown")
    device.set("Opt_beep", "on")  # Conflicts with BeepEnable=off.
    await setup(hass, entry, device)
    assert switches(hass, entry) == []
    assert entry.runtime_data.last_update_success


async def test_status_loss_is_unknown_then_service_rejected_without_write(
    hass, entry, device
):
    entities = await setup(hass, entry, device, enable=True)
    device.set("Opt_display", "invalid")
    await entry.runtime_data.async_refresh()
    assert hass.states.get(entities["display"]).state == "unknown"
    assert entry.runtime_data.last_update_success
    device.requests.clear()
    with pytest.raises(ServiceValidationError, match="usable display"):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entities["display"]}, blocking=True
        )
    assert device.message_ids == ["SyncStatusReq"]


@pytest.mark.parametrize("failure", ["ack_only", "reject", "post_read"])
async def test_no_optimism_or_retries_and_recovery(
    hass, entry, device, failure, monkeypatch
):
    entities = await setup(hass, entry, device, enable=True)
    if failure == "ack_only":
        monkeypatch.setattr(
            "custom_components.tfiac.api.COMMAND_CONFIRMATION_TIMEOUT", 0.05
        )
        device.apply_writes = False
        with pytest.raises(HomeAssistantError, match="did not confirm"):
            await hass.services.async_call(
                "switch", "turn_on", {"entity_id": entities["display"]}, blocking=True
            )
        assert hass.states.get(entities["display"]).state == "off"
        assert entry.runtime_data.last_update_success
        assert device.message_ids == ["SyncStatusReq", "SetMessage", "SyncStatusReq"]
        return
    if failure == "reject":
        device.reject_write = True
    else:
        device.fail_after_write = True
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entities["display"]}, blocking=True
        )
    assert sum(request.get("msgid") == "SetMessage" for request in device.requests) == 1
    assert entry.runtime_data.data.display is False
    assert all(
        hass.states.get(entity_id).state == "unavailable"
        for entity_id in entities.values()
    )
    device.reject_write = device.fail_after_write = device.fail_read = False
    await entry.runtime_data.async_refresh()
    assert hass.states.get(entities["display"]).state == (
        "off" if failure == "reject" else "on"
    )


async def test_concurrent_optional_climate_and_polling_share_lock(hass, entry, device):
    entities = await setup(hass, entry, device, enable=True)
    climate_id = er.async_get(hass).async_get_entity_id(
        "climate", "tfiac", entry.entry_id
    )
    await asyncio.wait_for(
        asyncio.gather(
            hass.services.async_call(
                "switch", "turn_on", {"entity_id": entities["display"]}, blocking=True
            ),
            hass.services.async_call(
                "switch", "turn_on", {"entity_id": entities["beep"]}, blocking=True
            ),
            hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": climate_id, "temperature": 78},
                blocking=True,
            ),
            entry.runtime_data.async_refresh(),
        ),
        timeout=1,
    )
    assert hass.states.get(entities["display"]).state == "on"
    assert hass.states.get(entities["beep"]).state == "on"
    assert entry.runtime_data.data.target_temperature == 78
    # One independent poll, three uninterrupted read/write/read transactions.
    ids = device.message_ids
    for _ in range(3):
        index = ids.index("SetMessage")
        assert ids[index - 1 : index + 2] == [
            "SyncStatusReq",
            "SetMessage",
            "SyncStatusReq",
        ]
        del ids[index - 1 : index + 2]
    assert ids == ["SyncStatusReq"]


async def options(hass, entry, profile):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"command_profile": profile}
    )
    await hass.async_block_till_done()
    return result


async def test_options_auto_reload_no_writes_preserves_options_and_ids(
    hass, entry, device
):
    hass.config_entries.async_update_entry(
        entry,
        options={"host": "192.0.2.2", "friendly_name": "Bedroom", "extra": "keep"},
    )
    await setup(hass, entry, device, profile=False)
    climate_before = er.async_get(hass).async_get_entity_id(
        "climate", "tfiac", entry.entry_id
    )
    result = await options(hass, entry, "legacy_experimental")
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "host": "192.0.2.2",
        "friendly_name": "Bedroom",
        "extra": "keep",
        "command_profile": "legacy_experimental",
    }
    assert (
        entry.runtime_data.client.command_profile == CommandProfile.LEGACY_EXPERIMENTAL
    )
    assert len(switches(hass, entry)) == 2
    assert device.hosts == ["192.0.2.2", "192.0.2.2"]
    assert device.message_ids == ["SyncStatusReq"]
    report = await async_get_config_entry_diagnostics(hass, entry)
    assert report["optional_command_contract"] == {
        "profile": "legacy_experimental",
        "hardware_validated": False,
        "command_fields": {
            "display": "Opt_display",
            "beep": "BeepEnable",
        },
    }
    assert (
        er.async_get(hass).async_get_entity_id("climate", "tfiac", entry.entry_id)
        == climate_before
    )
    previous = {item.unique_id: item.entity_id for item in switches(hass, entry)}
    await options(hass, entry, "disabled")
    assert entry.runtime_data.client.command_profile == CommandProfile.DISABLED
    assert len(entry.runtime_data._listeners) == 1
    await options(hass, entry, "legacy_experimental")
    assert {
        item.unique_id: item.entity_id for item in switches(hass, entry)
    } == previous
    assert (
        len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1
    )
    assert all(request.get("msgid") == "SyncStatusReq" for request in device.requests)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_disabling_profile_removes_enabled_controls_from_runtime(
    hass, entry, device
):
    entities = await setup(hass, entry, device, enable=True)
    await options(hass, entry, "disabled")
    assert len(entry.runtime_data._listeners) == 1
    assert hass.states.get(entities["display"]).state == "unavailable"
    assert all(request.get("msgid") == "SyncStatusReq" for request in device.requests)


async def test_invalid_profile_does_not_change_options(hass, entry, device):
    await setup(hass, entry, device, profile=False)
    with pytest.raises(InvalidData):
        await options(hass, entry, "auto_guess")
    # Initial submitted data reaches the handler before any prior form schema.
    result = await hass.config_entries.options.async_init(
        entry.entry_id, data={"command_profile": "auto_guess"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"command_profile": "invalid_profile"}
    assert entry.options == {}
    assert device.requests == []


async def test_upgrade_removes_only_retired_eco_and_turbo_switches(hass, entry, device):
    registry = er.async_get(hass)
    old_ids = []
    for field in ("eco", "turbo"):
        old_ids.append(
            registry.async_get_or_create(
                "switch",
                "tfiac",
                f"{entry.entry_id}_{field}",
                config_entry=entry,
                suggested_object_id=f"ac_{field}",
                disabled_by=None,
            ).entity_id
        )
    keep = registry.async_get_or_create(
        "switch",
        "tfiac",
        f"{entry.entry_id}_future",
        config_entry=entry,
        suggested_object_id="ac_future",
    )
    entities = await setup(hass, entry, device, enable=True)
    assert all(registry.async_get(entity_id) is None for entity_id in old_ids)
    assert registry.async_get(keep.entity_id) is not None
    assert all(
        registry.async_get(entity_id) is not None for entity_id in entities.values()
    )
    assert device.requests == []
