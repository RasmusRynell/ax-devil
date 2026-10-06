from __future__ import annotations

from ax_devil.modules.plugin_system import (
    DecoderPlugin,
    FileToSceneDecoderDefinition,
    PayloadToSceneDecoderDefinition,
)
from ax_devil.modules.scene.model import SCENE_MODEL_VERSION

from .provider import CVATSceneDataProvider

CVAT = "CVAT"


class CVATPlugin(DecoderPlugin):
    @classmethod
    def plugin_id(cls) -> str:
        return "axis-cvat"

    @classmethod
    def display_name(cls) -> str:
        return "CVAT"

    @classmethod
    def scene_model_version(cls) -> tuple[int, int]:
        return SCENE_MODEL_VERSION

    @classmethod
    def file_to_scene_decoders(cls) -> tuple[FileToSceneDecoderDefinition, ...]:
        return (
            FileToSceneDecoderDefinition(
                handler_type=CVAT,
                factory=CVATSceneDataProvider,
                display_name="CVAT",
                file_extensions=(".xml",),
            ),
        )

    @classmethod
    def payload_to_scene_decoders(cls) -> tuple[PayloadToSceneDecoderDefinition, ...]:
        return ()


PLUGIN_CLASS = CVATPlugin
