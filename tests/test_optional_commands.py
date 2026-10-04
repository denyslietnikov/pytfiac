"""Explicit experimental wire contracts, without Home Assistant or hardware."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from xml.etree import ElementTree as ET

import pytest

FIELDS = (
    ("display", "Opt_display"),
    ("beep", "BeepEnable"),
)


def response_with_flags(status_response):
    root = ET.fromstring(status_response)
    status = root.find("statusUpdateMsg")
    for tag in ("Opt_ECO", "Opt_super", "Opt_display", "BeepEnable"):
        node = status.find(tag)
        if node is None:
            node = ET.SubElement(status, tag)
        node.text = "off"
    return ET.tostring(root)


@pytest.mark.parametrize("field,tag", FIELDS)
@pytest.mark.parametrize("enabled", [False, True])
def test_exact_optional_payload(protocol, state, field, tag, enabled):
    current = replace(state, eco=True, turbo=True, display=True, beep=True)
    changes = protocol.models.TfiacChanges(**{field: enabled})
    desired = protocol.models.apply_changes(current, changes)
    payload = ET.fromstring(
        protocol.api.build_optional_message(
            desired,
            changes,
            protocol.models.CommandProfile.LEGACY_EXPERIMENTAL,
            "123",
        )
    ).find("SetMessage")
    expected = {
        "TurnOn": str(current.power),
        "BaseMode": str(current.operation),
        "SetTemp": str(current.target_temperature),
        "WindSpeed": str(current.fan),
        "Opt_sleepMode": current.sleep,
        tag: "on" if enabled else "off",
    }
    assert {node.tag: node.text for node in payload} == expected
    for other, _ in FIELDS:
        if other != field:
            assert getattr(desired, other) == getattr(current, other)
    assert desired.power == current.power
    assert desired.fan == current.fan
    assert desired.sleep == current.sleep


@pytest.mark.parametrize("field,tag", FIELDS)
def test_default_profile_blocks_optional_before_io(protocol, field, tag):
    async def scenario():
        client = protocol.api.TfiacClient("192.0.2.1")
        client._send = AsyncMock()
        with pytest.raises(ValueError, match="enabled profile"):
            await client.async_apply_changes(
                protocol.models.TfiacChanges(**{field: True})
            )
        client._send.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("field,tag", FIELDS)
@pytest.mark.parametrize("value", [None, "on", 1])
def test_invalid_or_missing_status_cannot_be_written(
    protocol, state, field, tag, value
):
    if value is None:
        state = replace(state, **{field: None})
        requested = True
    else:
        state = replace(state, **{field: False})
        requested = value
    with pytest.raises(ValueError):
        protocol.models.apply_changes(
            state, protocol.models.TfiacChanges(**{field: requested})
        )


@pytest.mark.parametrize(
    "extra",
    [{"beep": True}, {"preset": "sleep"}, {"power": "on"}, {"swing_vertical": True}],
)
def test_optional_mixed_intent_rejected_before_io(protocol, extra):
    async def scenario():
        client = protocol.api.TfiacClient(
            "192.0.2.1",
            command_profile=protocol.models.CommandProfile.LEGACY_EXPERIMENTAL,
        )
        client._send = AsyncMock()
        with pytest.raises(ValueError):
            await client.async_apply_changes(
                protocol.models.TfiacChanges(display=True, **extra)
            )
        client._send.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("field,tag", FIELDS)
def test_optional_ack_is_not_status(protocol, status_response, field, tag, monkeypatch):
    monkeypatch.setattr(protocol.api, "COMMAND_CONFIRMATION_TIMEOUT", 0.05)

    async def scenario():
        response = response_with_flags(status_response)
        client = protocol.api.TfiacClient(
            "192.0.2.1",
            command_profile=protocol.models.CommandProfile.LEGACY_EXPERIMENTAL,
        )
        client._send = AsyncMock(
            side_effect=[response, b"<msg><SetMessage /></msg>", response]
        )
        with pytest.raises(protocol.api.TfiacCommandNotConfirmedError) as error:
            await client.async_apply_changes(
                protocol.models.TfiacChanges(**{field: True})
            )
        assert getattr(error.value.last_state, field) is False
        assert client._send.await_count == 3

    asyncio.run(scenario())


def test_optional_noop_is_read_only(protocol, status_response):
    async def scenario():
        client = protocol.api.TfiacClient(
            "192.0.2.1",
            command_profile=protocol.models.CommandProfile.LEGACY_EXPERIMENTAL,
        )
        client._send = AsyncMock(return_value=response_with_flags(status_response))
        await client.async_apply_changes(protocol.models.TfiacChanges(display=False))
        client._send.assert_awaited_once()

    asyncio.run(scenario())


def test_invalid_profile_fails_closed(protocol):
    with pytest.raises(ValueError):
        protocol.api.TfiacClient("192.0.2.1", command_profile="auto_guess")


@pytest.mark.parametrize(
    "profile,changes",
    [
        ("disabled", {"display": True}),
        ("legacy_experimental", {}),
        ("legacy_experimental", {"display": True, "beep": True}),
    ],
)
def test_builder_requires_explicit_single_flag(protocol, state, profile, changes):
    with pytest.raises(ValueError):
        protocol.api.build_optional_message(
            state,
            protocol.models.TfiacChanges(**changes),
            protocol.models.CommandProfile(profile),
            "123",
        )


def test_missing_optional_status_after_refresh_blocks_write(protocol, status_response):
    async def scenario():
        client = protocol.api.TfiacClient(
            "192.0.2.1",
            command_profile=protocol.models.CommandProfile.LEGACY_EXPERIMENTAL,
        )
        client._send = AsyncMock(return_value=status_response)
        with pytest.raises(ValueError):
            await client.async_apply_changes(protocol.models.TfiacChanges(display=True))
        client._send.assert_awaited_once()

    asyncio.run(scenario())
