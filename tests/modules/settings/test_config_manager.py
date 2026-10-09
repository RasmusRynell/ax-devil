from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from ax_devil.modules.settings.config_manager import CONFIG_VERSION, DEFAULT_CONFIG, ConfigManager, load_config


def test_load_config_missing_file_returns_new_default(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"

    raw_config = load_config(config_path)
    persisted_raw = json.loads(config_path.read_text(encoding="utf-8"))

    assert raw_config == DEFAULT_CONFIG
    assert persisted_raw == DEFAULT_CONFIG
    assert raw_config["version"] == CONFIG_VERSION
    assert raw_config["storage"]["render_catalogs_dir"].endswith(".ax_devil/render_catalogs")
    assert raw_config["defaults"]["device"]["host"] == "$AX_DEVIL_TARGET_ADDR"


def test_config_manager_resolved_reads_and_save_preserve_raw_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AX_DEVIL_TARGET_ADDR", "10.20.30.40")

    raw = json.loads(json.dumps(DEFAULT_CONFIG))
    raw["storage"]["base_dir"] = "~/raw-storage"
    raw["defaults"]["device"]["host"] = "$AX_DEVIL_TARGET_ADDR"
    raw["defaults"]["device"]["username"] = "camera-admin"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")

    cfg = ConfigManager()
    cfg.set_config_path(config_path, create_if_missing=False)

    resolved_storage = cast(dict[str, Any], cfg.get("storage", {}))
    resolved_defaults = cast(dict[str, Any], cfg.get("defaults", {}))
    raw_storage = cast(dict[str, Any], cfg.get_raw("storage", {}))
    raw_defaults = cast(dict[str, Any], cfg.get_raw("defaults", {}))

    assert resolved_storage["base_dir"] == str((tmp_path / "raw-storage").resolve())
    assert cast(dict[str, Any], resolved_defaults["device"])["host"] == "10.20.30.40"
    assert raw_storage["base_dir"] == "~/raw-storage"
    assert cast(dict[str, Any], raw_defaults["device"])["host"] == "$AX_DEVIL_TARGET_ADDR"
    cfg.save()
    assert json.loads(config_path.read_text(encoding="utf-8")) == raw
    monkeypatch.setattr(ConfigManager, "_instance", None)
    reopened = ConfigManager()
    reopened.set_config_path(config_path, create_if_missing=False)
    assert reopened.get("storage")["base_dir"] == str((tmp_path / "raw-storage").resolve())
    assert reopened.get("defaults")["device"]["host"] == "10.20.30.40"
    assert reopened.get("defaults")["device"]["username"] == "camera-admin"
    assert reopened.get_raw("defaults")["device"]["host"] == "$AX_DEVIL_TARGET_ADDR"


def test_load_config_rejects_different_major_version(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": "3.0",
                "defaults": DEFAULT_CONFIG["defaults"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unsupported config major version"):
        load_config(config_path)


def test_load_config_allows_same_major_version_and_fills_missing_known_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": "2.99",
                "ui": {"theme": "light"},
                "custom": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )

    raw_config = load_config(config_path)

    assert raw_config["version"] == "2.99"
    assert raw_config["ui"]["theme"] == "light"
    assert raw_config["ui"]["window"]["custom_frame"] is True
    assert raw_config["storage"] == DEFAULT_CONFIG["storage"]
    assert raw_config["custom"] == {"enabled": True}


def test_load_config_same_major_raises_when_known_branch_is_not_mapping(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": "2.1",
                "defaults": "invalid-branch",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"Cannot merge config branch at 'config.defaults'"):
        load_config(config_path)


def test_load_config_preserves_scalar_type_mismatches_for_known_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": "2.2",
                "ui": {
                    "theme": False,
                },
            }
        ),
        encoding="utf-8",
    )

    raw_config = load_config(config_path)

    cfg = ConfigManager()
    cfg.set_config_path(config_path, create_if_missing=False)
    resolved_ui = cast(dict[str, Any], cfg.get("ui", {}))

    assert raw_config["ui"]["theme"] is False
    assert resolved_ui["theme"] is False


