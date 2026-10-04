"""HA services through the real client/parser, with a synthetic XML device."""

import asyncio
from unittest.mock import patch
from xml.etree import ElementTree as ET

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import METRIC_SYSTEM, US_CUSTOMARY_SYSTEM

from custom_components.tfiac.api import TfiacClient, TfiacTimeoutError
from custom_components.tfiac.models import Fan

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "units,requested,wire_temperature,display_temperature",
    [
        (METRIC_SYSTEM, 16, 60.8, 16),
        (METRIC_SYSTEM, 26, 78.8, 26),
        (METRIC_SYSTEM, 23.3, 73.94, 23.3),
        (METRIC_SYSTEM, 23.01, 73.42, 23),
        (METRIC_SYSTEM, 26.12345678, 79.02, 26.1),
        (METRIC_SYSTEM, 16.2, 61.16, 16.2),
        (METRIC_SYSTEM, 31.1, 87.98, 31.1),
        (METRIC_SYSTEM, (61 - 32) / 1.8, 61, 16.1),
        (METRIC_SYSTEM, (88 - 32) / 1.8, 88, 31.1),
        (US_CUSTOMARY_SYSTEM, 61, 61, 61),
        (US_CUSTOMARY_SYSTEM, 88, 88, 88),
        (US_CUSTOMARY_SYSTEM, 78.8, 78.8, 79),
        (US_CUSTOMARY_SYSTEM, 79.022222204, 79.02, 79),
    ],
)
async def test_temperature_round_trip_through_ha_and_real_client(
    hass,
    entry,
    wire,
    monkeypatch,
    units,
    requested,
    wire_temperature,
    display_temperature,
):
    monkeypatch.setattr(
        "custom_components.tfiac.api.COMMAND_CONFIRMATION_TIMEOUT", 0.05
    )
    hass.config.units = units
    wire.root.find("statusUpdateMsg/TurnOn").text = "on"
    wire.root.find("statusUpdateMsg/BaseMode").text = "cool"
    entity_id = await setup(hass, entry, wire)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity_id, "temperature": requested},
        blocking=True,
    )
    assert wire.message_ids == ["SyncStatusReq", "SetMessage", "SyncStatusReq"]
    payload = wire.requests[1].find("SetMessage")
    assert payload.find("SetTemp").text == str(float(wire_temperature))
    assert payload.find("TurnOn").text == "on"
    assert payload.find("BaseMode").text == "cool"
    assert payload.find("Degree_Half") is None
    assert entry.runtime_data.data.target_temperature == wire_temperature
    actual = hass.states.get(entity_id)
    assert actual.state == "cool"
    assert actual.attributes["temperature"] == display_temperature


@pytest.mark.parametrize("units", [METRIC_SYSTEM, US_CUSTOMARY_SYSTEM])
@pytest.mark.parametrize("degree_half", ["on", "off", None])
async def test_native_range_and_unknown_step_are_not_inferred(
    hass, entry, wire, units, degree_half
):
    hass.config.units = units
    node = wire.root.find("statusUpdateMsg/Degree_Half")
    if degree_half is None:
        wire.root.find("statusUpdateMsg").remove(node)
    else:
        node.text = degree_half
    entity_id = await setup(hass, entry, wire)
    entity = hass.data["climate"].get_entity(entity_id)
    assert entity.temperature_unit == "°F"
    assert entity.min_temp == 60.8
    assert entity.max_temp == 88
    assert entity.target_temperature_step is None
    actual = hass.states.get(entity_id)
    assert "target_temp_step" not in actual.attributes
    assert actual.attributes["min_temp"] == (16 if units == METRIC_SYSTEM else 61)
    assert actual.attributes["max_temp"] == (31.1 if units == METRIC_SYSTEM else 88)
    assert wire.requests == []


