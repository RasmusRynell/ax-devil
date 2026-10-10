import copy
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from ax_devil.modules.workspace.core import OverlayFile


class DummyCacheManager:
    def __init__(self, stats: dict[str, SimpleNamespace]) -> None:
        self._stats = stats

    def get_cache_stats(self, cache_type: str | None = None) -> SimpleNamespace:
        scoped = (
            self._stats if cache_type is None else {k: v for k, v in self._stats.items() if v.cache_type == cache_type}
        )
        return SimpleNamespace(all=self._stats, scoped=scoped)

    def get_cache_summary(
        self, cache_type: str | None = None
    ) -> SimpleNamespace:  # pragma: no cover - not expected in this test
        raise AssertionError("get_cache_summary should not be called when the cache is empty")

    def clear_cache(
        self, cache_type: str | None = None, force: bool = False
    ) -> None:  # pragma: no cover - not expected in this test
        raise AssertionError("clear_cache should not be called when the cache is empty")

    def aggregate_cache_stats(self, stats: dict[str, SimpleNamespace]) -> tuple[int, int]:
        total_files = sum(item.file_count for item in stats.values())
        total_size = sum(item.total_size for item in stats.values())
        return total_files, total_size

    def format_cache_summary(
        self, stats: dict[str, SimpleNamespace], include_total: bool = True
    ) -> str:  # pragma: no cover - not required for this test
        raise AssertionError("format_cache_summary should not be called when the cache is empty")


