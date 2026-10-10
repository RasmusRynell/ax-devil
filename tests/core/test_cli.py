"""The public ``ax-devil`` command builds the right startup content, or rejects bad input before launching."""

from __future__ import annotations

import copy
import importlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner, Result

from ax_devil import cli as cli_module
from ax_devil.modules.plugin_system import ApplicationPluginLoader
from ax_devil.modules.settings.config_manager import DEFAULT_CONFIG, ConfigManager

ENTRY_POINTS = "ax_devil.modules.plugin_system.loader.importlib.metadata.entry_points"
MOT_SEQINFO = (
    "[Sequence]\nname=MOT16-01\nimDir=img1\nframeRate=30\nseqLength=10\nimWidth=1920\nimHeight=1080\nimExt=.jpg\n"
)
GROUP_RESOLVER_SOURCE = """
from pathlib import Path

import click

from ax_devil.modules.plugin_system import PlaylistResolverPlugin, PlaylistResolverWidget
from ax_devil.modules.workspace import ResolvedPlaylistStartup


class ExternalGroupResolver(PlaylistResolverPlugin):
    @classmethod
    def required_api_version(cls) -> int:
        return 1

    @classmethod
    def plugin_id(cls) -> str:
        return "external_group"

    @classmethod
    def display_name(cls) -> str:
        return "External Group Resolver"

    @classmethod
    def create_cli_command(cls) -> click.Command | None:
        @click.group(name=cls.plugin_id())
        def command() -> None:
            pass

        @command.command(name="run")
        @click.argument("simulator_root", type=click.Path(path_type=Path, file_okay=False, dir_okay=True))
        @click.option("--select", "selected_overlays", multiple=True)
        @click.pass_context
        def run(ctx: click.Context, simulator_root: Path, selected_overlays: tuple[str, ...]) -> None:
            ctx.obj["run_with_startup_content"](ResolvedPlaylistStartup(playlists=()))

        return command

    def create_settings_widget(self) -> PlaylistResolverWidget:
        raise NotImplementedError


PLUGIN_CLASS = ExternalGroupResolver
"""


@pytest.fixture(autouse=True)
def startups(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Any]]:
    """Record the startup content each command would open, without starting the application.

    Commands reload plugins; only built-in ones are visible, and the built-in set is restored afterward.
    """
    opened: list[Any] = []

    class _App:
        def run(self) -> int:
            return 0

    def create_app(*args: Any, **kwargs: Any) -> _App:
        opened.append(kwargs["startup_content"])
        return _App()

    monkeypatch.setattr(cli_module, "create_app", create_app)
    monkeypatch.setattr(ENTRY_POINTS, lambda **_: [])
    yield opened
    with patch(ENTRY_POINTS, return_value=[]):
        ApplicationPluginLoader.reload_plugins()


def _invoke(*arguments: str, user_input: str | None = None) -> Result:
    return CliRunner().invoke(cli_module.cli, list(arguments), input=user_input)


def _default_config() -> dict[str, Any]:
    config: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
    return config


def _write_config(tmp_path: Path, config: dict[str, Any]) -> str:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


def _mot_dataset(tmp_path: Path) -> Path:
    root = tmp_path / "dataset"
    sequence = root / "MOT16-01"
    (sequence / "img1").mkdir(parents=True)
    (sequence / "seqinfo.ini").write_text(MOT_SEQINFO, encoding="utf-8")
    return root