@pytest.mark.parametrize(
    "units,requested",
    [
        (METRIC_SYSTEM, 15.9),
        (METRIC_SYSTEM, 31.2),
        (US_CUSTOMARY_SYSTEM, 60.7999),
        (US_CUSTOMARY_SYSTEM, 88.0001),
    ],
)
async def test_ha_rejects_outside_native_range_before_network(
    hass, entry, wire, units, requested
):
    hass.config.units = units
    entity_id = await setup(hass, entry, wire)
    previous = entry.runtime_data.data
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": entity_id, "temperature": requested},
            blocking=True,
        )
    assert wire.requests == []
    assert entry.runtime_data.data is previous
    assert entry.runtime_data.last_update_success


@pytest.mark.parametrize("requested", [float("nan"), float("inf"), float("-inf")])
async def test_nonfinite_temperature_never_writes(hass, entry, wire, requested):
    entity_id = await setup(hass, entry, wire)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": entity_id, "temperature": requested},
            blocking=True,
        )
    assert "SetMessage" not in wire.message_ids
    assert entry.runtime_data.data.target_temperature == 77
    assert entry.runtime_data.last_update_success


async def test_metric_rounding_noop_does_not_power_on(hass, entry, wire):
    hass.config.units = METRIC_SYSTEM
    wire.root.find("statusUpdateMsg/TurnOn").text = "off"
    entity_id = await setup(hass, entry, wire)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity_id, "temperature": 25.00000001},
        blocking=True,
    )
    assert wire.message_ids == ["SyncStatusReq"]
    assert hass.states.get(entity_id).state == "off"
    assert hass.states.get(entity_id).attributes["temperature"] == 25


async def test_metric_setpoint_without_mode_does_not_power_on(hass, entry, wire):
    hass.config.units = METRIC_SYSTEM
    wire.root.find("statusUpdateMsg/TurnOn").text = "off"
    entity_id = await setup(hass, entry, wire)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity_id, "temperature": 26},
        blocking=True,
    )
    assert wire.message_ids == ["SyncStatusReq", "SetMessage", "SyncStatusReq"]
    assert wire.requests[1].find("SetMessage/TurnOn").text == "off"
    assert wire.requests[1].find("SetMessage/SetTemp").text == "78.8"
    assert hass.states.get(entity_id).state == "off"


async def test_metric_setpoint_and_mode_use_one_transaction(hass, entry, wire):
    hass.config.units = METRIC_SYSTEM
    entity_id = await setup(hass, entry, wire)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity_id, "temperature": 23.01, "hvac_mode": "cool"},
        blocking=True,
    )
    assert wire.message_ids == ["SyncStatusReq", "SetMessage", "SyncStatusReq"]
    payload = wire.requests[1].find("SetMessage")
    assert payload.find("SetTemp").text == "73.42"
    assert payload.find("TurnOn").text == "on"
    assert payload.find("BaseMode").text == "cool"
    assert hass.states.get(entity_id).state == "cool"
    assert hass.states.get(entity_id).attributes["temperature"] == 23


async def test_unverified_hardware_rounding_is_not_false_confirmation(
    hass, entry, wire, monkeypatch
):
    monkeypatch.setattr(
        "custom_components.tfiac.api.COMMAND_CONFIRMATION_TIMEOUT", 0.05
    )
    hass.config.units = METRIC_SYSTEM
    entity_id = await setup(hass, entry, wire)
    send = wire.send

    async def rounded_device(message):
        response = await send(message)
        if ET.fromstring(message).get("msgid") == "SetMessage":
            wire.root.find("statusUpdateMsg/SetTemp").text = "79"
        return response

    entry.runtime_data.client._send = rounded_device
    with pytest.raises(HomeAssistantError, match="did not confirm"):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": entity_id, "temperature": 26},
            blocking=True,
        )
    assert wire.message_ids == ["SyncStatusReq", "SetMessage", "SyncStatusReq"]
    assert entry.runtime_data.data.target_temperature == 79
    assert hass.states.get(entity_id).attributes["temperature"] == 26.1
    assert entry.runtime_data.last_update_success


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
    entity_id = er.async_get(hass).async_get_entity_id(
        "climate", "tfiac", entry.entry_id
    )
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
        ("set_preset_mode", {"preset_mode": "boost"}, "preset_mode", "boost"),
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
    if data.get("preset_mode") == "boost":
        wire.root.find("statusUpdateMsg/BaseMode").text = "cool"
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


