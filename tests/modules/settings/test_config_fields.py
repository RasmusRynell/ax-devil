"""Tests that a configuration field accepts, rejects and keeps environment references."""

from __future__ import annotations

from pathlib import Path

import pytest

from ax_devil.modules.application_shell.configuration_preferences import ConfigField


@pytest.mark.parametrize("resolved", [None, "not-a-number", "0", "65536"])
def test_numeric_environment_reference_must_resolve_to_a_number_in_range(
    monkeypatch: pytest.MonkeyPatch, resolved: str | None
) -> None:
    port = ConfigField("defaults.port", "Port", minimum=1, maximum=65535)
    monkeypatch.delenv("AX_TEST_PORT", raising=False)
    if resolved is not None:
        monkeypatch.setenv("AX_TEST_PORT", resolved)
    with pytest.raises(ValueError, match="Port"):
        port.parse("$AX_TEST_PORT")


def test_valid_numeric_environment_reference_is_kept_unresolved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AX_TEST_PORT", "65535")
    assert ConfigField("defaults.port", "Port", minimum=1, maximum=65535).parse(" $AX_TEST_PORT ") == "$AX_TEST_PORT"


@pytest.mark.parametrize("resolved", [None, "   "])
def test_folder_environment_reference_must_resolve_to_a_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, resolved: str | None
) -> None:
    folder = ConfigField("storage.cache_dir", "Cache folder", directory=True)
    monkeypatch.delenv("AX_TEST_FOLDER", raising=False)
    if resolved is not None:
        monkeypatch.setenv("AX_TEST_FOLDER", resolved)
    with pytest.raises(ValueError, match="non-empty folder path"):
        folder.parse("$AX_TEST_FOLDER")
    monkeypatch.setenv("AX_TEST_FOLDER", str(tmp_path))
    assert folder.parse("$AX_TEST_FOLDER") == "$AX_TEST_FOLDER"


def test_default_ip_range_must_name_addresses_or_a_small_range() -> None:
    ip_range = ConfigField("defaults.network_scan.ip_range", "Default IP range")

    assert ip_range.parse(" 192.168.1.0/24 ") == "192.168.1.0/24"
    assert ip_range.parse("") == ""
    with pytest.raises(ValueError, match="not an IPv4 address or range"):
        ip_range.parse("camera")
    with pytest.raises(ValueError, match="at most"):
        ip_range.parse("10.0.0.0/8")


def test_default_ip_range_environment_reference_must_resolve_to_a_valid_range(monkeypatch: pytest.MonkeyPatch) -> None:
    ip_range = ConfigField("defaults.network_scan.ip_range", "Default IP range")
    monkeypatch.setenv("AX_TEST_RANGE", "10.0.4.0/24")
    assert ip_range.parse("$AX_TEST_RANGE") == "$AX_TEST_RANGE"

    monkeypatch.setenv("AX_TEST_RANGE", "camera")
    with pytest.raises(ValueError, match="not an IPv4 address or range"):
        ip_range.parse("$AX_TEST_RANGE")
