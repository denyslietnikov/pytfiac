"""Load pure protocol modules without importing Home Assistant."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="session")
def protocol():
    package_name = "tfiac_protocol_test"
    package = ModuleType(package_name)
    package.__path__ = [str(ROOT / "custom_components" / "tfiac")]
    sys.modules[package_name] = package
    loaded = {}
    for name in ("models", "api"):
        module_name = f"{package_name}.{name}"
        spec = importlib.util.spec_from_file_location(
            module_name, ROOT / "custom_components" / "tfiac" / f"{name}.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        loaded[name] = module
    return SimpleNamespace(**loaded)


@pytest.fixture(scope="session")
def status_response() -> bytes:
    """Legacy captured status; edited variants in tests are synthetic."""
    return (ROOT / "tests" / "fixtures" / "status.xml").read_bytes()


@pytest.fixture
def state(protocol, status_response):
    return protocol.api.parse_status(status_response)