@pytest.mark.parametrize("initial", ["none", "sleep", "boost"])
@pytest.mark.parametrize("preset", ["none", "sleep", "boost"])
@pytest.mark.parametrize("power", ["off", "on"])
async def test_preset_transitions_are_one_write_with_distinct_encodings(
    hass, entry, wire, initial, preset, power
):
    status = wire.root.find("statusUpdateMsg")
    status.find("TurnOn").text = power
    status.find("BaseMode").text = "cool"
    status.find("Opt_sleepMode").text = (
        "sleepMode1:0:0" if initial == "sleep" else "off"
    )
    status.find("Opt_super").text = "on" if initial == "boost" else "off"
    entity_id = await setup(hass, entry, wire)
    assert hass.states.get(entity_id).attributes["preset_modes"] == [
        "none",
        "sleep",
        "boost",
    ]
    # A remote changes core settings after the cached HA snapshot.
    status.find("WindSpeed").text = "High"
    status.find("SetTemp").text = "79"
    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": entity_id, "preset_mode": preset},
        blocking=True,
    )
    actual = hass.states.get(entity_id)
    assert actual.attributes["preset_mode"] == preset
    assert actual.state == ("off" if power == "off" else "cool")
    assert actual.attributes["fan_mode"] == "high"
    assert actual.attributes["temperature"] == (61 if preset == "boost" else 79)
    if initial == preset and preset != "boost":
        assert wire.message_ids == ["SyncStatusReq"]
        return
    assert wire.message_ids == ["SyncStatusReq", "SetMessage", "SyncStatusReq"]
    fields = {node.tag: node.text for node in wire.requests[1].find("SetMessage")}
    assert fields["Opt_super"] == ("on" if preset == "boost" else "off")
    assert (
        fields["Opt_sleepMode"].startswith("sleepMode1:")
        if preset == "sleep"
        else fields["Opt_sleepMode"] == "off"
    )
    assert fields["TurnOn"] == power
    assert fields["WindSpeed"] == "High"
    assert fields["SetTemp"] == ("60.8" if preset == "boost" else "79.0")
    assert set(fields) == {
        "TurnOn",
        "BaseMode",
        "SetTemp",
        "WindSpeed",
        "Opt_sleepMode",
        "Opt_super",
    }


@pytest.mark.parametrize(
    "preset,ignored",
    [
        ("sleep", "Opt_super"),
        ("boost", "Opt_sleepMode"),
        ("none", "Opt_super"),
        ("boost", "SetTemp"),
    ],
)
async def test_preset_partial_application_is_not_success(
    hass, entry, wire, monkeypatch, preset, ignored
):
    monkeypatch.setattr(
        "custom_components.tfiac.api.COMMAND_CONFIRMATION_TIMEOUT", 0.03
    )
    monkeypatch.setattr("custom_components.tfiac.api.COMMAND_CONFIRMATION_INTERVAL", 1)
    status = wire.root.find("statusUpdateMsg")
    status.find("Opt_super").text = "off" if preset == "boost" else "on"
    status.find("BaseMode").text = "cool"
    status.find("Opt_sleepMode").text = "sleepMode1:0:0" if preset == "boost" else "off"
    entity_id = await setup(hass, entry, wire)
    old_send = wire.send

    async def partially_apply(message):
        request = ET.fromstring(message)
        if request.get("msgid") == "SetMessage":
            node = request.find(f"SetMessage/{ignored}")
            request.find("SetMessage").remove(node)
            return await old_send(ET.tostring(request))
        return await old_send(message)

    entry.runtime_data.client._send = partially_apply
    with pytest.raises(HomeAssistantError, match="did not confirm"):
        await hass.services.async_call(
            "climate",
            "set_preset_mode",
            {"entity_id": entity_id, "preset_mode": preset},
            blocking=True,
        )
    assert entry.runtime_data.last_update_success
    assert sum(request.get("msgid") == "SetMessage" for request in wire.requests) == 1
    actual = hass.states.get(entity_id)
    if ignored == "SetTemp":
        assert actual.attributes["preset_mode"] == preset
        assert actual.attributes["temperature"] != 61
    else:
        assert actual.attributes["preset_mode"] != preset


