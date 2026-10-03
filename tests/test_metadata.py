"""Release metadata tests."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_json(path: str) -> dict:
    """Load a JSON file relative to the repository root."""
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_manifest_is_development_candidate() -> None:
    """The Home Assistant manifest contains the HACS-required metadata."""
    manifest = load_json("custom_components/tfiac/manifest.json")

    assert manifest["domain"] == "tfiac"
    assert manifest["version"] == "0.6.0b1"
    assert manifest["codeowners"] == ["@denyslietnikov"]
    assert manifest["documentation"].startswith("https://github.com/")
    assert manifest["issue_tracker"].endswith("/issues")
    assert manifest["requirements"] == []


def test_hacs_metadata_does_not_duplicate_manifest() -> None:
    """hacs.json contains only supported repository-level settings."""
    hacs = load_json("hacs.json")

    assert hacs == {
        "name": "TFIAC",
        "homeassistant": "2026.9.4",
        "hide_default_branch": True,
    }


def test_english_config_flow_translation_is_complete() -> None:
    """Custom integrations ship direct translations instead of strings.json."""
    translation = load_json("custom_components/tfiac/translations/en.json")
    config = translation["config"]

    assert {"user", "reconfigure"} <= config["step"].keys()
    assert "cannot_connect" in config["error"]
    assert {"already_configured", "reconfigure_successful"} <= config["abort"].keys()