def test_config_manager_expands_home_only_in_storage_folders(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AX_TEST_LOGS_DIR", "~/env-logs")

    absolute_entry = str((tmp_path / "absolute").resolve())

    config = {
        "version": CONFIG_VERSION,
        "storage": {
            "base_dir": "~/configs",
            "logs_dir": "$AX_TEST_LOGS_DIR",
            "nested": {"cache_dir": "~/caches"},
            "non_path_value": "not-a-path",
        },
        "list_paths": ["~/data", absolute_entry, 42],
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    cfg = ConfigManager()
    cfg.set_config_path(config_path, create_if_missing=False)
    resolved_storage = cast(dict[str, Any], cfg.get("storage", {}))
    resolved_list_paths = cast(list[Any], cfg.get("list_paths", []))

    assert resolved_storage["base_dir"] == str((tmp_path / "configs").resolve())
    assert resolved_storage["logs_dir"] == str((tmp_path / "env-logs").resolve())
    assert resolved_storage["nested"]["cache_dir"] == str((tmp_path / "caches").resolve())
    assert resolved_storage["non_path_value"] == "not-a-path"
    assert resolved_list_paths == ["~/data", absolute_entry, 42]


@pytest.mark.parametrize("literal", ["~pass123", "~", "$5admin", "$ pw", "$", "pa$$word"])
def test_literal_credentials_are_never_expanded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, literal: str) -> None:
    """A password or username that happens to start with ``~`` or ``$`` is used exactly as typed."""
    monkeypatch.setenv("HOME", str(tmp_path))

    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["defaults"]["device"]["password"] = literal
    config["defaults"]["device"]["username"] = literal
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    cfg = ConfigManager()
    cfg.set_config_path(config_path, create_if_missing=False)
    device = cast(dict[str, Any], cfg.get("defaults", {})["device"])

    assert device["password"] == literal
    assert device["username"] == literal


def test_config_manager_ensures_render_catalog_storage_directory(tmp_path: Path) -> None:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["storage"] = {
        "base_dir": str(tmp_path / "base"),
        "cache_dir": str(tmp_path / "caches"),
        "logs_dir": str(tmp_path / "logs"),
        "render_catalogs_dir": str(tmp_path / "render_catalogs"),
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    cfg = ConfigManager()
    cfg.set_config_path(config_path, create_if_missing=False)
    cfg.ensure_storage_directories()

    assert (tmp_path / "render_catalogs").is_dir()


def test_config_manager_logs_single_warning_for_all_missing_vars(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    monkeypatch.delenv("AX_DEVIL_TARGET_ADDR", raising=False)
    monkeypatch.delenv("AX_DEVIL_TARGET_USER", raising=False)
    monkeypatch.delenv("AX_DEVIL_TARGET_PASS", raising=False)

    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    cfg = ConfigManager()
    cfg.set_config_path(config_path, create_if_missing=False)

    with caplog.at_level("WARNING"):
        resolved_defaults = cast(dict[str, Any], cfg.get("defaults", {}))

    resolved_device = cast(dict[str, Any], resolved_defaults["device"])
    assert resolved_device["host"] == ""
    assert resolved_device["username"] == ""
    assert resolved_device["password"] == ""

    warning_messages = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert len(warning_messages) == 1
    assert "Config references unset environment variables" in warning_messages[0]
    assert "AX_DEVIL_TARGET_ADDR" in warning_messages[0]
    assert "AX_DEVIL_TARGET_USER" in warning_messages[0]
    assert "AX_DEVIL_TARGET_PASS" in warning_messages[0]


def test_config_manager_deduplicates_missing_vars(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    monkeypatch.delenv("AX_DEVIL_TARGET_ADDR", raising=False)
    monkeypatch.setenv("AX_DEVIL_TARGET_USER", "user")
    monkeypatch.setenv("AX_DEVIL_TARGET_PASS", "pass")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_ADDR", "broker")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_USER", "mqtt-user")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_PASS", "mqtt-pass")

    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["defaults"]["device"]["host"] = "$AX_DEVIL_TARGET_ADDR"
    config["defaults"]["device"]["username"] = "$AX_DEVIL_TARGET_ADDR"
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    cfg = ConfigManager()
    cfg.set_config_path(config_path, create_if_missing=False)

    with caplog.at_level("WARNING"):
        _ = cfg.get("defaults", {})

    warning_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelname == "WARNING" and "Config references unset environment variables" in record.getMessage()
    ]
    assert len(warning_messages) == 1
    assert warning_messages[0].count("AX_DEVIL_TARGET_ADDR") == 1


def test_set_config_path_unchanged_keeps_unsaved_changes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(DEFAULT_CONFIG), encoding="utf-8")

    cfg = ConfigManager()
    cfg.set_config_path(config_path, create_if_missing=False)
    cfg.set("ui", {"theme": "light"})

    cfg.set_config_path(config_path, create_if_missing=False)
    assert cfg.get("ui") == {"theme": "light"}
    assert json.loads(config_path.read_text(encoding="utf-8"))["ui"] == DEFAULT_CONFIG["ui"]


def test_load_config_warns_once_for_unsupported_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AX_DEVIL_TARGET_ADDR", "10.0.0.1")
    monkeypatch.setenv("AX_DEVIL_TARGET_USER", "user")
    monkeypatch.setenv("AX_DEVIL_TARGET_PASS", "pass")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_ADDR", "broker")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_USER", "mqtt-user")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_PASS", "mqtt-pass")

    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["extra_top"] = {"value": 1}
    config["ui"]["window"]["extra_window_option"] = "enabled"

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with caplog.at_level("WARNING"):
        _ = load_config(config_path)

    unsupported_warnings = [
        record.getMessage() for record in caplog.records if "Config contains unsupported keys" in record.getMessage()
    ]
    assert len(unsupported_warnings) == 1
    assert "preserved but ignored by this version" in unsupported_warnings[0]
    assert "config.extra_top" in unsupported_warnings[0]
    assert "config.ui.window.extra_window_option" in unsupported_warnings[0]


