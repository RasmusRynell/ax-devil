"""Plugin registry records for tests of the plugin catalog and the Plugins view."""

from __future__ import annotations

from unittest.mock import Mock

from ax_devil.modules.plugin_system import (
    DECODER_PLUGIN_TYPE,
    DecoderPluginDefinition,
    FileToSceneDecoderDefinition,
    PayloadToSceneDecoderDefinition,
    PluginRecord,
    PluginStatus,
)


def file_handler(handler_type: str, name: str, description: str | None = None) -> FileToSceneDecoderDefinition:
    """Return a file decoder definition whose decoder is never called."""
    return FileToSceneDecoderDefinition(handler_type, Mock(), name, description=description)


def payload_handler(handler_type: str, name: str) -> PayloadToSceneDecoderDefinition:
    """Return a payload decoder definition whose decoder is never called."""
    return PayloadToSceneDecoderDefinition(handler_type, Mock(), name)


def plugin_record(
    plugin_id: str,
    package: str | None,
    *,
    files: tuple[FileToSceneDecoderDefinition, ...] = (),
    payloads: tuple[PayloadToSceneDecoderDefinition, ...] = (),
    error: str | None = None,
) -> PluginRecord:
    """Return a decoder plugin record that loaded, or failed with *error*."""
    return PluginRecord(
        definition=DecoderPluginDefinition(
            plugin_type=DECODER_PLUGIN_TYPE,
            plugin_id=plugin_id,
            display_name=plugin_id,
            file_to_scene_decoders=files,
            payload_to_scene_decoders=payloads,
        ),
        package=package,
        status=PluginStatus.FAILED if error else PluginStatus.LOADED,
        error=error,
    )
