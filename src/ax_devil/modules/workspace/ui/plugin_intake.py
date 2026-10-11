"""Workspace intake and item resolution backed by the plugins installed in the application."""

from __future__ import annotations

from typing import cast

from ax_devil.modules.plugin_system import (
    DECODER_PLUGIN_TYPE,
    PLAYLIST_RESOLVER_PLUGIN_TYPE,
    DecoderPluginDefinition,
    PlaylistResolverPlugin,
    PluginStatus,
    RuntimePluginRegistry,
    get_file_decoder_definitions,
)
from ax_devil.modules.workspace.core.intake import WorkspaceDecoderOption, WorkspaceIntake
from ax_devil.modules.workspace.core.resolution import ItemResolutionError, PlaylistResolver


class PluginWorkspaceDecoderOptionProvider:
    """Adapt plugin-system decoder definitions into Workspace option records."""

    def file_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        """Return file decoder options discovered through the plugin host."""
        return tuple(
            WorkspaceDecoderOption(
                handler_type=decoder.handler_type,
                display_name=decoder.display_name,
                description=decoder.description or "",
                file_extensions=decoder.file_extensions,
            )
            for decoder in get_file_decoder_definitions()
        )

    def live_overlay_decoder_options(self) -> tuple[WorkspaceDecoderOption, ...]:
        """Return payload decoder options discovered through the plugin host."""
        options: list[WorkspaceDecoderOption] = []
        for record in RuntimePluginRegistry.get_plugins(DECODER_PLUGIN_TYPE):
            definition = record.definition
            if not isinstance(definition, DecoderPluginDefinition):
                continue
            options.extend(
                WorkspaceDecoderOption(
                    handler_type=decoder.handler_type,
                    display_name=decoder.display_name,
                    description=decoder.description or "",
                )
                for decoder in definition.payload_to_scene_decoders
            )
        return tuple(options)


def default_workspace_intake() -> WorkspaceIntake:
    """Return the production Workspace intake seam."""
    return WorkspaceIntake(PluginWorkspaceDecoderOptionProvider())


class PluginResolutionContext:
    """Resolve Workspace Items with the plugin-backed intake and the installed playlist resolver plugins."""

    def __init__(self, intake: WorkspaceIntake) -> None:
        self._intake = intake

    @property
    def intake(self) -> WorkspaceIntake:
        """Return the intake that validates selections and constructs video Content."""
        return self._intake

    def playlist_resolver(self, resolver_id: str) -> PlaylistResolver:
        """Return a loaded resolver plugin with *resolver_id*, or raise ``ItemResolutionError``."""
        try:
            record = RuntimePluginRegistry.get_plugin(PLAYLIST_RESOLVER_PLUGIN_TYPE, resolver_id)
        except ValueError as exc:
            raise ItemResolutionError(f"Playlist resolver '{resolver_id}' is not installed.") from exc
        plugin_class = cast(type[PlaylistResolverPlugin] | None, record.plugin_class)
        if record.status != PluginStatus.LOADED or plugin_class is None:
            raise ItemResolutionError(f"Playlist resolver '{resolver_id}' is not available.")
        return plugin_class()


def default_resolution_context() -> PluginResolutionContext:
    """Return the production resolution context."""
    return PluginResolutionContext(default_workspace_intake())