def test_unsupported_nested_config_round_trips_unchanged_on_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AX_DEVIL_TARGET_ADDR", "10.0.0.1")
    monkeypatch.setenv("AX_DEVIL_TARGET_USER", "user")
    monkeypatch.setenv("AX_DEVIL_TARGET_PASS", "pass")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_ADDR", "broker")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_USER", "mqtt-user")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_PASS", "mqtt-pass")

    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["ui"]["window"]["extra_window_option"] = "enabled"
    config["extra_top"] = {
        "deep": {
            "items": [
                {"id": 1, "payload": {"k": "v"}},
                {"id": 2, "payload": {"k": "w"}},
            ]
        }
    }
    config["defaults"]["live_stream"]["analytics-mqtt"]["extra_branch"] = {"child": {"token": "opaque"}}

    expected_extras = {
        "extra_top": json.loads(json.dumps(config["extra_top"])),
        "analytics_extra_branch": json.loads(
            json.dumps(config["defaults"]["live_stream"]["analytics-mqtt"]["extra_branch"])
        ),
    }

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    cfg = ConfigManager()
    cfg.set_config_path(config_path, create_if_missing=False)
    assert cfg.get_raw("extra_top") == expected_extras["extra_top"]
    assert cfg.get("extra_top") == expected_extras["extra_top"]
    assert cfg.get_raw("ui")["window"]["extra_window_option"] == "enabled"
    assert cfg.get("ui")["window"]["extra_window_option"] == "enabled"
    assert (
        cfg.get("defaults")["live_stream"]["analytics-mqtt"]["extra_branch"]
        == expected_extras["analytics_extra_branch"]
    )
    cfg.save()

    persisted_raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted_raw["extra_top"] == expected_extras["extra_top"]
    assert (
        persisted_raw["defaults"]["live_stream"]["analytics-mqtt"]["extra_branch"]
        == expected_extras["analytics_extra_branch"]
    )

    assert persisted_raw["ui"]["window"]["extra_window_option"] == "enabled"
    monkeypatch.setattr(ConfigManager, "_instance", None)
    reopened = ConfigManager()
    reopened.set_config_path(config_path, create_if_missing=False)
    assert reopened.get("extra_top") == expected_extras["extra_top"]
    assert reopened.get("ui")["window"]["extra_window_option"] == "enabled"
    assert (
        reopened.get("defaults")["live_stream"]["analytics-mqtt"]["extra_branch"]
        == expected_extras["analytics_extra_branch"]
    )


def test_config_logs_keys_without_resolved_or_replaced_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Nested reads and replacements must not log device or broker credentials."""
    monkeypatch.setenv("AX_DEVIL_TARGET_PASS", "synthetic-device-secret")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_PASS", "synthetic-broker-secret")
    cfg = ConfigManager()
    cfg.set_config_path(tmp_path / "config.json")
    with caplog.at_level("DEBUG"):
        defaults = cfg.get("defaults")
        assert defaults["device"]["password"] == "synthetic-device-secret"
        cfg.set("defaults", {"password": "synthetic-replacement-secret"})
        assert cfg.get("defaults") == {"password": "synthetic-replacement-secret"}
        assert cfg.get("missing", "synthetic-default-secret") == "synthetic-default-secret"
    assert "getting config key: defaults" in caplog.text
    for secret in (
        "synthetic-device-secret",
        "synthetic-broker-secret",
        "synthetic-replacement-secret",
        "synthetic-default-secret",
    ):
        assert secret not in caplog.text
