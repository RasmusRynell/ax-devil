"""Workspace intake backed by the decoder plugins installed in the application."""

from __future__ import annotations

from ax_devil.modules.plugin_system import (
    DECODER_PLUGIN_TYPE,
    DecoderPluginDefinition,
    RuntimePluginRegistry,
    get_file_decoder_definitions,
)
from ax_devil.modules.workspace.core.intake import WorkspaceDecoderOption, WorkspaceIntake


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
