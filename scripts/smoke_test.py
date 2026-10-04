"""Explicit, standalone hardware smoke test using the bundled protocol client."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import math
import os
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MODE_NAMES = {
    "cool": "COOL",
    "heat": "HEAT",
    "auto": "AUTO",
    "dry": "DRY",
    "fan_only": "FAN",
}
CONTROL_FIELDS = (
    "power",
    "operation",
    "target_temperature",
    "fan",
    "swing_horizontal",
    "swing_vertical",
    "sleep",
    "turbo",
    "display",
    "beep",
    "eco",
    "degree_half",
)


def load_protocol():
    """Load exactly the bundled client without executing HA's __init__.py."""
    name = "tfiac_smoke_protocol"
    if name not in sys.modules:
        package = ModuleType(name)
        package.__path__ = [str(ROOT / "custom_components" / "tfiac")]
        sys.modules[name] = package
    return SimpleNamespace(
        models=importlib.import_module(f"{name}.models"),
        api=importlib.import_module(f"{name}.api"),
    )


def snapshot(state):
    """No host, device name, raw XML, firmware profiles or arbitrary values."""
    result = {field: getattr(state, field) for field in CONTROL_FIELDS}
    result["sleep"] = None if state.sleep is None else state.sleep != "off"
    result.update(
        current_temperature=state.current_temperature,
        outdoor_temperature=state.outdoor_temperature,
        degree_half=state.degree_half,
    )
    return result