def _install_group_resolver(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Expose an installed resolver whose command is a group of subcommands."""
    (tmp_path / "external_group_plugin.py").write_text(GROUP_RESOLVER_SOURCE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "external_group_plugin", raising=False)
    entrypoint = SimpleNamespace(
        name="external_group",
        value="external_group_plugin:PLUGIN_CLASS",
        dist=SimpleNamespace(name="external-group-plugin", locate_file=lambda _: tmp_path),
        load=lambda: importlib.import_module("external_group_plugin").PLUGIN_CLASS,
    )
    monkeypatch.setattr(
        ENTRY_POINTS, lambda *, group: [entrypoint] if group == "ax_devil.playlist_resolver_plugins" else []
    )


def test_clear_cache_asks_before_deleting_cached_files(tmp_path: Path) -> None:
    cache_dir = tmp_path / "caches"
    cached = cache_dir / "frames" / "video.cache"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"cached")
    config = _default_config()
    config["storage"] = {**ConfigManager().get("storage"), "cache_dir": str(cache_dir)}
    config_path = _write_config(tmp_path, config)

    declined = _invoke("clear-cache", "--config", config_path, user_input="n\n")

    assert declined.exit_code == 0, declined.output
    assert cached.exists()

    forced = _invoke("clear-cache", "--config", config_path, "--force")

    assert forced.exit_code == 0, forced.output
    assert not cached.exists()


def test_clear_cache_with_nothing_cached_does_not_ask(tmp_path: Path) -> None:
    config = _default_config()
    config["storage"] = {**ConfigManager().get("storage"), "cache_dir": str(tmp_path / "empty")}

    result = _invoke("clear-cache", "--config", _write_config(tmp_path, config), "--type", "main")

    assert result.exit_code == 0, result.output
    assert "No cached files found for cache type 'main'." in result.output
    assert "Are you sure" not in result.output


def test_clear_cache_rejects_missing_explicit_config(tmp_path: Path) -> None:
    result = _invoke("clear-cache", "--config", str(tmp_path / "missing-config.json"))

    assert result.exit_code != 0
    assert "Config file does not exist" in result.output


def test_playlist_command_opens_resolved_playlists(tmp_path: Path, startups: list[Any]) -> None:
    result = _invoke("playlist", "mot_challenge", str(_mot_dataset(tmp_path)))

    assert result.exit_code == 0, result.output
    [startup] = startups
    [playlist] = startup.playlists
    assert playlist.display_name == "MOT Challenge"
    assert len(playlist.entries) == 1


def test_playlist_command_rejects_missing_directory(tmp_path: Path, startups: list[Any]) -> None:
    result = _invoke("playlist", "mot_challenge", str(tmp_path / "does-not-exist"))

    assert result.exit_code != 0
    assert "does not exist" in result.output
    assert startups == []


def test_playlist_help_lists_available_resolvers() -> None:
    result = _invoke("playlist", "--help")

    assert result.exit_code == 0
    assert "Available resolvers:" in result.output
    assert "mot_challenge" in result.output
    assert "Resolver help: ax-devil playlist <resolver_id> --help" in result.output


def test_playlist_help_forwards_to_resolver() -> None:
    result = _invoke("playlist", "mot_challenge", "--help")

    assert result.exit_code == 0
    assert "playlist mot_challenge" in result.output
    assert "DATASET_DIR" in result.output


def test_playlist_command_does_not_warn_for_unread_env_bound_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unset device and broker variables only matter to live commands, so playlists must not warn about them."""
    for env_var in (
        "AX_DEVIL_TARGET_ADDR",
        "AX_DEVIL_TARGET_USER",
        "AX_DEVIL_TARGET_PASS",
        "AX_DEVIL_MQTT_BROKER_ADDR",
        "AX_DEVIL_MQTT_BROKER_USER",
        "AX_DEVIL_MQTT_BROKER_PASS",
    ):
        monkeypatch.delenv(env_var, raising=False)
    config_module = importlib.import_module("ax_devil.modules.settings.config_manager")
    warnings: list[str] = []
    monkeypatch.setattr(config_module.logger, "warning", warnings.append)

    result = _invoke(
        "--config", _write_config(tmp_path, _default_config()), "playlist", "mot_challenge", str(_mot_dataset(tmp_path))
    )

    assert result.exit_code == 0, result.output
    assert not [message for message in warnings if message.startswith("Config references unset environment variables")]


def test_group_resolver_help_reaches_every_subcommand(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_group_resolver(monkeypatch, tmp_path)

    group_help = _invoke("playlist", "external_group", "--help")
    run_help = _invoke("playlist", "external_group", "run", "--help")

    assert group_help.exit_code == 0, group_help.output
    assert "Commands:" in group_help.output
    assert "run" in group_help.output
    assert run_help.exit_code == 0, run_help.output
    assert "playlist external_group run" in run_help.output
    assert "SIMULATOR_ROOT" in run_help.output
    assert "--select" in run_help.output


def test_group_resolver_subcommand_opens_its_playlists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, startups: list[Any]
) -> None:
    _install_group_resolver(monkeypatch, tmp_path)

    result = _invoke("playlist", "external_group", "run", str(tmp_path), "--select", "alpha")

    assert result.exit_code == 0, result.output
    assert len(startups) == 1


def test_live_reads_device_and_broker_from_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, startups: list[Any]
) -> None:
    monkeypatch.setenv("AX_DEVIL_TARGET_ADDR", "camera.example")
    monkeypatch.setenv("AX_DEVIL_TARGET_USER", "operator")
    monkeypatch.setenv("AX_DEVIL_TARGET_PASS", "secret")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_ADDR", "mqtt.example")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_USER", "mqtt-user")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_PASS", "mqtt-pass")

    result = _invoke("--config", _write_config(tmp_path, _default_config()), "live", "--overlay", "mqtt")

    assert result.exit_code == 0, result.output
    [startup] = startups
    assert (startup.host, startup.username, startup.password) == ("camera.example", "operator", "secret")
    assert (startup.mqtt_host, startup.mqtt_username, startup.mqtt_password) == (
        "mqtt.example",
        "mqtt-user",
        "mqtt-pass",
    )
    assert startup.overlay_mode.value == "mqtt"