@pytest.mark.parametrize(
    "mode,native,displayed", [("cool", 60.8, 16), ("heat", 87.8, 31)]
)
@pytest.mark.parametrize("already_boost", [False, True])
async def test_boost_confirms_documented_temperature_and_flags(
    hass, entry, wire, mode, native, displayed, already_boost
):
    hass.config.units = METRIC_SYSTEM
    status = wire.root.find("statusUpdateMsg")
    status.find("BaseMode").text = mode
    status.find("Opt_super").text = "on" if already_boost else "off"
    entity_id = await setup(hass, entry, wire)
    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": entity_id, "preset_mode": "boost"},
        blocking=True,
    )
    assert wire.message_ids == ["SyncStatusReq", "SetMessage", "SyncStatusReq"]
    payload = wire.requests[1].find("SetMessage")
    assert payload.find("SetTemp").text == str(native)
    assert payload.find("BaseMode").text == mode
    assert payload.find("WindSpeed").text == "Low"
    assert hass.states.get(entity_id).attributes["temperature"] == displayed
    assert hass.states.get(entity_id).attributes["preset_mode"] == "boost"
    wire.requests.clear()
    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": entity_id, "preset_mode": "boost"},
        blocking=True,
    )
    assert wire.message_ids == ["SyncStatusReq"]


@pytest.mark.parametrize("intent", ["boost", "temperature"])
async def test_fan_only_fresh_status_blocks_cached_cool_command(
    hass, entry, wire, intent
):
    status = wire.root.find("statusUpdateMsg")
    status.find("BaseMode").text = "cool"
    entity_id = await setup(hass, entry, wire)
    status.find("BaseMode").text = "fan"
    service, data = (
        ("set_preset_mode", {"preset_mode": "boost"})
        if intent == "boost"
        else ("set_temperature", {"temperature": 73})
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "climate", service, {"entity_id": entity_id, **data}, blocking=True
        )
    assert wire.message_ids == ["SyncStatusReq"]
    assert entry.runtime_data.last_update_success


async def test_fan_only_to_cool_temperature_is_one_explicit_command(hass, entry, wire):
    wire.root.find("statusUpdateMsg/BaseMode").text = "fan"
    entity_id = await setup(hass, entry, wire)
    assert hass.states.get(entity_id).attributes["temperature"] is None
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity_id, "hvac_mode": "cool", "temperature": 73},
        blocking=True,
    )
    assert wire.message_ids == ["SyncStatusReq", "SetMessage", "SyncStatusReq"]
    assert hass.states.get(entity_id).attributes["temperature"] == 73
    assert hass.states.get(entity_id).state == "cool"


async def test_concurrent_presets_and_polling_do_not_mix_flags(hass, entry, wire):
    wire.root.find("statusUpdateMsg/BaseMode").text = "cool"
    entity_id = await setup(hass, entry, wire)
    await asyncio.wait_for(
        asyncio.gather(
            hass.services.async_call(
                "climate",
                "set_preset_mode",
                {"entity_id": entity_id, "preset_mode": "sleep"},
                blocking=True,
            ),
            hass.services.async_call(
                "climate",
                "set_preset_mode",
                {"entity_id": entity_id, "preset_mode": "boost"},
                blocking=True,
            ),
            entry.runtime_data.async_refresh(),
        ),
        timeout=1,
    )
    writes = [
        request.find("SetMessage")
        for request in wire.requests
        if request.get("msgid") == "SetMessage"
    ]
    assert len(writes) == 2
    for payload in writes:
        assert not (
            payload.find("Opt_super").text == "on"
            and payload.find("Opt_sleepMode").text != "off"
        )
    assert entry.runtime_data.data.preset in ("sleep", "boost")


