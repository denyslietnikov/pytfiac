"""HA services through the real client/parser, with a synthetic XML device."""

import asyncio
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.tfiac.api import TfiacClient, TfiacTimeoutError
from custom_components.tfiac.models import Fan

pytestmark = pytest.mark.asyncio


class XmlDevice:
    """Synthetic behavior, not proof of a real device's acknowledgement/timing."""

    def __init__(self, response):
        self.root = ET.fromstring(response)
        self.requests = []
        self.clients = []
        self.apply_writes = True
        self.fail_after_write = False
        self.fail_next_read = False
        self.reject_write = False

    def client(self, host, **kwargs):
        client = TfiacClient(host, **kwargs)
        client._send = self.send
        self.clients.append(client)
        return client

    async def send(self, message):
        request = ET.fromstring(message)
        self.requests.append(request)
        # Allow concurrent HA services and polling to race for the real lock.
        await asyncio.sleep(0)
        if request.get("msgid") == "SyncStatusReq":
            if self.fail_next_read:
                self.fail_next_read = False
                raise TfiacTimeoutError("Synthetic post-write failure")
            return ET.tostring(self.root)
        assert request.get("msgid") == "SetMessage"
        if self.reject_write:
            return b'<msg msgid="UnknownCmd"><UnknownCmd /></msg>'
        if self.apply_writes:
            for field in request.find("SetMessage"):
                self.root.find(f"statusUpdateMsg/{field.tag}").text = field.text
        if self.fail_after_write:
            self.fail_next_read = True
        return b'<msg msgid="SetMessage"><SetMessage /></msg>'

    @property
    def message_ids(self):
        return [request.get("msgid") for request in self.requests]


@pytest.fixture
def wire(status_response):
    device = XmlDevice(status_response)
    with (
        patch("custom_components.tfiac.TfiacClient", side_effect=device.client),
        patch(
            "custom_components.tfiac.config_flow.TfiacClient", side_effect=device.client
        ),
    ):
        yield device


async def setup(hass, entry, wire):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[
        0
    ].entity_id
    wire.requests.clear()
    return entity_id


@pytest.mark.parametrize(
    "service,data,attribute,expected",
    [
        ("set_hvac_mode", {"hvac_mode": "off"}, "state", "off"),
        ("set_hvac_mode", {"hvac_mode": "cool"}, "state", "cool"),
        ("set_hvac_mode", {"hvac_mode": "heat"}, "state", "heat"),
        ("set_hvac_mode", {"hvac_mode": "auto"}, "state", "auto"),
        ("set_hvac_mode", {"hvac_mode": "dry"}, "state", "dry"),
        ("set_hvac_mode", {"hvac_mode": "fan_only"}, "state", "fan_only"),
        ("set_fan_mode", {"fan_mode": "auto"}, "fan_mode", "auto"),
        ("set_fan_mode", {"fan_mode": "low"}, "fan_mode", "low"),
        ("set_fan_mode", {"fan_mode": "middle"}, "fan_mode", "middle"),
        ("set_fan_mode", {"fan_mode": "high"}, "fan_mode", "high"),
        ("set_swing_mode", {"swing_mode": "off"}, "swing_mode", "off"),
        ("set_swing_mode", {"swing_mode": "on"}, "swing_mode", "on"),
        (
            "set_swing_horizontal_mode",
            {"swing_horizontal_mode": "off"},
            "swing_horizontal_mode",
            "off",
        ),
        (
            "set_swing_horizontal_mode",
            {"swing_horizontal_mode": "on"},
            "swing_horizontal_mode",
            "on",
        ),
        ("set_preset_mode", {"preset_mode": "sleep"}, "preset_mode", "sleep"),
        ("set_preset_mode", {"preset_mode": "none"}, "preset_mode", "none"),
        ("turn_on", {}, "state", "auto"),
        ("turn_off", {}, "state", "off"),
        ("set_temperature", {"temperature": 78}, "temperature", 78),
        (
            "set_temperature",
            {"temperature": 78, "hvac_mode": "heat"},
            "state",
            "heat",
        ),
    ],
)
async def test_controls_read_back_real_snapshot(
    hass, entry, wire, service, data, attribute, expected
):
    entity_id = await setup(hass, entry, wire)
    await hass.services.async_call(
        "climate", service, {"entity_id": entity_id, **data}, blocking=True
    )
    state = hass.states.get(entity_id)
    actual = state.state if attribute == "state" else state.attributes[attribute]
    assert actual == expected
    # Changed controls use exactly one write; unchanged controls are read-only.
    assert wire.message_ids in (
        ["SyncStatusReq"],
        ["SyncStatusReq", "SetMessage", "SyncStatusReq"],
    )
    if service == "set_temperature":
        assert state.attributes["temperature"] == 78


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("horizontal", [False, True])
async def test_swing_preserves_fresh_other_axis(hass, entry, wire, horizontal, enabled):
    field = "WindDirection_H" if horizontal else "WindDirection_V"
    other = "WindDirection_V" if horizontal else "WindDirection_H"
    service = "set_swing_horizontal_mode" if horizontal else "set_swing_mode"
    attribute = "swing_horizontal_mode" if horizontal else "swing_mode"
    other_attribute = "swing_mode" if horizontal else "swing_horizontal_mode"
    mode = "on" if enabled else "off"
    # Start with the opposite value so this command must write.
    wire.root.find(f"statusUpdateMsg/{field}").text = "off" if enabled else "on"
    entity_id = await setup(hass, entry, wire)
    assert hass.states.get(entity_id).attributes[other_attribute] == "off"
    # Simulate a physical remote change after the last coordinator poll.
    wire.root.find(f"statusUpdateMsg/{other}").text = "on"
    await hass.services.async_call(
        "climate", service, {"entity_id": entity_id, attribute: mode}, blocking=True
    )
    assert wire.message_ids == ["SyncStatusReq", "SetMessage", "SyncStatusReq"]
    payload = wire.requests[1].find("SetMessage")
    assert {node.tag: node.text for node in payload} == {field: mode, other: "on"}
    attrs = hass.states.get(entity_id).attributes
    assert attrs[attribute] == mode
    assert attrs[other_attribute] == "on"


