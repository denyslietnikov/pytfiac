"""Shared fixtures for TFIAC characterization tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="session")
def protocol() -> ModuleType:
    """Load the bundled protocol client without importing Home Assistant."""
    module_path = ROOT / "custom_components" / "tfiac" / "pytfiac.py"
    spec = importlib.util.spec_from_file_location("tfiac_protocol_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def status_response() -> bytes:
    """Return the status response captured by the legacy test server."""
    return (ROOT / "tests" / "fixtures" / "status.xml").read_bytes()
