"""Release metadata tests."""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_json(path: str) -> dict:
    """Load a JSON file relative to the repository root."""
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_manifest_is_development_candidate() -> None:
    """The Home Assistant manifest contains the HACS-required metadata."""
    manifest = load_json("custom_components/tfiac/manifest.json")

    assert manifest["domain"] == "tfiac"
    assert manifest["version"] == "0.7.0b1"
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


def test_local_brand_icon_is_valid_square_png() -> None:
    """The integration ships a complete 256px PNG for HACS/HA local brands."""
    png = (ROOT / "custom_components/tfiac/brand/icon.png").read_bytes()

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert png[12:16] == b"IHDR"
    assert struct.unpack(">II", png[16:24]) == (256, 256)

    offset = 8
    chunks = []
    while offset < len(png):
        length = struct.unpack(">I", png[offset : offset + 4])[0]
        chunk_end = offset + 8 + length
        chunk = png[offset + 4 : chunk_end]
        checksum = struct.unpack(">I", png[chunk_end : chunk_end + 4])[0]
        assert zlib.crc32(chunk) == checksum
        chunks.append(chunk[:4])
        offset = chunk_end + 4

    assert offset == len(png)
    assert b"IDAT" in chunks
    assert chunks[-1] == b"IEND"


def test_english_config_flow_translation_is_complete() -> None:
    """Custom integrations ship direct translations instead of strings.json."""
    translation = load_json("custom_components/tfiac/translations/en.json")
    config = translation["config"]

    assert {"user", "reconfigure"} <= config["step"].keys()
    assert "cannot_connect" in config["error"]
    assert {"already_configured", "reconfigure_successful"} <= config["abort"].keys()