class SmokeHarness:
    """Stop testing on first failure, but attempt all restoration steps."""

    def __init__(self, protocol, client, options, report, persist=lambda: None):
        self.p = protocol
        self.client = client
        self.options = options
        self.report = report
        self.persist = persist

    def save(self, *, strict=False):
        """A report disk failure must never prevent restoration writes."""
        try:
            self.persist()
        except OSError as err:
            self.report["report_error"] = type(err).__name__
            if strict:
                raise
            print("Cannot persist report; restoration continues", file=sys.stderr)

    async def command(self, label, changes, *, restoring=False):
        event = {
            "label": label,
            "phase": "restore" if restoring else "test",
            "requested": asdict(changes),
            "time": datetime.now(UTC).isoformat(),
        }
        self.report["events"].append(event)
        # Persist the intent before a potentially applied but unacknowledged write.
        self.save(strict=not restoring)
        started = perf_counter()
        try:
            state = await self.client.async_apply_changes(changes)
            event.update(result="confirmed", state=snapshot(state))
            return state
        except asyncio.CancelledError:
            event["result"] = "interrupted"
            raise
        except Exception as err:
            event.update(result="failed", error=type(err).__name__)
            if hasattr(err, "last_state"):
                event["state"] = snapshot(err.last_state)
            if not restoring:
                raise
        finally:
            event["elapsed_seconds"] = round(perf_counter() - started, 3)
            self.save()
            print(f"{event['phase']}: {label}: {event['result']}", flush=True)

    def preflight(self, initial):
        m = self.p.models
        m.normalize_target_temperature(initial.target_temperature)
        if (
            initial.sleep is not None or initial.turbo is not None
        ) and initial.preset is None:
            raise ValueError("Conflicting initial presets; cannot safely restore")
        if initial.preset is not None:
            # Refuse a baseline whose preset cannot be restored in its operation.
            m.apply_changes(initial, m.TfiacChanges(preset=initial.preset))
        if initial.eco is True:
            raise ValueError("Active Eco has no restoration writer; refusing writes")
        delta = self.options.temperature_step
        for value in (
            initial.target_temperature + delta,
            initial.target_temperature - delta,
        ):
            if m.MIN_TEMP <= value <= m.MAX_TEMP:
                target = m.normalize_target_temperature(value)
                if target != initial.target_temperature:
                    return target
        raise ValueError("Temperature step produces no usable in-range test target")

    async def exercise(self, initial, target):
        m = self.p.models
        change = m.TfiacChanges
        await self.command(
            "Cool for temperature and optional controls",
            change(operation=m.Operation.COOL),
        )
        if initial.preset is not None:
            await self.command("clear presets", change(preset=m.Preset.NONE))
        await self.command("power off", change(power=m.Power.OFF))
        await self.command("power on", change(power=m.Power.ON))
        await self.command(
            "target temperature (native Fahrenheit)", change(target_temperature=target)
        )
        for fan in m.Fan:
            await self.command(f"fan {fan.value}", change(fan=fan))
        for field in ("swing_vertical", "swing_horizontal"):
            if getattr(initial, field) is None:
                self.report["skipped"].append(field)
                continue
            for enabled in (True, False):
                await self.command(f"{field} {enabled}", change(**{field: enabled}))
        for field in self.options.optional:
            if getattr(initial, field) is None:
                self.report["skipped"].append(field)
                continue
            for enabled in (False, True):
                await self.command(
                    f"{field} {enabled} (running Cool)", change(**{field: enabled})
                )
        presets = []
        if initial.sleep is not None:
            presets.append(m.Preset.SLEEP)
        else:
            self.report["skipped"].append("sleep")
        if initial.turbo is not None:
            presets.append(m.Preset.BOOST)
        else:
            self.report["skipped"].append("boost")
        if presets:
            # Exercise Sleep -> Boost -> Sleep -> None when both are reported.
            for preset in [*presets, *presets[:1], m.Preset.NONE]:
                await self.command(f"preset {preset.value}", change(preset=preset))
        for mode in self.options.modes:
            if mode != "cool":
                await self.command(
                    f"mode {mode}",
                    change(operation=getattr(m.Operation, MODE_NAMES[mode])),
                )

    async def restore(self, initial):
        m = self.p.models
        change = m.TfiacChanges
        try:
            # A failed write can have been applied: do not trust the last event.
            if initial.preset is not None:
                await self.command(
                    "clear presets", change(preset=m.Preset.NONE), restoring=True
                )
            # Livingroom ignores setpoint changes in standby/Fan-only. Restore in
            # active Cool, then restore the original operation and finally power.
            await self.command(
                "restore target in Cool",
                change(
                    operation=m.Operation.COOL,
                    target_temperature=initial.target_temperature,
                ),
                restoring=True,
            )
            for field in self.options.optional:
                if getattr(initial, field) is not None:
                    await self.command(
                        f"original {field}",
                        change(**{field: getattr(initial, field)}),
                        restoring=True,
                    )
            await self.command(
                "original operation",
                change(operation=initial.operation),
                restoring=True,
            )
            swing = {
                field: getattr(initial, field)
                for field in ("swing_vertical", "swing_horizontal")
                if getattr(initial, field) is not None
            }
            if swing:
                await self.command("original swing", change(**swing), restoring=True)
            await self.command("original fan", change(fan=initial.fan), restoring=True)
            if initial.preset is not None:
                await self.command(
                    "original preset", change(preset=initial.preset), restoring=True
                )
        finally:
            # Attempt original power even if another restoration operation fails.
            await self.command(
                "original power", change(power=initial.power), restoring=True
            )
            try:
                final = await self.client.async_update()
                self.report["final"] = snapshot(final)
                self.report["restore_mismatches"] = [
                    field
                    for field in CONTROL_FIELDS
                    if self.report["initial"][field] is not None
                    and self.report["initial"][field] != self.report["final"][field]
                ]
                self.report["restored"] = not self.report["restore_mismatches"]
            except Exception as err:
                self.report.update(
                    restored=False, restore_read_error=type(err).__name__
                )
            self.save()

    async def run(self):
        initial = await self.client.async_update()
        self.report["initial"] = snapshot(initial)
        self.report["capabilities"] = asdict(initial.capabilities)
        self.save(strict=True)
        if not self.options.write:
            self.report["result"] = "read_only"
            return
        try:
            target = self.preflight(initial)
        except ValueError as err:
            self.report.update(result="failed", preflight_error=str(err))
            self.save()
            raise
        self.report["result"] = "running"
        try:
            await self.exercise(initial, target)
            self.report["result"] = "passed"
        except asyncio.CancelledError:
            self.report["result"] = "interrupted"
            raise
        except Exception as err:
            self.report.update(result="failed", error=type(err).__name__)
        finally:
            # First Ctrl-C cancels the test, not its restoration transaction.
            task = asyncio.create_task(self.restore(initial))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            if (
                self.report.get("report_error")
                or not self.report["restored"]
                or any(
                    event["phase"] == "restore" and event["result"] != "confirmed"
                    for event in self.report["events"]
                )
            ):
                self.report["result"] = "failed"
            self.save()


