"""Hardware harness safety tests; never access a physical AC or network."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest

from scripts import smoke_test as smoke

VERSION = json.loads(
    (smoke.ROOT / "custom_components/tfiac/manifest.json").read_text()
)["version"]


@pytest.fixture
def rig():
    p = smoke.load_protocol()
    m = p.models
    initial = m.TfiacState(
        power=m.Power.OFF,
        operation=m.Operation.FAN,
        target_temperature=77,
        fan=m.Fan.MIDDLE,
        sleep="off",
        turbo=False,
        eco=False,
        swing_vertical=True,
        swing_horizontal=False,
        display=True,
        beep=False,
        name="private device",
        raw_fields=(("private", "secret"),),
        current_temperature=70,
    )

    class Client:
        def __init__(self):
            self.state = initial
            self.calls = []
            self.hook = None
            self.reads = 0
            self.read_error = None

        async def async_update(self):
            self.reads += 1
            if self.read_error and self.reads == self.read_error:
                raise p.api.TfiacTimeoutError("private host")
            return self.state

        async def async_apply_changes(self, changes):
            self.calls.append(changes)
            self.state = m.apply_changes(self.state, changes)
            if self.hook:
                await self.hook(changes)
            return self.state

    client = Client()
    options = SimpleNamespace(
        write=True, modes=["cool"], optional=[], temperature_step=1
    )
    report = {"events": [], "skipped": [], "result": "not_started"}
    harness = smoke.SmokeHarness(p, client, options, report)
    return SimpleNamespace(
        p=p,
        m=m,
        initial=initial,
        client=client,
        options=options,
        report=report,
        harness=harness,
    )


def test_read_only_default_does_not_write(rig):
    rig.options.write = False
    asyncio.run(rig.harness.run())
    assert rig.report["result"] == "read_only"
    assert rig.client.reads == 1
    assert rig.client.calls == []
    assert "restored" not in rig.report


def test_exercises_default_controls_and_restores_off_fan_state(rig):
    asyncio.run(rig.harness.run())
    assert rig.report["result"] == "passed"
    assert rig.report["restored"] is True
    assert rig.report["restore_mismatches"] == []
    assert rig.client.state == rig.initial
    tested = [event for event in rig.report["events"] if event["phase"] == "test"]
    assert [
        event["requested"]["preset"] for event in tested if event["requested"]["preset"]
    ] == ["none", "sleep", "boost", "sleep", "none"]
    assert {call.operation for call in rig.client.calls if call.operation} == {
        rig.m.Operation.COOL,
        rig.m.Operation.FAN,
    }
    assert all(call.display is None and call.beep is None for call in rig.client.calls)
    assert rig.client.calls[-1].power == rig.m.Power.OFF


def test_optional_controls_are_explicit_and_tested_and_restored_in_cool(rig):
    rig.options.optional = ["display", "beep"]
    rig.options.modes = ["heat", "auto", "dry", "fan_only"]
    observed = []

    async def hook(changes):
        if changes.display is not None or changes.beep is not None:
            observed.append((rig.client.state.power, rig.client.state.operation))

    rig.client.hook = hook
    asyncio.run(rig.harness.run())
    assert len(observed) == 6
    assert all(pair == (rig.m.Power.ON, rig.m.Operation.COOL) for pair in observed)
    assert rig.report["restored"]
    assert rig.report["result"] == "passed"


def test_missing_capabilities_skip_without_inventing_controls(rig):
    rig.client.state = replace(
        rig.initial,
        sleep=None,
        turbo=None,
        swing_horizontal=None,
        swing_vertical=None,
        display=None,
        beep=None,
    )
    rig.options.optional = ["display", "beep"]
    asyncio.run(rig.harness.run())
    assert set(rig.report["skipped"]) == {
        "sleep",
        "boost",
        "swing_horizontal",
        "swing_vertical",
        "display",
        "beep",
    }
    assert all(
        call.preset is None
        and call.swing_horizontal is None
        and call.swing_vertical is None
        and call.beep is None
        and call.display is None
        for call in rig.client.calls
    )
    assert rig.report["restored"]


@pytest.mark.parametrize("sleep,turbo", [(None, False), ("off", None)])
def test_partial_preset_capabilities(rig, sleep, turbo):
    rig.client.state = replace(rig.initial, sleep=sleep, turbo=turbo)
    asyncio.run(rig.harness.run())
    assert rig.report["result"] == "passed"
    assert rig.report["restored"]


@pytest.mark.parametrize(
    "temperature,step,expected", [(88, 1, 87), (61, 1, 62), (77, 0.5, 77.5)]
)
def test_temperature_target_is_bounded_native_fahrenheit(
    rig, temperature, step, expected
):
    rig.client.state = replace(rig.initial, target_temperature=temperature)
    rig.options.temperature_step = step
    assert rig.harness.preflight(rig.client.state) == expected


@pytest.mark.parametrize(
    "changes",
    [
        {"sleep": "sleepMode1", "turbo": True},
        {"eco": True},
        {"target_temperature": 100},
    ],
)
def test_unsafe_baseline_rejects_all_writes(rig, changes):
    rig.client.state = replace(rig.initial, **changes)
    with pytest.raises(ValueError):
        asyncio.run(rig.harness.run())
    assert rig.client.calls == []


def test_unusable_temperature_delta_rejects_before_writes(rig):
    rig.options.temperature_step = 0.00001
    with pytest.raises(ValueError, match="no usable"):
        asyncio.run(rig.harness.run())
    assert rig.client.calls == []


def test_applied_but_unacknowledged_write_stops_testing_and_restores(rig):
    async def hook(changes):
        if changes.target_temperature == 78:
            raise rig.p.api.TfiacTimeoutError("private host")

    rig.client.hook = hook
    asyncio.run(rig.harness.run())
    assert rig.report["result"] == "failed"
    assert rig.report["restored"]
    tested = [event for event in rig.report["events"] if event["phase"] == "test"]
    assert tested[-1]["result"] == "failed"
    assert tested[-1]["requested"]["target_temperature"] == 78
    assert rig.client.state == rig.initial


def test_restoration_failure_does_not_skip_original_power_or_final_read(rig):
    async def hook(changes):
        if rig.report["events"][-1]["label"] == "original fan":
            raise rig.p.api.TfiacTimeoutError()

    rig.client.hook = hook
    asyncio.run(rig.harness.run())
    assert rig.report["result"] == "failed"
    assert rig.client.calls[-1].power == rig.initial.power
    assert "final" in rig.report


def test_final_read_failure_is_not_reported_as_restored(rig):
    rig.client.read_error = 2
    asyncio.run(rig.harness.run())
    assert rig.report["result"] == "failed"
    assert rig.report["restored"] is False
    assert rig.report["restore_read_error"] == "TfiacTimeoutError"


@pytest.mark.parametrize("field", ["eco", "degree_half"])
def test_actual_final_mismatch_in_readonly_flag_fails(rig, field):
    rig.client.state = replace(rig.client.state, **{field: False})

    async def hook(changes):
        rig.client.state = replace(
            rig.client.state, **{field: True}, current_temperature=99
        )

    rig.client.hook = hook
    asyncio.run(rig.harness.run())
    assert rig.report["result"] == "failed"
    assert rig.report["restore_mismatches"] == [field]


def test_changed_ambient_reading_is_not_a_restoration_failure(rig):
    async def hook(changes):
        rig.client.state = replace(rig.client.state, current_temperature=99)

    rig.client.hook = hook
    asyncio.run(rig.harness.run())
    assert rig.report["result"] == "passed"


def test_cancellation_waits_for_restoration(rig):
    async def scenario():
        started = asyncio.Event()

        async def hook(changes):
            if changes.target_temperature == 78:
                started.set()
                await asyncio.Future()

        rig.client.hook = hook
        task = asyncio.create_task(rig.harness.run())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert rig.client.state == rig.initial
        assert rig.report["restored"]
        assert rig.report["result"] == "interrupted"

    asyncio.run(scenario())


def test_report_disk_failure_does_not_block_rollback(rig):
    def persist():
        if len(rig.client.calls) >= 4:
            raise OSError("disk full")

    rig.harness.persist = persist
    asyncio.run(rig.harness.run())
    assert rig.report["result"] == "failed"
    assert rig.report["restored"]
    assert rig.client.calls[-1].power == rig.initial.power


def test_initial_snapshot_must_be_saved_before_any_write(rig):
    rig.harness.persist = Mock(side_effect=OSError())
    with pytest.raises(OSError):
        asyncio.run(rig.harness.run())
    assert rig.client.calls == []


def test_report_does_not_expose_raw_or_identifying_values(rig):
    asyncio.run(rig.harness.run())
    serialized = json.dumps(rig.report)
    assert "private device" not in serialized
    assert "secret" not in serialized
    assert "raw_fields" not in serialized
    assert "sleepMode" not in serialized


@pytest.mark.parametrize(
    "extra",
    [
        [],
        ["--confirm-host", "192.0.2.1"],
        [
            "--confirm-host",
            "192.0.2.2",
            "--exclusive-control",
            "--candidate",
            VERSION,
        ],
        ["--confirm-host", "192.0.2.1", "--exclusive-control", "--candidate", "0.0.0"],
    ],
)
def test_cli_refuses_unconfirmed_writes(extra):
    with pytest.raises(SystemExit) as err:
        smoke.parse_args(
            ["--report", "/unused.json", "--write", *extra],
            environ={"TFIAC_HOST": "192.0.2.1"},
        )
    assert err.value.code == 2


@pytest.mark.parametrize("delta", ["nan", "inf", "-1", "0", "28"])
def test_cli_refuses_invalid_temperature_delta(delta):
    with pytest.raises(SystemExit):
        smoke.parse_args(
            ["--report", "/unused.json", "--temperature-step", delta],
            environ={"TFIAC_HOST": "192.0.2.1"},
        )


def test_cli_requires_explicit_host_and_validates_candidate():
    with pytest.raises(SystemExit):
        smoke.parse_args(["--report", "/unused.json"], environ={})
    args = smoke.parse_args(
        [
            "--report",
            "/unused.json",
            "--write",
            "--confirm-host",
            "192.0.2.1",
            "--exclusive-control",
            "--candidate",
            VERSION,
        ],
        environ={"TFIAC_HOST": "192.0.2.1"},
    )
    assert args.write and args.modes == ["cool"] and args.optional == []


def test_cli_readonly_report_and_existing_file_protection(rig, tmp_path, monkeypatch):
    monkeypatch.setenv("TFIAC_HOST", "192.0.2.1")
    monkeypatch.setattr(
        smoke,
        "load_protocol",
        lambda: SimpleNamespace(
            models=rig.m, api=SimpleNamespace(TfiacClient=lambda *a, **k: rig.client)
        ),
    )
    path = tmp_path / "report.json"
    assert smoke.main(["--report", str(path)]) == 0
    data = json.loads(path.read_text())
    assert data["result"] == "read_only"
    assert len(data["source_sha256"]) == 64
    assert path.stat().st_mode & 0o777 == 0o600
    original = path.read_bytes()
    assert smoke.main(["--report", str(path)]) == 2
    assert path.read_bytes() == original
    assert rig.client.reads == 1


@pytest.mark.parametrize("preset", ["sleep", "boost"])
def test_active_initial_preset_is_restored_semantically(rig, preset):
    rig.client.state = replace(
        rig.client.state,
        operation=rig.m.Operation.COOL,
        sleep="firmwareProfile:0" if preset == "sleep" else "off",
        turbo=preset == "boost",
    )
    asyncio.run(rig.harness.run())
    assert rig.report["result"] == "passed"
    assert rig.report["restored"]
    assert rig.client.state.preset == preset


def test_sleep_in_fan_only_refuses_test_before_writes(rig):
    rig.client.state = replace(
        rig.client.state,
        operation=rig.m.Operation.FAN,
        sleep="firmwareProfile:0",
        turbo=False,
    )
    with pytest.raises(ValueError, match="Sleep is not available in Fan Only"):
        asyncio.run(rig.harness.run())
    assert rig.report["result"] == "failed"
    assert "Sleep is not available in Fan Only" in rig.report["preflight_error"]
    assert not rig.client.calls


@pytest.mark.parametrize("failure", [None, "timeout", "cancel"])
def test_cli_write_exit_codes_and_durable_snapshot(rig, tmp_path, monkeypatch, failure):
    monkeypatch.setenv("TFIAC_HOST", "192.0.2.1")
    monkeypatch.setattr(
        smoke,
        "load_protocol",
        lambda: SimpleNamespace(
            models=rig.m, api=SimpleNamespace(TfiacClient=lambda *a, **k: rig.client)
        ),
    )

    def check_snapshot():
        assert json.loads(path.read_text())["initial"]["power"] == "off"

    async def hook(changes):
        check_snapshot()
        if changes.target_temperature == 78:
            if failure == "timeout":
                raise rig.p.api.TfiacTimeoutError("192.0.2.1")
            if failure == "cancel":
                raise asyncio.CancelledError()

    rig.client.hook = hook
    path = tmp_path / "report.json"
    result = smoke.main(
        [
            "--report",
            str(path),
            "--write",
            "--confirm-host",
            "192.0.2.1",
            "--exclusive-control",
            "--candidate",
            VERSION,
        ]
    )
    assert result == {None: 0, "timeout": 1, "cancel": 130}[failure]
    report = json.loads(path.read_text())
    assert report["restored"]
    assert "finished" in report
    assert "192.0.2.1" not in path.read_text()


def test_real_bundled_client_with_synthetic_xml_device(rig):
    p, m = rig.p, rig.m
    state = rig.initial
    client = p.api.TfiacClient("192.0.2.1")

    async def send(message):
        nonlocal state
        root = ET.fromstring(message)
        if root.get("msgid") == "SetMessage":
            fields = {node.tag: node.text for node in root.find("SetMessage")}
            updates = {}
            for tag, field, enum in [
                ("TurnOn", "power", m.Power),
                ("BaseMode", "operation", m.Operation),
                ("WindSpeed", "fan", m.Fan),
            ]:
                if tag in fields:
                    updates[field] = enum(fields[tag])
            for tag, field in [
                ("Opt_super", "turbo"),
                ("Opt_display", "display"),
                ("BeepEnable", "beep"),
                ("WindDirection_H", "swing_horizontal"),
                ("WindDirection_V", "swing_vertical"),
            ]:
                if tag in fields:
                    updates[field] = fields[tag] == "on"
            if "Opt_sleepMode" in fields:
                updates["sleep"] = (
                    "off" if fields["Opt_sleepMode"] == "off" else "sleepMode1:0:0"
                )
                if updates["sleep"] != "off":
                    updates["fan"] = m.Fan.AUTO
            next_state = replace(state, **updates)
            if (
                "SetTemp" in fields
                and next_state.power == m.Power.ON
                and next_state.operation == m.Operation.COOL
            ):
                next_state = replace(
                    next_state, target_temperature=float(fields["SetTemp"])
                )
            state = next_state
            return b'<msg msgid="ack" />'
        values = {
            "TurnOn": state.power,
            "BaseMode": state.operation,
            "WindSpeed": state.fan,
            "SetTemp": state.target_temperature,
            "Opt_sleepMode": state.sleep,
            "Opt_super": "on" if state.turbo else "off",
            "Opt_display": "on" if state.display else "off",
            "BeepEnable": "on" if state.beep else "off",
            "Opt_ECO": "off",
            "WindDirection_H": "on" if state.swing_horizontal else "off",
            "WindDirection_V": "on" if state.swing_vertical else "off",
            "IndoorTemp": 99,
        }
        envelope = ET.Element("msg")
        payload = ET.SubElement(envelope, "statusUpdateMsg")
        for tag, value in values.items():
            ET.SubElement(payload, tag).text = str(value)
        return ET.tostring(envelope)

    client._send = send
    rig.options.optional = ["display", "beep"]
    harness = smoke.SmokeHarness(p, client, rig.options, rig.report)
    asyncio.run(harness.run())
    assert rig.report["result"] == "passed"
    assert rig.report["restored"]
    assert rig.report["final"]["power"] == "off"
    assert rig.report["final"]["operation"] == "fan"
    assert rig.report["final"]["target_temperature"] == 77