@pytest.mark.parametrize(
    ("configured_mode", "arguments", "expected_mode"),
    [
        pytest.param("MQTT", [], "mqtt", id="normalize-config-mode"),
        pytest.param("websocket", [], "websocket", id="websocket-config"),
        pytest.param(
            "websocket",
            ["--topic", "override.topic", "--channel-id", "4", "--device-api-protocol", "https"],
            "websocket",
            id="websocket-cli-overrides",
        ),
    ],
)
def test_live_selects_handler_and_transport_settings(
    tmp_path: Path, startups: list[Any], configured_mode: str, arguments: list[str], expected_mode: str
) -> None:
    """The live command normalizes the configured mode and applies transport overrides."""
    config = _default_config()
    config["defaults"]["device"]["host"] = "camera.example"
    live = config["defaults"]["live_stream"]
    live["overlay_source"] = configured_mode
    live["analytics-mqtt"].update(
        data_stream_handler="ADF_BETA_FRAME", broker_host="mqtt.example", data_source_key="analytics/source"
    )
    live["analytics-websocket"].update(
        data_stream_handler="ADF_V1_FRAME", topic="configured.topic", channel_id=2, device_api_protocol="http"
    )

    result = _invoke("--config", _write_config(tmp_path, config), "live", *arguments)

    assert result.exit_code == 0, result.output
    [startup] = startups
    assert startup.overlay_mode.value == expected_mode
    if expected_mode == "mqtt":
        assert startup.handler_type == "ADF_BETA_FRAME"
        assert startup.mqtt_host == "mqtt.example"
        assert startup.analytics_data_source_key == "analytics/source"
    else:
        assert startup.handler_type == "ADF_V1_FRAME"
        expected = ("override.topic", 4, "https") if arguments else ("configured.topic", 2, "http")
        assert (startup.websocket_topic, startup.websocket_channel_id, startup.device_api_protocol) == expected


@pytest.mark.parametrize(
    "mode,expected", [("unset", (1, 1883, 1)), ("configured", (3, 2883, 4)), ("overridden", (9, 3883, 7))]
)
def test_live_resolves_numeric_references(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, startups: list[Any], mode: str, expected: tuple[int, int, int]
) -> None:
    """The live command handles unset numeric references, numeric strings and CLI precedence."""
    config = _default_config()
    config["defaults"]["device"]["host"] = "camera.example"
    live = config["defaults"]["live_stream"]
    live["overlay_source"] = "none"
    for branch, key, variable, value in (
        ("rtsp", "camera_head", "AX_DEVIL_TEST_HEAD", "3"),
        ("analytics-mqtt", "broker_port", "AX_DEVIL_TEST_PORT", "2883"),
        ("analytics-websocket", "channel_id", "AX_DEVIL_TEST_CHANNEL", "4"),
    ):
        live[branch][key] = f"${variable}"
        if mode == "unset":
            monkeypatch.delenv(variable, raising=False)
        else:
            monkeypatch.setenv(variable, value)
    arguments = ["--config", _write_config(tmp_path, config), "live"]
    if mode == "overridden":
        arguments.extend(["--camera-head", "9", "--mqtt-port", "3883", "--channel-id", "7"])

    result = _invoke(*arguments)

    assert result.exit_code == 0, result.output
    [startup] = startups
    assert (startup.camera_head, startup.mqtt_port, startup.websocket_channel_id) == expected


def test_live_reports_invalid_config_overlay_mode(tmp_path: Path, startups: list[Any]) -> None:
    config = _default_config()
    config["defaults"]["live_stream"]["overlay_source"] = "invalid"

    result = _invoke("--config", _write_config(tmp_path, config), "live")

    assert result.exit_code != 0
    assert "Unsupported live overlay mode: invalid" in result.output
    assert startups == []


@pytest.mark.parametrize("with_overlay", [False, True], ids=["video-only", "video-and-overlay"])
def test_local_opens_video_with_optional_overlay(tmp_path: Path, startups: list[Any], with_overlay: bool) -> None:
    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"\x00")
    overlay_file = tmp_path / "data.jsonl"
    overlay_file.write_text("{}\n", encoding="utf-8")
    overlay_arguments = ["--overlay", str(overlay_file), "--handler-type", "ADF_BETA_FRAME"] if with_overlay else []

    result = _invoke("local", "--video", str(video_file), *overlay_arguments)

    assert result.exit_code == 0, result.output
    [startup] = startups
    assert startup.video_path == video_file
    assert startup.overlay_path == (overlay_file if with_overlay else None)
    assert startup.handler_type == ("ADF_BETA_FRAME" if with_overlay else None)


def test_local_overlay_requires_handler_type(tmp_path: Path, startups: list[Any]) -> None:
    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"\x00")
    overlay_file = tmp_path / "data.jsonl"
    overlay_file.write_text("{}\n", encoding="utf-8")

    result = _invoke("local", "--video", str(video_file), "--overlay", str(overlay_file))

    assert result.exit_code != 0
    assert "--handler-type is required" in result.output
    assert startups == []