def parse_args(argv=None, *, environ=None):
    env = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="New JSON report; existing files are never overwritten",
    )
    parser.add_argument(
        "--write", action="store_true", help="Allow AC writes (default is read-only)"
    )
    parser.add_argument(
        "--confirm-host", help="Repeat TFIAC_HOST to acknowledge the write target"
    )
    parser.add_argument(
        "--exclusive-control",
        action="store_true",
        help="Confirm HA/Homebridge/other controllers are stopped",
    )
    parser.add_argument(
        "--candidate", help="Expected manifest version; required for writes"
    )
    parser.add_argument(
        "--optional",
        nargs="+",
        choices=("display", "beep"),
        default=[],
        help="Explicitly include Display/Beep hardware checks",
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=tuple(MODE_NAMES),
        default=["cool"],
        help="Extra mode changes are opt-in; Cool is always exercised",
    )
    parser.add_argument(
        "--temperature-step",
        type=float,
        default=1,
        help="Native Fahrenheit test delta, not a claim about hardware resolution",
    )
    args = parser.parse_args(argv)
    args.host = env.get("TFIAC_HOST", "").strip()
    if not args.host:
        parser.error("Set TFIAC_HOST explicitly; no discovery or default device")
    if not math.isfinite(args.temperature_step) or not 0 < args.temperature_step <= 27:
        parser.error("--temperature-step must be finite and in (0, 27] °F")
    args.version = json.loads(
        (ROOT / "custom_components/tfiac/manifest.json").read_text()
    )["version"]
    if args.candidate is not None and args.candidate != args.version:
        parser.error("--candidate does not match this checkout's manifest version")
    if args.write and (
        args.confirm_host != args.host
        or not args.exclusive_control
        or args.candidate is None
    ):
        parser.error(
            "Writes require matching --confirm-host, --exclusive-control and --candidate"
        )
    return args


def main(argv=None):
    args = parse_args(argv)
    source_hash = hashlib.sha256()
    for relative in (
        "scripts/smoke_test.py",
        "custom_components/tfiac/api.py",
        "custom_components/tfiac/models.py",
        "custom_components/tfiac/manifest.json",
    ):
        source_hash.update(relative.encode())
        source_hash.update((ROOT / relative).read_bytes())
    report = {
        "version": args.version,
        "source_sha256": source_hash.hexdigest(),
        "started": datetime.now(UTC).isoformat(),
        "result": "not_started",
        "events": [],
        "skipped": [],
        "options": {
            "write": args.write,
            "modes": args.modes,
            "optional": args.optional,
            "temperature_step_fahrenheit": args.temperature_step,
        },
        "limitations": [
            "Protocol confirmation is not proof of physical operation.",
            "Sleep restores enabled/disabled only, not a firmware profile or timer.",
            "Unknown/raw controls and Eco have no restoration writer.",
            "Restoration may temporarily enable Cool, even if originally off.",
            "No guarantee of restoration after power loss, SIGKILL or repeated Ctrl-C.",
        ],
    }
    # Exclusive creation also prevents accidental truncation of the saved baseline.
    try:
        fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as err:
        print(f"Cannot create report: {type(err).__name__}", file=sys.stderr)
        return 2
    with os.fdopen(fd, "w", encoding="utf-8") as output:

        def persist():
            output.seek(0)
            json.dump(report, output, indent=2, allow_nan=False)
            output.write("\n")
            output.truncate()
            output.flush()
            os.fsync(output.fileno())

        persist()
        protocol = load_protocol()
        client = protocol.api.TfiacClient(args.host)
        try:
            asyncio.run(SmokeHarness(protocol, client, args, report, persist).run())
        except (KeyboardInterrupt, asyncio.CancelledError):
            report["result"] = "interrupted"
        except Exception as err:
            report.update(result="failed", error=type(err).__name__)
        finally:
            report["finished"] = datetime.now(UTC).isoformat()
            persist()
    print(
        f"Result: {report['result']}; restored: {report.get('restored', 'not needed')}; report: {args.report}"
    )
    if report["result"] == "interrupted":
        return 130
    return 0 if report["result"] in ("passed", "read_only") else 1


if __name__ == "__main__":
    sys.exit(main())
