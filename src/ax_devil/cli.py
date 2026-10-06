"""Command-line interface for ax-devil application."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

from ax_devil.modules.catalog_viewer.cli import catalog
from ax_devil.modules.plugin_installation.cli import plugins
from ax_devil.modules.plugin_system import (
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    ApplicationPluginLoader,
    PlaylistResolverPlugin,
    PluginStatus,
    RuntimePluginRegistry,
)
from ax_devil.modules.settings.config_manager import ConfigManager, integer_default
from ax_devil.modules.settings.logging_config import setup_logging
from ax_devil.modules.settings.paths import DEFAULT_CONFIG_PATH
from ax_devil.modules.workspace import LiveOverlayMode, LiveStreamStartup

from .app import create_app
from .cli_options import CONFIG_OPTION, DEBUG_OPTION, LOG_LEVEL_OPTION, apply_run_options

if TYPE_CHECKING:
    from ax_devil.modules.workspace import StartupContent


def _require_existing_config_path(config: Path | None) -> None:
    """Reject explicit config paths that do not exist."""
    if config is not None and not config.exists():
        raise click.ClickException(f"Config file does not exist: {config}")


def _apply_cli_config_and_reload_plugins(config: Path | None) -> None:
    """Apply the requested CLI config path, then reload plugins."""
    _require_existing_config_path(config)

    config_manager = ConfigManager()
    target = config if config is not None else DEFAULT_CONFIG_PATH
    config_manager.set_config_path(target, create_if_missing=config is None)
    ApplicationPluginLoader.reload_plugins()


def _live_startup_from_config(
    *,
    host: str | None = None,
    username: str | None = None,
    password: str | None = None,
    camera_head: int | None = None,
    resolution: str | None = None,
    overlay: str | None = None,
    handler_type: str | None = None,
    mqtt_host: str | None = None,
    mqtt_port: int | None = None,
    mqtt_username: str | None = None,
    mqtt_password: str | None = None,
    analytics_data_source_key: str | None = None,
    device_api_protocol: str | None = None,
    websocket_topic: str | None = None,
    websocket_channel_id: int | None = None,
) -> LiveStreamStartup:
    """Build a ``LiveStreamStartup`` from config defaults, with optional CLI overrides."""
    cfg = ConfigManager()
    defaults = cfg.get("defaults", {}) or {}
    device = defaults.get("device", {}) or {}
    live = defaults.get("live_stream", {}) or {}
    rtsp = live.get("rtsp", {}) or {}
    mqtt = live.get("analytics-mqtt", {}) or {}
    websocket = live.get("analytics-websocket", {}) or {}

    resolved_overlay = LiveOverlayMode.from_value(overlay or str(live.get("overlay_source", "none")))
    resolved_handler = handler_type
    if resolved_handler is None and resolved_overlay is LiveOverlayMode.RTSP:
        resolved_handler = rtsp.get("data_stream_handler")
    if resolved_handler is None and resolved_overlay is LiveOverlayMode.MQTT:
        resolved_handler = mqtt.get("data_stream_handler")
    if resolved_handler is None and resolved_overlay is LiveOverlayMode.WEBSOCKET:
        resolved_handler = websocket.get("data_stream_handler")

    configured_protocol = mqtt.get("device_api_protocol", "https")
    if resolved_overlay is LiveOverlayMode.WEBSOCKET:
        configured_protocol = websocket.get("device_api_protocol", "https")

    return LiveStreamStartup(
        host=host or device.get("host", ""),
        username=username or device.get("username", ""),
        password=password or device.get("password", ""),
        camera_head=camera_head if camera_head is not None else integer_default(rtsp.get("camera_head", 1), 1),
        resolution=resolution or rtsp.get("resolution", "1280x720"),
        overlay_mode=resolved_overlay,
        handler_type=resolved_handler,
        mqtt_host=mqtt_host or mqtt.get("broker_host", ""),
        mqtt_port=mqtt_port if mqtt_port is not None else integer_default(mqtt.get("broker_port", 1883), 1883),
        mqtt_username=mqtt_username or mqtt.get("broker_username", ""),
        mqtt_password=mqtt_password or mqtt.get("broker_password", ""),
        analytics_data_source_key=analytics_data_source_key or mqtt.get("data_source_key", ""),
        device_api_protocol=device_api_protocol or configured_protocol,
        websocket_topic=websocket_topic or websocket.get("topic", ""),
        websocket_channel_id=(
            websocket_channel_id
            if websocket_channel_id is not None
            else integer_default(websocket.get("channel_id", 1), 1)
        ),
    )


def _run_app(
    log_level: str,
    config: Path | None,
    debug: bool,
    startup_content: StartupContent | None = None,
    open_catalog_viewer: bool = False,
) -> None:
    """Helper function to run the application with consistent error handling."""
    _require_existing_config_path(config)

    try:
        app = create_app(
            log_level=log_level,
            config_path=config,
            debug=debug,
            startup_content=startup_content,
            open_catalog_viewer=open_catalog_viewer,
        )

        exit_code = app.run()
        sys.exit(exit_code)

    except KeyboardInterrupt:
        click.echo("\nApplication interrupted by user", err=True)
        sys.exit(130)
    except Exception as e:
        click.echo(f"Application failed to start: {e}", err=True)
        if debug or log_level == "DEBUG":
            traceback.print_exc()
        sys.exit(1)


def _build_runtime_context(log_level: str, config: Path | None, debug: bool) -> dict[str, Any]:
    """Build shared runtime context for CLI commands."""
    return {
        "log_level": log_level,
        "config": config,
        "debug": debug,
        "run_with_startup_content": lambda startup_content: _run_app(
            log_level=log_level,
            config=config,
            debug=debug,
            startup_content=startup_content,
        ),
        "run_catalog_viewer": lambda: _run_app(
            log_level=log_level,
            config=config,
            debug=debug,
            open_catalog_viewer=True,
        ),
    }


@click.group(
    invoke_without_command=True,
    epilog=(
        "\b\n"
        "Documentation: https://github.com/RasmusRynell/ax-devil\n"
        "Agent guidance: https://github.com/RasmusRynell/ax-devil/blob/main/AGENTS.md\n"
        "Agent skills:   https://github.com/RasmusRynell/ax-devil/tree/main/.agents/skills"
    ),
)
@apply_run_options
@click.pass_context
def cli(
    ctx: click.Context,
    log_level: str,
    config: Path | None,
    debug: bool,
) -> None:
    """View video, live Axis cameras, and analytics overlays.

    Run without a command to open the workspace.
    """
    setup_logging(console_log_level="WARNING", console_only=True)
    ctx.obj = _build_runtime_context(log_level=log_level, config=config, debug=debug)
    if ctx.invoked_subcommand is None:
        _run_app(log_level, config, debug)


def _get_playlist_plugin_command(config: Path | None, resolver_id: str) -> click.Command:
    """Look up a resolver-provided playlist command after config has been applied."""
    _apply_cli_config_and_reload_plugins(config)

    try:
        record = RuntimePluginRegistry.get_plugin(PLAYLIST_RESOLVER_PLUGIN_TYPE, resolver_id)
    except ValueError as exc:
        available = [
            plugin.definition.plugin_id
            for plugin in RuntimePluginRegistry.get_plugins(PLAYLIST_RESOLVER_PLUGIN_TYPE)
            if plugin.status == PluginStatus.LOADED and plugin.plugin_class is not None
        ]
        available_list = ", ".join(sorted(available)) if available else "none"
        raise click.ClickException(
            f"Unknown playlist resolver '{resolver_id}'. Available resolvers: {available_list}."
        ) from exc

    plugin_class = record.plugin_class
    if record.status != PluginStatus.LOADED or plugin_class is None:
        raise click.ClickException(f"Playlist resolver '{resolver_id}' is not available.")
    if not issubclass(plugin_class, PlaylistResolverPlugin):
        raise click.ClickException(f"Playlist resolver '{resolver_id}' is invalid.")

    command = plugin_class.create_cli_command()
    if command is None:
        raise click.ClickException(f"Playlist resolver '{resolver_id}' does not provide a CLI command.")
    return command


def _list_available_playlist_resolvers(config: Path | None) -> list[str]:
    """Return sorted IDs of currently loadable playlist resolvers."""
    _apply_cli_config_and_reload_plugins(config)
    return sorted(
        plugin.definition.plugin_id
        for plugin in RuntimePluginRegistry.get_plugins(PLAYLIST_RESOLVER_PLUGIN_TYPE)
        if plugin.status == PluginStatus.LOADED and plugin.plugin_class is not None
    )


@cli.command(
    context_settings={
        "ignore_unknown_options": True,
        "allow_extra_args": True,
        "help_option_names": [],
    },
    add_help_option=False,
)
@click.option("--help", "show_help", is_flag=True, help="Show this message and resolver-specific help.")
@click.argument("resolver_id", required=False)
@click.pass_context
def playlist(ctx: click.Context, show_help: bool, resolver_id: str | None) -> None:
    """Open playlists using a resolver-provided CLI command."""
    config = ctx.obj.get("config") if isinstance(ctx.obj, dict) else None
    if config is not None and not isinstance(config, Path):
        config = None

    if show_help and resolver_id is None:
        click.echo(ctx.get_help())
        available = _list_available_playlist_resolvers(config)
        available_text = ", ".join(available) if available else "none"
        click.echo(f"\nAvailable resolvers: {available_text}")
        click.echo("Resolver help: ax-devil playlist <resolver_id> --help")
        return

    if resolver_id is None:
        raise click.UsageError("Missing argument 'RESOLVER_ID'.", ctx)

    command = _get_playlist_plugin_command(config, resolver_id)
    forwarded_args = list(ctx.args)
    if show_help:
        forwarded_args.append("--help")
    command.main(
        args=forwarded_args,
        prog_name=f"{ctx.command_path} {resolver_id}",
        obj=ctx.obj,
        standalone_mode=False,
    )


@cli.command()
@CONFIG_OPTION
@click.option("--force", is_flag=True, help="Skip confirmation prompts")
@click.option("--type", "cache_type", type=click.Choice(["main", "all"]), default="all", help="Cache type to clear")
def clear_cache(config: Path | None, force: bool, cache_type: str) -> None:
    """Clear application caches.

    This command will remove cached data used by ax-devil.
    You will be prompted for confirmation unless --force is used.
    """
    from ax_devil.modules.cache import CacheManager

    _require_existing_config_path(config)

    config_manager = ConfigManager()
    if config is not None:
        config_manager.set_config_path(config, create_if_missing=False)

    cache_manager = CacheManager()

    selected_cache_type = None if cache_type == "all" else cache_type

    def _message_for_scope(default: str, scoped: str | None = None) -> str:
        if selected_cache_type and scoped:
            return scoped.format(cache_type=selected_cache_type)
        return default

    stats_snapshot = cache_manager.get_cache_stats(selected_cache_type)
    cache_stats = stats_snapshot.all
    filtered_stats = stats_snapshot.scoped

    if not cache_stats or not filtered_stats:
        click.echo(
            _message_for_scope(
                "No cache directories found.",
                "No cache directories found for cache type '{cache_type}'.",
            )
        )
        return

    total_files, _ = cache_manager.aggregate_cache_stats(filtered_stats)
    if total_files == 0:
        click.echo(
            _message_for_scope(
                "No cached files found to clear.",
                "No cached files found for cache type '{cache_type}'.",
            )
        )
        return

    cache_summary = cache_manager.format_cache_summary(filtered_stats, include_total=selected_cache_type is None)

    click.echo("Cache locations that will be cleared:")
    click.echo(cache_summary)

    if not force:
        if not click.confirm("Are you sure you want to clear these caches?"):
            click.echo("Cache clearing cancelled.")
            return

    # Clear the caches
    success, message = cache_manager.clear_cache(
        cache_type=selected_cache_type,
        force=True,  # We've already handled confirmation
    )

    if success:
        click.echo(f"✓ {message}")
    else:
        click.echo(f"✗ {message}")


@cli.command("list-handlers")
def list_handlers() -> None:
    """List available decoder handler types for use with --handler-type."""
    from ax_devil.modules.plugin_system import get_file_decoder_definitions

    _apply_cli_config_and_reload_plugins(None)

    definitions = get_file_decoder_definitions()
    if not definitions:
        click.echo("No file overlay handlers found.")
        return

    click.echo("File overlay handlers:")
    for decoder in definitions:
        desc = f"  {decoder.handler_type:<30} {decoder.display_name}"
        if decoder.description:
            desc += f" — {decoder.description}"
        click.echo(desc)


@cli.command()
@LOG_LEVEL_OPTION
@CONFIG_OPTION
@DEBUG_OPTION
@click.option(
    "--video",
    type=click.Path(exists=True, path_type=Path, dir_okay=False),
    required=True,
    help="Path to a video file",
)
@click.option(
    "--overlay",
    type=click.Path(exists=True, path_type=Path, dir_okay=False),
    default=None,
    help="Path to an overlay file",
)
@click.option("--handler-type", default=None, help="Decoder handler type for the overlay (required with --overlay)")
@click.pass_context
def local(
    ctx: click.Context,
    log_level: str,
    config: Path | None,
    debug: bool,
    video: Path,
    overlay: Path | None,
    handler_type: str | None,
) -> None:
    """Open an offline video file with optional overlay.

    Examples:

    \b
      ax-devil local --video recording.mp4
      ax-devil local --video recording.mp4 --overlay data.jsonl --handler-type ADF_BETA_FRAME
    """
    from ax_devil.modules.workspace import VideoFileStartup

    effective_config = config or (ctx.obj.get("config") if isinstance(ctx.obj, dict) else None)

    if overlay and not handler_type:
        raise click.ClickException("--handler-type is required when --overlay is set.")

    startup = VideoFileStartup(
        video_path=video,
        overlay_path=overlay,
        handler_type=handler_type,
    )
    _run_app(log_level=log_level, config=effective_config, debug=debug, startup_content=startup)


@cli.command()
@LOG_LEVEL_OPTION
@CONFIG_OPTION
@DEBUG_OPTION
@click.option("--host", default=None, help="Device host (default: config/env)")
@click.option("--username", default=None, help="Device username (default: config/env)")
@click.option("--password", default=None, help="Device password (default: config/env)")
@click.option("--camera-head", type=int, default=None, help="Camera head number (default: config)")
@click.option("--resolution", default=None, help="Video resolution e.g. 1280x720 (default: config)")
@click.option(
    "--overlay",
    type=click.Choice(["none", "rtsp", "mqtt", "websocket"]),
    default=None,
    help="Overlay mode (default: config)",
)
@click.option("--handler-type", default=None, help="Decoder handler type for overlay")
@click.option("--mqtt-host", default=None, help="MQTT broker host (default: config/env)")
@click.option("--mqtt-port", type=int, default=None, help="MQTT broker port (default: config)")
@click.option("--mqtt-username", default=None, help="MQTT broker username (default: config/env)")
@click.option("--mqtt-password", default=None, help="MQTT broker password (default: config/env)")
@click.option(
    "--data-source",
    "analytics_data_source_key",
    default=None,
    help="Analytics data source (default: config)",
)
@click.option(
    "--device-api-protocol",
    type=click.Choice(["https", "http"]),
    default=None,
    help="Device API protocol (default: config)",
)
@click.option("--topic", "websocket_topic", default=None, help="DataHub WebSocket topic (default: config)")
@click.option(
    "--channel-id", "websocket_channel_id", type=int, default=None, help="DataHub topic instance ID (default: config)"
)
@click.pass_context
def live(
    ctx: click.Context,
    log_level: str,
    config: Path | None,
    debug: bool,
    host: str | None,
    username: str | None,
    password: str | None,
    camera_head: int | None,
    resolution: str | None,
    overlay: str | None,
    handler_type: str | None,
    mqtt_host: str | None,
    mqtt_port: int | None,
    mqtt_username: str | None,
    mqtt_password: str | None,
    analytics_data_source_key: str | None,
    device_api_protocol: str | None,
    websocket_topic: str | None,
    websocket_channel_id: int | None,
) -> None:
    """Open a live stream using config defaults.

    Reads device and stream settings from the configuration file and
    environment variables.  CLI options override individual values.

    Examples:

    \b
      ax-devil live
      ax-devil live --host 192.168.1.100
      ax-devil live --overlay rtsp --handler-type ONVIF_XML
      ax-devil live --overlay mqtt --handler-type ADF_BETA_FRAME
    ax-devil live --overlay websocket --topic com.axis.scene.frame.v1 --channel-id 1
    """
    setup_logging(console_log_level="WARNING", console_only=True)

    effective_config = config or (ctx.obj.get("config") if isinstance(ctx.obj, dict) else None)
    _apply_cli_config_and_reload_plugins(effective_config)
    try:
        startup = _live_startup_from_config(
            host=host,
            username=username,
            password=password,
            camera_head=camera_head,
            resolution=resolution,
            overlay=overlay,
            handler_type=handler_type,
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_username=mqtt_username,
            mqtt_password=mqtt_password,
            analytics_data_source_key=analytics_data_source_key,
            device_api_protocol=device_api_protocol,
            websocket_topic=websocket_topic,
            websocket_channel_id=websocket_channel_id,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if not startup.host:
        raise click.ClickException(
            "No device host configured. Set AX_DEVIL_TARGET_ADDR, use --host, or configure defaults.device.host."
        )

    if startup.overlay_mode.requires_handler and not startup.handler_type:
        raise click.ClickException("--handler-type is required when overlay mode is set.")

    if startup.overlay_mode is LiveOverlayMode.MQTT:
        if not startup.mqtt_host:
            raise click.ClickException(
                "No MQTT broker host configured. Set AX_DEVIL_MQTT_BROKER_ADDR, use --mqtt-host, "
                "or configure defaults.live_stream.analytics-mqtt.broker_host."
            )
        if not startup.analytics_data_source_key:
            raise click.ClickException(
                "No analytics data source configured. Use --data-source or configure it in the config file."
            )
    if startup.overlay_mode is LiveOverlayMode.WEBSOCKET:
        if not startup.websocket_topic:
            raise click.ClickException(
                "No DataHub WebSocket topic configured. Use --topic or configure "
                "defaults.live_stream.analytics-websocket.topic."
            )
        if startup.websocket_channel_id < 1:
            raise click.ClickException("--channel-id must be a positive integer.")

    _run_app(
        log_level=log_level,
        config=effective_config,
        debug=debug,
        startup_content=startup,
    )


cli.add_command(plugins)
cli.add_command(catalog)


if __name__ == "__main__":
    cli(prog_name="ax-devil")