@pytest.mark.parametrize(
    "preset,missing", [("sleep", "Opt_super"), ("boost", "Opt_sleepMode")]
)
async def test_lost_counterpart_status_blocks_preset_before_write(
    hass, entry, wire, preset, missing
):
    wire.root.find("statusUpdateMsg/BaseMode").text = "cool"
    entity_id = await setup(hass, entry, wire)
    status = wire.root.find("statusUpdateMsg")
    status.remove(status.find(missing))
    with pytest.raises(ServiceValidationError, match="usable preset status"):
        await hass.services.async_call(
            "climate",
            "set_preset_mode",
            {"entity_id": entity_id, "preset_mode": preset},
            blocking=True,
        )
    assert wire.message_ids == ["SyncStatusReq"]
    assert entry.runtime_data.last_update_success


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


async def test_ack_does_not_publish_unapplied_change(hass, entry, wire, monkeypatch):
    monkeypatch.setattr(
        "custom_components.tfiac.api.COMMAND_CONFIRMATION_TIMEOUT", 0.05
    )
    entity_id = await setup(hass, entry, wire)
    wire.apply_writes = False
    send = wire.send

    async def ignored_command(message):
        response = await send(message)
        if ET.fromstring(message).get("msgid") == "SetMessage":
            wire.root.find("statusUpdateMsg/WindSpeed").text = "High"
        return response

    entry.runtime_data.client._send = ignored_command
    with pytest.raises(HomeAssistantError, match="did not confirm"):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": entity_id, "temperature": 78},
            blocking=True,
        )
    assert wire.message_ids == ["SyncStatusReq", "SetMessage", "SyncStatusReq"]
    assert hass.states.get(entity_id).attributes["temperature"] == 77
    assert hass.states.get(entity_id).attributes["fan_mode"] == "high"
    assert hass.states.get(entity_id).state != "unavailable"
    assert entry.runtime_data.last_update_success


@pytest.mark.parametrize("sleep", [False, True])
async def test_service_waits_for_delayed_actual_state(
    hass, entry, wire, monkeypatch, sleep
):
    monkeypatch.setattr("custom_components.tfiac.api.COMMAND_CONFIRMATION_INTERVAL", 0)
    wire.root.find("statusUpdateMsg/WindSpeed").text = "Middle"
    entity_id = await setup(hass, entry, wire)
    before = ET.tostring(wire.root)
    send = wire.send
    remaining = 0

    async def delayed_command(message):
        nonlocal remaining
        request = ET.fromstring(message)
        response = await send(message)
        if request.get("msgid") == "SetMessage":
            remaining = 2
            if sleep:
                wire.root.find(
                    "statusUpdateMsg/Opt_sleepMode"
                ).text = "sleepMode1:0:0:0:0:0:0:0:0:0:0"
                wire.root.find("statusUpdateMsg/WindSpeed").text = "Auto"
            return response
        if remaining:
            remaining -= 1
            # No desired snapshot is published while waiting.
            assert hass.states.get(entity_id).attributes["temperature"] == 77
            assert hass.states.get(entity_id).attributes["preset_mode"] == "none"
            return before
        return response

    entry.runtime_data.client._send = delayed_command
    await hass.services.async_call(
        "climate",
        "set_preset_mode" if sleep else "set_temperature",
        {
            "entity_id": entity_id,
            **({"preset_mode": "sleep"} if sleep else {"temperature": 78}),
        },
        blocking=True,
    )
    assert wire.message_ids == ["SyncStatusReq", "SetMessage"] + ["SyncStatusReq"] * 3
    actual = hass.states.get(entity_id)
    assert actual.attributes["temperature"] == (77 if sleep else 78)
    assert actual.attributes["preset_mode"] == ("sleep" if sleep else "none")
    assert actual.attributes["fan_mode"] == ("auto" if sleep else "middle")


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
    assert len(registry_entries) == 2
    assert {
        item.entity_id for item in registry_entries if item.domain == "climate"
    } == {entity_id}
    assert hass.states.get(entity_id).state == "auto"