async def test_concurrent_swing_services_preserve_both_changes(hass, entry, wire):
    entity_id = await setup(hass, entry, wire)
    await asyncio.wait_for(
        asyncio.gather(
            hass.services.async_call(
                "climate",
                "set_swing_mode",
                {"entity_id": entity_id, "swing_mode": "on"},
                blocking=True,
            ),
            hass.services.async_call(
                "climate",
                "set_swing_horizontal_mode",
                {"entity_id": entity_id, "swing_horizontal_mode": "on"},
                blocking=True,
            ),
        ),
        timeout=1,
    )
    attrs = hass.states.get(entity_id).attributes
    assert attrs["swing_mode"] == "on"
    assert attrs["swing_horizontal_mode"] == "on"
    assert wire.message_ids == ["SyncStatusReq", "SetMessage", "SyncStatusReq"] * 2


@pytest.mark.parametrize("horizontal", [False, True])
async def test_swing_payload_omits_unreported_axis(hass, entry, wire, horizontal):
    field = "WindDirection_H" if horizontal else "WindDirection_V"
    other = "WindDirection_V" if horizontal else "WindDirection_H"
    service = "set_swing_horizontal_mode" if horizontal else "set_swing_mode"
    attribute = "swing_horizontal_mode" if horizontal else "swing_mode"
    status = wire.root.find("statusUpdateMsg")
    status.remove(status.find(other))
    entity_id = await setup(hass, entry, wire)
    await hass.services.async_call(
        "climate", service, {"entity_id": entity_id, attribute: "on"}, blocking=True
    )
    payload = wire.requests[1].find("SetMessage")
    assert {node.tag: node.text for node in payload} == {field: "on"}
    assert hass.states.get(entity_id).attributes[attribute] == "on"


async def test_concurrent_services_and_polling(hass, entry, wire):
    entity_id = await setup(hass, entry, wire)
    await asyncio.wait_for(
        asyncio.gather(
            hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": entity_id, "temperature": 78},
                blocking=True,
            ),
            hass.services.async_call(
                "climate",
                "set_fan_mode",
                {"entity_id": entity_id, "fan_mode": "middle"},
                blocking=True,
            ),
            entry.runtime_data.async_refresh(),
        ),
        timeout=1,
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.attributes["temperature"] == 78
    assert state.attributes["fan_mode"] == "middle"
    assert entry.runtime_data.data.target_temperature == 78
    assert entry.runtime_data.data.fan == Fan.MIDDLE
    assert wire.message_ids.count("SetMessage") == 2
    assert wire.message_ids.count("SyncStatusReq") == 5
    # No poll or other write may interleave inside either command transaction.
    for index, message_id in enumerate(wire.message_ids):
        if message_id == "SetMessage":
            assert wire.message_ids[index - 1 : index + 2] == [
                "SyncStatusReq",
                "SetMessage",
                "SyncStatusReq",
            ]
    assert len(wire.clients) == 1


async def test_failed_post_write_read_keeps_previous_snapshot(hass, entry, wire):
    entity_id = await setup(hass, entry, wire)
    previous = entry.runtime_data.data
    wire.fail_after_write = True
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": entity_id, "temperature": 78},
            blocking=True,
        )
    assert wire.message_ids == ["SyncStatusReq", "SetMessage", "SyncStatusReq"]
    assert entry.runtime_data.data is previous
    assert hass.states.get(entity_id).state == "unavailable"
    await entry.runtime_data.async_refresh()
    assert hass.states.get(entity_id).state == "auto"
    assert hass.states.get(entity_id).attributes["temperature"] == 78


async def test_ack_does_not_publish_unapplied_change(hass, entry, wire):
    entity_id = await setup(hass, entry, wire)
    wire.apply_writes = False
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity_id, "temperature": 78},
        blocking=True,
    )
    assert wire.message_ids == ["SyncStatusReq", "SetMessage", "SyncStatusReq"]
    assert hass.states.get(entity_id).attributes["temperature"] == 77


async def test_device_rejection_is_not_retried(hass, entry, wire):
    entity_id = await setup(hass, entry, wire)
    wire.reject_write = True
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "climate", "turn_off", {"entity_id": entity_id}, blocking=True
        )
    assert wire.message_ids == ["SyncStatusReq", "SetMessage"]
    assert hass.states.get(entity_id).state == "unavailable"
    await entry.runtime_data.async_refresh()
    assert hass.states.get(entity_id).state == "auto"


async def test_reconfigure_reloads_real_client_without_new_entity(hass, entry, wire):
    entity_id = await setup(hass, entry, wire)
    old_coordinator = entry.runtime_data
    result = await hass.config_entries.flow.async_init(
        "tfiac",
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        data={"host": "192.0.2.2"},
    )
    await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["host"] == "192.0.2.2"
    assert entry.unique_id == "legacy-id"
    assert old_coordinator._shutdown_requested
    assert entry.runtime_data.client.host == "192.0.2.2"
    assert [client.host for client in wire.clients] == [
        "192.0.2.1",
        "192.0.2.2",
        "192.0.2.2",
    ]
    registry_entries = er.async_entries_for_config_entry(
        er.async_get(hass), entry.entry_id
    )
    assert len(registry_entries) == 1
    assert registry_entries[0].entity_id == entity_id
    assert hass.states.get(entity_id).state == "auto"