def _import_cli_module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import ``ax_devil.cli`` fresh so per-test stubs are respected."""
    sys.modules.pop("ax_devil.cli", None)
    return importlib.import_module("ax_devil.cli")


def _repo_root() -> Path:
    """Return the repository root for tests that patch ``sys.path``."""
    return Path(__file__).resolve().parents[2]


def _install_group_resolver_entrypoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Expose an installed resolver entry point whose CLI is a subcommand group."""
    module_path = tmp_path / "external_group_plugin.py"
    module_path.write_text(
        """
from __future__ import annotations

from pathlib import Path

import click

from ax_devil.modules.workspace.core import PlaylistContent, PlaylistItem, PlaylistSettings
from ax_devil.modules.plugin_system import PlaylistResolverPlugin, PlaylistResolverWidget


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

        @command.command(name="list-selectors")
        def list_selectors() -> None:
            click.echo("alpha/v1")
            click.echo("beta/amf")

        @command.command(name="run")
        @click.argument("simulator_root", type=click.Path(path_type=Path, file_okay=False, dir_okay=True))
        @click.option("--select", "selected_overlays", multiple=True)
        @click.pass_context
        def run(ctx: click.Context, simulator_root: Path, selected_overlays: tuple[str, ...]) -> None:
            if not simulator_root.is_dir():
                raise click.ClickException(f"Simulator root directory not found: {simulator_root}")
            runner = ctx.obj["run_with_items"]
            settings = {"root": str(simulator_root), "select": list(selected_overlays)}
            runner([PlaylistItem(label=cls.display_name(), resolver=cls.plugin_id(), settings=settings)])

        return command

    def resolve(self, settings: PlaylistSettings) -> list[PlaylistContent]:
        raise NotImplementedError

    def create_settings_widget(self) -> PlaylistResolverWidget:
        raise NotImplementedError


PLUGIN_CLASS = ExternalGroupResolver
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("external_group_plugin", None)
    distribution = SimpleNamespace(name="external-group-plugin", locate_file=lambda _: tmp_path)
    entrypoint = SimpleNamespace(
        name="external_group",
        value="external_group_plugin:PLUGIN_CLASS",
        dist=distribution,
        load=lambda: importlib.import_module("external_group_plugin").PLUGIN_CLASS,
    )

    def _entry_points(*, group: str) -> list[SimpleNamespace]:
        return [entrypoint] if group == "ax_devil.playlist_resolver_plugins" else []

    monkeypatch.setattr("ax_devil.modules.plugin_system.loader.importlib.metadata.entry_points", _entry_points)

    config_module = importlib.import_module("ax_devil.modules.settings.config_manager")
    config_data = json.loads(json.dumps(config_module.DEFAULT_CONFIG))
    config_path = tmp_path / "custom-config.json"
    config_path.write_text(json.dumps(config_data), encoding="utf-8")
    return config_path


def test_clear_cache_specific_type_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))

    package = ModuleType("ax_devil")
    setattr(package, "__path__", [str(src_dir / "ax_devil")])
    monkeypatch.setitem(sys.modules, "ax_devil", package)

    for subpackage in ("core", "utils"):
        module_name = f"ax_devil.{subpackage}"
        module = ModuleType(module_name)
        setattr(module, "__path__", [str(src_dir / "ax_devil" / subpackage)])
        monkeypatch.setitem(sys.modules, module_name, module)
        setattr(package, subpackage, module)

    core_module = sys.modules["ax_devil.core"]
    cache_manager_module = ModuleType("ax_devil.modules.cache")
    setattr(cache_manager_module, "CacheManager", DummyCacheManager)
    monkeypatch.setitem(sys.modules, "ax_devil.modules.cache", cache_manager_module)
    setattr(core_module, "cache", cache_manager_module)

    app_module = ModuleType("ax_devil.app")

    def _unused_create_app(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("create_app should not be invoked during cache clearing tests")

    setattr(app_module, "create_app", _unused_create_app)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    other_cache = SimpleNamespace(
        cache_type="other",
        file_count=5,
        total_size=1024,
        path=Path("/tmp/other"),
    )
    empty_main_cache = SimpleNamespace(
        cache_type="main",
        file_count=0,
        total_size=0,
        path=Path("/tmp/main"),
    )

    def _factory() -> DummyCacheManager:
        return DummyCacheManager({"main": empty_main_cache, "other": other_cache})

    monkeypatch.setattr(cache_manager_module, "CacheManager", _factory)

    runner = CliRunner()
    result = runner.invoke(cli, ["clear-cache", "--type", "main"])

    assert result.exit_code == 0
    assert "No cached files found for cache type 'main'." in result.output
    assert "Are you sure" not in result.output


def test_clear_cache_rejects_missing_explicit_config(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))

    package = ModuleType("ax_devil")
    setattr(package, "__path__", [str(src_dir / "ax_devil")])
    monkeypatch.setitem(sys.modules, "ax_devil", package)

    app_module = ModuleType("ax_devil.app")
    setattr(app_module, "create_app", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(cli, ["clear-cache", "--config", "/tmp/missing-config.json"])

    assert result.exit_code != 0
    assert "Config file does not exist" in result.output


def test_cli_playlist_command_launches_a_playlist_item(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))

    captured: dict[str, Any] = {}

    app_module = ModuleType("ax_devil.app")

    class DummyApp:
        def run(self) -> int:
            return 0

    def _create_app(*args: Any, **kwargs: Any) -> DummyApp:
        captured.update(kwargs)
        return DummyApp()

    setattr(app_module, "create_app", _create_app)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    seq_dir = tmp_path / "MOT16-01"
    seq_dir.mkdir()
    (seq_dir / "img1").mkdir()
    (seq_dir / "seqinfo.ini").write_text(
        "[Sequence]\nname=MOT16-01\nimDir=img1\nframeRate=30\nseqLength=10\nimWidth=1920\nimHeight=1080\nimExt=.jpg\n",
        encoding="utf-8",
    )

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["playlist", "mot_challenge", str(tmp_path)],
    )

    assert result.exit_code == 0
    [item] = captured["startup_items"]
    assert (item.resolver, item.settings) == ("mot_challenge", {"root": str(tmp_path)})


def test_cli_playlist_command_rejects_missing_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))

    app_module = ModuleType("ax_devil.app")
    setattr(app_module, "create_app", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(cli, ["playlist", "mot_challenge", "/tmp/does-not-exist"])

    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_cli_playlist_help_lists_available_resolvers(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))

    app_module = ModuleType("ax_devil.app")
    setattr(app_module, "create_app", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(cli, ["playlist", "--help"])

    assert result.exit_code == 0
    assert "Available resolvers:" in result.output
    assert "Resolver help: ax-devil playlist <resolver_id> --help" in result.output
    assert "mot_challenge" in result.output


def test_cli_playlist_help_forwards_to_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))

    app_module = ModuleType("ax_devil.app")
    setattr(app_module, "create_app", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(cli, ["playlist", "mot_challenge", "--help"])

    assert result.exit_code == 0
    assert "Usage: " in result.output
    assert "playlist mot_challenge" in result.output
    assert "DATASET_DIR" in result.output


def test_cli_playlist_command_does_not_warn_for_unread_env_bound_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))
    for env_var in (
        "AX_DEVIL_TARGET_ADDR",
        "AX_DEVIL_TARGET_USER",
        "AX_DEVIL_TARGET_PASS",
        "AX_DEVIL_MQTT_BROKER_ADDR",
        "AX_DEVIL_MQTT_BROKER_USER",
        "AX_DEVIL_MQTT_BROKER_PASS",
    ):
        monkeypatch.delenv(env_var, raising=False)

    app_module = ModuleType("ax_devil.app")

    class DummyApp:
        def run(self) -> int:
            return 0

    def _create_app(*args: Any, **kwargs: Any) -> DummyApp:
        return DummyApp()

    setattr(app_module, "create_app", _create_app)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    config_module = importlib.import_module("ax_devil.modules.settings.config_manager")

    # Write a config from DEFAULT_CONFIG (which has $VAR placeholders) so
    # the test is isolated from the user's real config file.
    config_path = tmp_path / "test_config.json"
    config_path.write_text(json.dumps(config_module.DEFAULT_CONFIG), encoding="utf-8")

    warning_messages: list[str] = []

    def _capture_warning(message: str) -> None:
        warning_messages.append(message)

    monkeypatch.setattr(config_module.logger, "warning", _capture_warning)

    seq_dir = tmp_path / "MOT16-01"
    seq_dir.mkdir()
    (seq_dir / "img1").mkdir()
    (seq_dir / "seqinfo.ini").write_text(
        "[Sequence]\nname=MOT16-01\nimDir=img1\nframeRate=30\nseqLength=10\nimWidth=1920\nimHeight=1080\nimExt=.jpg\n",
        encoding="utf-8",
    )

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--config", str(config_path), "playlist", "mot_challenge", str(tmp_path)])

    assert result.exit_code == 0
    aggregated_env_warnings = [
        msg for msg in warning_messages if msg.startswith("Config references unset environment variables")
    ]
    assert aggregated_env_warnings == []


def test_cli_group_resolver_help_shows_subcommands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))

    app_module = ModuleType("ax_devil.app")
    setattr(app_module, "create_app", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    config_path = _install_group_resolver_entrypoint(monkeypatch, tmp_path)

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--config", str(config_path), "playlist", "external_group", "--help"])

    assert result.exit_code == 0
    assert "Commands:" in result.output
    assert "list-selectors" in result.output
    assert "run" in result.output


def test_cli_group_resolver_list_selectors_subcommand(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))

    app_module = ModuleType("ax_devil.app")
    setattr(app_module, "create_app", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    config_path = _install_group_resolver_entrypoint(monkeypatch, tmp_path)

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--config", str(config_path), "playlist", "external_group", "list-selectors"])

    assert result.exit_code == 0
    assert "alpha/v1" in result.output
    assert "beta/amf" in result.output


def test_cli_group_resolver_does_not_allow_legacy_positional_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))

    app_module = ModuleType("ax_devil.app")
    setattr(app_module, "create_app", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    config_path = _install_group_resolver_entrypoint(monkeypatch, tmp_path)

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--config", str(config_path), "playlist", "external_group", str(tmp_path)])

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_cli_group_resolver_subcommand_help_is_preserved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))

    app_module = ModuleType("ax_devil.app")
    setattr(app_module, "create_app", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    config_path = _install_group_resolver_entrypoint(monkeypatch, tmp_path)

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--config", str(config_path), "playlist", "external_group", "run", "--help"])

    assert result.exit_code == 0
    assert "Usage: " in result.output
    assert "playlist external_group run" in result.output
    assert "SIMULATOR_ROOT" in result.output
    assert "--select" in result.output


def test_cli_live_keeps_configured_credential_references_in_the_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))
    monkeypatch.setenv("AX_DEVIL_TARGET_ADDR", "camera.example")
    monkeypatch.setenv("AX_DEVIL_TARGET_USER", "operator")
    monkeypatch.setenv("AX_DEVIL_TARGET_PASS", "secret")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_ADDR", "mqtt.example")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_USER", "mqtt-user")
    monkeypatch.setenv("AX_DEVIL_MQTT_BROKER_PASS", "mqtt-pass")

    captured: dict[str, Any] = {}

    app_module = ModuleType("ax_devil.app")

    class DummyApp:
        def run(self) -> int:
            return 0

    def _create_app(*args: Any, **kwargs: Any) -> DummyApp:
        captured.update(kwargs)
        return DummyApp()

    setattr(app_module, "create_app", _create_app)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    config_module = importlib.import_module("ax_devil.modules.settings.config_manager")
    config_path = tmp_path / "live-config.json"
    config_path.write_text(json.dumps(config_module.DEFAULT_CONFIG), encoding="utf-8")

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--config", str(config_path), "live", "--overlay", "mqtt"])

    assert result.exit_code == 0
    [item] = captured["startup_items"]
    assert item.label == "Live: $AX_DEVIL_TARGET_ADDR"
    assert (item.host, item.username, item.password) == (
        "$AX_DEVIL_TARGET_ADDR",
        "$AX_DEVIL_TARGET_USER",
        "$AX_DEVIL_TARGET_PASS",
    )
    assert (item.mqtt_host, item.mqtt_username, item.mqtt_password) == (
        "$AX_DEVIL_MQTT_BROKER_ADDR",
        "$AX_DEVIL_MQTT_BROKER_USER",
        "$AX_DEVIL_MQTT_BROKER_PASS",
    )
    values = item.expanded()
    assert (values.host, values.username, values.password) == ("camera.example", "operator", "secret")
    assert (values.mqtt_host, values.mqtt_username, values.mqtt_password) == ("mqtt.example", "mqtt-user", "mqtt-pass")
    assert item.overlay_mode.value == "mqtt"
    assert item.handler_type == "ADF_V1_FRAME"
    assert item.analytics_data_source_key == "com.axis.scene.frame.v1#1"
    assert item.device_api_protocol == "https"


def test_cli_live_takes_command_line_credentials_literally(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    app_module = ModuleType("ax_devil.app")

    class DummyApp:
        def run(self) -> int:
            return 0

    def create_app(*args: Any, **kwargs: Any) -> DummyApp:
        captured.update(kwargs)
        return DummyApp()

    setattr(app_module, "create_app", create_app)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)
    config_module = importlib.import_module("ax_devil.modules.settings.config_manager")
    config = copy.deepcopy(config_module.DEFAULT_CONFIG)
    config["defaults"]["live_stream"]["overlay_source"] = "none"
    config_path = tmp_path / "live-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    cli_module = _import_cli_module(monkeypatch)

    result = CliRunner().invoke(
        cli_module.cli,
        ["--config", str(config_path), "live", "--host", "camera.local", "--password", "pa$$word"],
    )

    assert result.exit_code == 0, result.output
    [item] = captured["startup_items"]
    assert (item.host, item.password) == ("camera.local", "pa$$word")
    assert item.expanded().password == "pa$$word"


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
def test_cli_live_selects_handler_and_transport_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    configured_mode: str,
    arguments: list[str],
    expected_mode: str,
) -> None:
    """The public live command normalizes the configured mode and applies transport overrides."""
    captured: dict[str, Any] = {}
    app_module = ModuleType("ax_devil.app")

    class DummyApp:
        def run(self) -> int:
            """Avoid launching an interactive application."""
            return 0

    def create_app(*args: Any, **kwargs: Any) -> DummyApp:
        captured.update(kwargs)
        return DummyApp()

    setattr(app_module, "create_app", create_app)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)
    config_module = importlib.import_module("ax_devil.modules.settings.config_manager")
    config = copy.deepcopy(config_module.DEFAULT_CONFIG)
    config["defaults"]["device"]["host"] = "camera.example"
    live = config["defaults"]["live_stream"]
    live["overlay_source"] = configured_mode
    live["analytics-mqtt"].update(
        data_stream_handler="ADF_BETA_FRAME", broker_host="mqtt.example", data_source_key="analytics/source"
    )
    live["analytics-websocket"].update(
        data_stream_handler="ADF_V1_FRAME", topic="configured.topic", channel_id=2, device_api_protocol="http"
    )
    config_path = tmp_path / "live-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    cli_module = _import_cli_module(monkeypatch)

    result = CliRunner().invoke(cli_module.cli, ["--config", str(config_path), "live", *arguments])

    assert result.exit_code == 0, result.output
    [startup] = captured["startup_items"]
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
def test_cli_live_resolves_numeric_references(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, expected: tuple[int, int, int]
) -> None:
    """The public command handles unset numeric references, numeric strings and CLI precedence."""
    captured: dict[str, Any] = {}
    app_module = ModuleType("ax_devil.app")

    class DummyApp:
        def run(self) -> int:
            return 0

    def create_app(*args: Any, **kwargs: Any) -> DummyApp:
        captured.update(kwargs)
        return DummyApp()

    setattr(app_module, "create_app", create_app)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)
    config_module = importlib.import_module("ax_devil.modules.settings.config_manager")
    config = copy.deepcopy(config_module.DEFAULT_CONFIG)
    defaults = config["defaults"]
    defaults["device"]["host"] = "camera.example"
    live = defaults["live_stream"]
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
    path = tmp_path / "live.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    cli_module = _import_cli_module(monkeypatch)
    args = ["--config", str(path), "live"]
    if mode == "overridden":
        args.extend(["--camera-head", "9", "--mqtt-port", "3883", "--channel-id", "7"])

    result = CliRunner().invoke(cli_module.cli, args)

    assert result.exit_code == 0, result.output
    [startup] = captured["startup_items"]
    assert (startup.camera_head, startup.mqtt_port, startup.websocket_channel_id) == expected


def test_cli_live_reports_invalid_config_overlay_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app_module = ModuleType("ax_devil.app")
    setattr(app_module, "create_app", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)
    config_module = importlib.import_module("ax_devil.modules.settings.config_manager")
    config = copy.deepcopy(config_module.DEFAULT_CONFIG)
    config["defaults"]["live_stream"]["overlay_source"] = "invalid"
    config_path = tmp_path / "invalid-live-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    cli_module = _import_cli_module(monkeypatch)

    result = CliRunner().invoke(cli_module.cli, ["--config", str(config_path), "live"])

    assert result.exit_code != 0
    assert "Unsupported live overlay mode: invalid" in result.output


def test_cli_local_launches_a_video_item(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))

    captured: dict[str, Any] = {}

    app_module = ModuleType("ax_devil.app")

    class DummyApp:
        def run(self) -> int:
            return 0

    def _create_app(*args: Any, **kwargs: Any) -> DummyApp:
        captured.update(kwargs)
        return DummyApp()

    setattr(app_module, "create_app", _create_app)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"\x00")

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(cli, ["local", "--video", str(video_file)])

    assert result.exit_code == 0
    [item] = captured["startup_items"]
    assert (item.label, item.video, item.overlays) == ("test.mp4", video_file, ())


def test_cli_local_with_overlay(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))

    captured: dict[str, Any] = {}

    app_module = ModuleType("ax_devil.app")

    class DummyApp:
        def run(self) -> int:
            return 0

    def _create_app(*args: Any, **kwargs: Any) -> DummyApp:
        captured.update(kwargs)
        return DummyApp()

    setattr(app_module, "create_app", _create_app)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"\x00")
    overlay_file = tmp_path / "data.jsonl"
    overlay_file.write_text("{}\n", encoding="utf-8")

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(
        cli, ["local", "--video", str(video_file), "--overlay", str(overlay_file), "--handler-type", "ADF_BETA_FRAME"]
    )

    assert result.exit_code == 0
    [item] = captured["startup_items"]
    assert item.video == video_file
    assert item.overlays == (OverlayFile(overlay_file, "ADF_BETA_FRAME"),)


def test_cli_local_overlay_requires_handler_type(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))

    app_module = ModuleType("ax_devil.app")
    setattr(app_module, "create_app", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"\x00")
    overlay_file = tmp_path / "data.jsonl"
    overlay_file.write_text("{}\n", encoding="utf-8")

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(cli, ["local", "--video", str(video_file), "--overlay", str(overlay_file)])

    assert result.exit_code != 0
    assert "--handler-type is required" in result.output


def test_cli_local_requires_video(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = _repo_root()
    src_dir = repo_root / "src"

    monkeypatch.syspath_prepend(str(src_dir))

    app_module = ModuleType("ax_devil.app")
    setattr(app_module, "create_app", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "ax_devil.app", app_module)

    cli_module = _import_cli_module(monkeypatch)
    cli = cli_module.cli

    runner = CliRunner()
    result = runner.invoke(cli, ["local"])

    assert result.exit_code != 0
    assert "--video" in result.output
